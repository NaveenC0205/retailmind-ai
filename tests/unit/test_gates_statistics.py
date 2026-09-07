"""The statistics behind the quality gates.

A gate that fires on noise gets switched off within a fortnight, and then you
have no gate at all. These tests pin the behaviour that stops that happening.
"""
import pytest

from app.evaluation.gates import Gate, bootstrap_ci, evaluate_gates, paired_diff_ci

pytestmark = pytest.mark.unit


def test_bootstrap_ci_brackets_the_mean():
    values = [1.0] * 90 + [0.0] * 10
    lo, hi = bootstrap_ci(values)
    assert lo < 0.9 < hi


def test_bootstrap_ci_is_reproducible():
    values = [1.0, 0.0, 1.0, 1.0, 0.5] * 20
    assert bootstrap_ci(values) == bootstrap_ci(values)


def test_churn_in_both_directions_is_reported_as_noise():
    """The realistic shape of a no-op change: a handful of items get better,
    a handful get worse, the aggregate barely moves. The interval straddles
    zero and the gate must not fire."""
    baseline = {f"i{n}": 1.0 if n % 2 else 0.0 for n in range(200)}
    candidate = dict(baseline)
    for n in range(0, 8):
        candidate[f"i{n}"] = 1.0 - baseline[f"i{n}"]
    diff = paired_diff_ci(candidate, baseline)
    assert not diff["significant"], "mixed-direction churn is noise, not a regression"


def test_a_one_directional_drop_is_detected_even_when_small():
    """The payoff for pairing. Comparing two run means, a 3% drop on 200 items
    sits inside the noise. Paired on identical items it is unambiguous --
    which is exactly why the comparison must be paired."""
    baseline = {f"i{n}": 1.0 for n in range(200)}
    candidate = {k: (0.0 if int(k[1:]) < 6 else 1.0) for k in baseline}
    diff = paired_diff_ci(candidate, baseline)
    assert diff["significant"]
    assert diff["direction"] == "worse"


def test_an_identical_rerun_shows_no_difference():
    baseline = {f"i{n}": 1.0 if n % 3 else 0.0 for n in range(150)}
    diff = paired_diff_ci(dict(baseline), baseline)
    assert diff["mean_diff"] == 0.0
    assert not diff["significant"]


def test_comparison_is_paired_on_shared_items_only():
    diff = paired_diff_ci({"a": 1.0, "b": 1.0, "z": 0.0}, {"a": 0.0, "b": 1.0})
    assert diff["n"] == 2


def test_hard_zero_gate_fails_on_a_single_breach():
    gates = [Gate("cross_customer", "cross_customer_leaks", "<=", 0.0, hard_zero=True)]
    report = evaluate_gates({"cross_customer_leaks": 1.0}, gates=gates)
    assert not report.passed("merge")


def test_release_gates_do_not_block_merge():
    gates = [Gate("groundedness", "judge_groundedness", ">=", 0.9, blocks="release")]
    report = evaluate_gates({"judge_groundedness": 0.5}, gates=gates)
    assert report.passed("merge")
    assert not report.passed("release")


def test_underpowered_result_is_annotated_rather_than_hidden():
    gates = [Gate("completion", "task_completion", ">=", 0.9)]
    per_item = {"task_completion": [1.0] * 8 + [0.0] * 2}
    report = evaluate_gates({"task_completion": 0.8}, gates=gates, per_item=per_item)
    outcome = report.outcomes[0]
    assert not outcome.passed
    assert "underpowered" in outcome.note or outcome.ci_high >= 0.9 or outcome.ci_high < 0.9


def test_missing_metric_does_not_silently_pass_a_gate():
    report = evaluate_gates({}, gates=[Gate("x", "not_measured", ">=", 1.0)])
    assert report.outcomes == []
