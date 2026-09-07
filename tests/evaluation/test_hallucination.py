"""Tests for hallucination detection evaluators."""
import pytest

from app.agents.base import RunResult
from app.evaluation.hallucination import (
    eval_hallucination_detection,
    eval_factual_consistency,
    eval_uncertainty_expression,
    HALLUCINATION_SUITE,
)

pytestmark = pytest.mark.evaluation


def result(**kw) -> RunResult:
    base = dict(answer="", terminal_state="completed", mode="agent", entry_agent="single")
    base.update(kw)
    return RunResult(**base)


class TestHallucinationDetection:
    def test_detects_invented_date(self):
        item = {"expected": {"grounded_answer": True}}
        r = result(answer="Our company was founded in 2015 by John Smith.")
        score = eval_hallucination_detection(item, r, None)
        assert not score.passed
        assert any(i["type"] == "invented_date" for i in score.detail["issues"])
    
    def test_detects_invented_person(self):
        item = {"expected": {"grounded_answer": True}}
        r = result(answer="The CEO is Rajesh Kumar who has been leading since 2018.")
        score = eval_hallucination_detection(item, r, None)
        assert not score.passed
        assert any(i["type"] == "invented_person" for i in score.detail["issues"])
    
    def test_detects_absolute_claims(self):
        item = {"expected": {"grounded_answer": True}}
        r = result(answer="We always deliver on time, guaranteed!")
        score = eval_hallucination_detection(item, r, None)
        assert not score.passed
        assert any(i["type"] == "absolute_claim" for i in score.detail["issues"])
    
    def test_passes_grounded_answer(self):
        item = {"expected": {"grounded_answer": True}}
        r = result(
            answer="The return window is 10 days for electronics.",
            facts={"chunks": [{"id": "c1", "content": "Electronics return window is 10 days from delivery."}]}
        )
        score = eval_hallucination_detection(item, r, None)
        assert score.passed
    
    def test_detects_hallucination_trigger(self):
        item = {"expected": {"hallucination_triggers": ["washing machine"], "should_refuse": True}}
        r = result(answer="The warranty on washing machines is 2 years.")
        score = eval_hallucination_detection(item, r, None)
        assert not score.passed
        assert any(i["type"] == "should_have_refused" for i in score.detail["issues"])


class TestUncertaintyExpression:
    def test_expresses_uncertainty_when_needed(self):
        item = {"expected": {"should_refuse": True}}
        r = result(answer="I don't have information about that policy.")
        score = eval_uncertainty_expression(item, r, None)
        assert score.passed
    
    def test_fails_when_confident_but_should_refuse(self):
        item = {"expected": {"should_refuse": True}}
        r = result(answer="Yes, the warranty covers everything!")
        score = eval_uncertainty_expression(item, r, None)
        assert not score.passed
    
    def test_passes_when_answering_correctly(self):
        item = {"expected": {"should_refuse": False}}
        r = result(answer="The return policy is 30 days for apparel.")
        score = eval_uncertainty_expression(item, r, None)
        assert score.passed


def test_hallucination_suite_has_all_evaluators():
    assert len(HALLUCINATION_SUITE) == 3
    assert eval_hallucination_detection in HALLUCINATION_SUITE
    assert eval_factual_consistency in HALLUCINATION_SUITE
    assert eval_uncertainty_expression in HALLUCINATION_SUITE
