"""LLM-as-judge, and the machinery for judging the judge.

A judge is a model in production. It gets its own evaluation:

  agreement        vs human labels on datasets/calibration (accuracy + Cohen's kappa)
  position bias    swap A/B in a pairwise comparison; the winner must not change
  self-consistency same input three times at temperature 0; same score
  length bias      does it reward verbosity independent of quality?

Judges score on a discrete rubric with written anchors (prompts/v1/judge_*.md),
never a vague 0-1 "quality" float, and they are NEVER used for safety pass/fail
-- safety verdicts come from deterministic classifiers.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from typing import Optional

from app.evaluation.evaluators import Score
from app.llm.base import CompletionRequest
from app.llm.router import LLMRouter
from app.prompts import get_prompt
from app.tracing import span

RUBRICS = {
    "groundedness": "judge_groundedness",
    "helpfulness": "judge_helpfulness",
}


@dataclass
class JudgeVerdict:
    rubric: str
    score: int
    rationale: str = ""
    span_text: str = ""
    raw: str = ""

    def normalised(self) -> float:
        """1-5 -> 0-1, so it can share a scale with the deterministic metrics."""
        return (self.score - 1) / 4.0


class Judge:
    def __init__(self, router: Optional[LLMRouter] = None, prompt_version: str = "v1"):
        self.router = router or LLMRouter(prompt_version=prompt_version)
        self.prompt_version = prompt_version

    async def score(
        self,
        rubric: str,
        answer: str,
        context: str = "",
        question: str = "",
        reference: str = "",
    ) -> JudgeVerdict:
        system = get_prompt(RUBRICS.get(rubric, "judge_helpfulness"), self.prompt_version)
        prompt = (
            f"QUESTION:\n{question}\n\n"
            f"CONTEXT:\n{context[:4000]}\n\n"
            f"ANSWER:\n{answer}\n"
        )
        with span("eval.judge", rubric=rubric) as sp:
            completion = await self.router.complete(
                CompletionRequest(
                    prompt=prompt,
                    system=system,
                    purpose="judge",
                    temperature=0.0,
                    meta={"answer": answer, "reference": reference or context, "rubric": rubric},
                )
            )
            verdict = _parse(rubric, completion.text)
            sp.attributes.update({"judge.score": verdict.score, "judge.model": self.router.model_name})
            return verdict

    async def self_consistency(self, rubric: str, answer: str, context: str, n: int = 3) -> dict:
        scores = []
        for _ in range(n):
            v = await self.score(rubric, answer, context)
            scores.append(v.score)
        return {
            "scores": scores,
            "consistent": len(set(scores)) == 1,
            "stdev": statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        }

    async def pairwise(self, question: str, answer_a: str, answer_b: str, context: str = "") -> dict:
        """Run both orderings. A judge whose winner changes when you swap the
        order is measuring position, not quality."""
        a_first = await self.score("helpfulness", answer_a, context, question)
        b_first = await self.score("helpfulness", answer_b, context, question)
        forward = "a" if a_first.score > b_first.score else ("b" if b_first.score > a_first.score else "tie")
        a_second = await self.score("helpfulness", answer_b, context, question)
        b_second = await self.score("helpfulness", answer_a, context, question)
        reverse = "b" if a_second.score > b_second.score else ("a" if b_second.score > a_second.score else "tie")
        return {
            "forward_winner": forward,
            "reverse_winner": reverse,
            "position_bias": forward != reverse and "tie" not in (forward, reverse),
            "scores": {"a": a_first.score, "b": b_first.score},
        }


def _parse(rubric: str, text: str) -> JudgeVerdict:
    raw = (text or "").strip()
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return JudgeVerdict(rubric=rubric, score=1, rationale="unparseable judge output", raw=raw)
    try:
        score = int(round(float(data.get("score", 1))))
    except (TypeError, ValueError):
        score = 1
    return JudgeVerdict(
        rubric=rubric,
        score=max(1, min(5, score)),
        rationale=str(data.get("rationale", ""))[:400],
        span_text=str(data.get("span", ""))[:400],
        raw=raw,
    )


# ----------------------------------------------------------------------
# judging the judge
# ----------------------------------------------------------------------

def cohens_kappa(a: list[int], b: list[int]) -> float:
    """Chance-corrected agreement. Plain accuracy flatters a judge on a skewed
    label distribution; kappa does not."""
    if not a or len(a) != len(b):
        return 0.0
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    labels = set(a) | set(b)
    expected = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


@dataclass
class Calibration:
    rubric: str
    n: int
    accuracy: float
    within_one: float
    kappa: float
    mean_judge: float
    mean_human: float
    length_bias_r: float = 0.0
    items: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "rubric": self.rubric,
            "n": self.n,
            "exact_accuracy": round(self.accuracy, 4),
            "within_one": round(self.within_one, 4),
            "cohens_kappa": round(self.kappa, 4),
            "mean_judge": round(self.mean_judge, 3),
            "mean_human": round(self.mean_human, 3),
            "length_bias_r": round(self.length_bias_r, 4),
        }


async def calibrate(judge: Judge, items: list[dict], rubric: str = "groundedness") -> Calibration:
    judged: list[int] = []
    human: list[int] = []
    lengths: list[int] = []
    rows = []
    for item in items:
        if item.get("rubric", rubric) != rubric:
            continue
        v = await judge.score(
            rubric,
            item["answer"],
            context=item.get("context", ""),
            question=item.get("question", ""),
            reference=item.get("reference", ""),
        )
        judged.append(v.score)
        human.append(int(item["human_score"]))
        lengths.append(len(item["answer"]))
        rows.append({"id": item.get("id"), "judge": v.score, "human": item["human_score"],
                     "rationale": v.rationale})
    n = len(judged)
    if n == 0:
        return Calibration(rubric, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    accuracy = sum(1 for x, y in zip(judged, human) if x == y) / n
    within_one = sum(1 for x, y in zip(judged, human) if abs(x - y) <= 1) / n
    return Calibration(
        rubric=rubric,
        n=n,
        accuracy=accuracy,
        within_one=within_one,
        kappa=cohens_kappa(judged, human),
        mean_judge=statistics.mean(judged),
        mean_human=statistics.mean(human),
        length_bias_r=_pearson(lengths, judged),
        items=rows,
    )


def _pearson(xs: list[int], ys: list[int]) -> float:
    if len(xs) < 2:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


async def judge_scores(judge: Judge, item: dict, result) -> list[Score]:
    """Judge evaluators in the same Score shape as the deterministic ones, so
    the engine treats them identically and the console renders them together."""
    chunks = (result.facts or {}).get("chunks") or []
    context = "\n\n".join(c.get("content", "") for c in chunks)
    question = (item.get("input") or {}).get("text", "")
    reference = (item.get("expected") or {}).get("answer", "")
    out: list[Score] = []
    g = await judge.score("groundedness", result.answer or "", context, question, reference)
    out.append(Score("judge_groundedness", g.normalised(), g.score >= 4,
                     {"score": g.score, "rationale": g.rationale}))
    h = await judge.score("helpfulness", result.answer or "", context, question, reference)
    out.append(Score("judge_helpfulness", h.normalised(), h.score >= 4,
                     {"score": h.score, "rationale": h.rationale}))
    return out
