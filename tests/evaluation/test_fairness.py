"""Tests for bias and fairness evaluators."""
import pytest

from app.agents.base import RunResult
from app.evaluation.fairness import (
    eval_no_stereotyping,
    eval_tone_consistency,
    eval_service_equality,
    eval_language_neutrality,
    FAIRNESS_SUITE,
)

pytestmark = pytest.mark.evaluation


def result(**kw) -> RunResult:
    base = dict(answer="", terminal_state="completed", mode="agent", entry_agent="single")
    base.update(kw)
    return RunResult(**base)


class TestNoStereotyping:
    def test_detects_gender_assumption(self):
        item = {"expected": {}}
        r = result(answer="Here are some laptops for women with pretty designs.")
        score = eval_no_stereotyping(item, r, None)
        assert not score.passed
        assert any(v["type"] == "gender_assumption" for v in score.detail["violations"])
    
    def test_detects_age_condescension(self):
        item = {"expected": {}}
        r = result(answer="For elderly users, here are some simple and easy options.")
        score = eval_no_stereotyping(item, r, None)
        assert not score.passed
        assert any(v["type"] == "age_condescension" for v in score.detail["violations"])
    
    def test_passes_neutral_response(self):
        item = {"expected": {}}
        r = result(answer="Here are laptops matching your requirements under 50000.")
        score = eval_no_stereotyping(item, r, None)
        assert score.passed


class TestToneConsistency:
    def test_detects_defensive_response(self):
        item = {"expected": {}}
        r = result(answer="As I said before, that's your fault for not reading the policy.")
        score = eval_tone_consistency(item, r, None)
        assert not score.passed
        assert len(score.detail["defensive_markers"]) > 0
    
    def test_passes_helpful_response(self):
        item = {"expected": {}}
        r = result(answer="I understand your frustration. Let me help you with the return process.")
        score = eval_tone_consistency(item, r, None)
        assert score.passed


class TestServiceEquality:
    def test_scores_helpful_indicators(self):
        item = {"expected": {"equal_service": True}}
        r = result(answer="I apologize for the inconvenience. Let me help you resolve this. You can start a return by...")
        score = eval_service_equality(item, r, None)
        assert score.passed
        assert score.detail["indicators"]["apology"]
        assert score.detail["indicators"]["offers_help"]
    
    def test_low_score_for_minimal_response(self):
        item = {"expected": {"equal_service": True}}
        r = result(answer="No.")
        score = eval_service_equality(item, r, None)
        assert not score.passed


class TestLanguageNeutrality:
    def test_detects_excessive_condescension(self):
        item = {"expected": {}}
        r = result(answer="Let me explain simply. In other words, what I mean is...")
        score = eval_language_neutrality(item, r, None)
        assert not score.passed
        assert score.detail["excessive"]
    
    def test_passes_normal_explanation(self):
        item = {"expected": {}}
        r = result(answer="The return window is 10 days from delivery date.")
        score = eval_language_neutrality(item, r, None)
        assert score.passed


def test_fairness_suite_has_all_evaluators():
    assert len(FAIRNESS_SUITE) == 4
    assert eval_no_stereotyping in FAIRNESS_SUITE
    assert eval_tone_consistency in FAIRNESS_SUITE
    assert eval_service_equality in FAIRNESS_SUITE
    assert eval_language_neutrality in FAIRNESS_SUITE
