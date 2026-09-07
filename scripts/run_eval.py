#!/usr/bin/env python3
"""Run an evaluation and report gates.

    python scripts/run_eval.py rag/policy_qa
    python scripts/run_eval.py agents/task_trajectories --suite agents
    python scripts/run_eval.py adversarial/attack_corpus --suite safety --provider mock-naive
    python scripts/run_eval.py golden/regression --prompt-version v2 --compare v1

Exit code is 0 when the merge gates pass, 1 when they do not. That is what CI
consumes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import create_all, dispose, session_scope  # noqa: E402
from app.evaluation import datasets as ds_mod  # noqa: E402
from app.evaluation.engine import EvaluationEngine  # noqa: E402
from app.evaluation.gates import paired_diff_ci  # noqa: E402


async def run_one(args, prompt_version: str):
    engine = EvaluationEngine(
        session_scope,
        prompt_version=prompt_version,
        provider=args.provider,
        model=args.model,
        execution_tier="mocked" if args.provider.startswith("mock") else "live",
        use_judge=args.judge,
        persist=not args.no_persist,
    )
    dataset = ds_mod.load(args.dataset)
    problems = ds_mod.validate(dataset)
    if problems:
        print("dataset problems:")
        for p in problems[:10]:
            print("  -", p)
    return await engine.run(dataset, suite=args.suite, concurrency=args.concurrency)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("--suite", default=None)
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--prompt-version", default="v1")
    ap.add_argument("--compare", default=None, help="baseline prompt version for a paired diff")
    ap.add_argument("--metric", default="task_completion", help="metric for the paired diff")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    await create_all()
    report = await run_one(args, args.prompt_version)

    if args.json:
        print(json.dumps(
            {"summary": report.summary(),
             "gates": report.gate_report.as_dict() if report.gate_report else None},
            indent=2))
    else:
        print(report.render())
        print()
        print("per category (" + args.metric + "):")
        for k, v in report.per_category(args.metric).items():
            print(f"  {k:<28} {v:.3f}")
        print()
        if report.gate_report:
            print(report.gate_report.render())

    exit_code = 0 if (report.gate_report is None or report.gate_report.passed("merge")) else 1

    if args.compare:
        baseline = await run_one(args, args.compare)
        diff = paired_diff_ci(report.per_item(args.metric), baseline.per_item(args.metric))
        print()
        print(f"paired comparison on '{args.metric}': "
              f"{args.prompt_version} vs {args.compare}")
        print(f"  n              {diff['n']}")
        print(f"  mean diff      {diff['mean_diff']:+.4f}")
        print(f"  95% CI         [{diff['ci_low']:+.4f}, {diff['ci_high']:+.4f}]")
        print(f"  significant    {diff['significant']}  ({diff['direction']})")
        if not diff["significant"]:
            print("  -> the interval includes zero; this is noise, not a regression.")
        if diff["regressed_items"]:
            print("  regressed items:", ", ".join(diff["regressed_items"][:6]))

    await dispose()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
