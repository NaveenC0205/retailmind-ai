"""Judging the judge.

An LLM judge is a model in production and needs its own evaluation. These
tests exercise the calibration harness; with the mock judge the absolute
numbers are not meaningful, but the harness, the rubric plumbing, the
position-bias check and the agreement statistics all are.
"""
import pytest

from app.evaluation import datasets as ds_mod
from app.evaluation.judge import Judge, calibrate, cohens_kappa
from app.llm.mock import MockLLM
from app.llm.router import LLMRouter
from app.tracing import new_trace, set_trace

pytestmark = pytest.mark.evaluation


@pytest.fixture
def judge():
    return Judge(LLMRouter(provider=MockLLM(), prompt_version="v1"))


def test_kappa_is_chance_corrected():
    """Plain accuracy flatters a judge on a skewed label distribution."""
    agree = [5, 5, 5, 1, 1]
    assert cohens_kappa(agree, agree) == 1.0
    # Both raters say 5 almost always: high raw agreement, near-zero kappa.
    a = [5] * 9 + [1]
    b = [5] * 9 + [5]
    assert cohens_kappa(a, b) < 0.5


async def test_judge_returns_a_bounded_rubric_score(judge):
    new_trace()
    v = await judge.score("groundedness", "Electronics may be returned within 10 days.",
                          context="Electronics may be returned within 10 days of delivery.")
    set_trace(None)
    assert 1 <= v.score <= 5
    assert 0.0 <= v.normalised() <= 1.0


async def test_judge_scores_a_supported_answer_above_an_invented_one(judge):
    new_trace()
    context = "Electronics may be returned within 10 days of delivery."
    good = await judge.score("groundedness", context, context=context)
    bad = await judge.score("groundedness", "You can return anything within a year.",
                            context=context)
    set_trace(None)
    assert good.score > bad.score


async def test_judge_is_self_consistent_at_temperature_zero(judge):
    new_trace()
    out = await judge.self_consistency(
        "groundedness", "Electronics: 10 days.",
        "Electronics may be returned within 10 days of delivery.",
    )
    set_trace(None)
    assert out["consistent"], out


async def test_pairwise_comparison_reports_position_bias(judge):
    new_trace()
    out = await judge.pairwise(
        "What is the return window?",
        "Electronics may be returned within 10 days of delivery.",
        "Probably a couple of weeks, I think.",
        context="Electronics may be returned within 10 days of delivery.",
    )
    set_trace(None)
    assert out["forward_winner"] in ("a", "b", "tie")
    assert isinstance(out["position_bias"], bool)


async def test_calibration_reports_agreement_against_human_labels(judge):
    new_trace()
    items = ds_mod.load("calibration/judge_groundedness").items
    cal = await calibrate(judge, items, rubric="groundedness")
    set_trace(None)
    summary = cal.summary()
    assert summary["n"] == len(items)
    assert 0.0 <= summary["within_one"] <= 1.0
    assert -1.0 <= summary["cohens_kappa"] <= 1.0
    # Length bias: a judge that simply rewards longer answers is not judging.
    assert abs(summary["length_bias_r"]) <= 1.0


async def test_an_unparseable_judge_response_scores_lowest_rather_than_crashing():
    from app.evaluation.judge import _parse

    v = _parse("groundedness", "I think it's pretty good honestly")
    assert v.score == 1
    assert "unparseable" in v.rationale
