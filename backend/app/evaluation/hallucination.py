"""Hallucination detection evaluators.

Hallucination is when the model generates content not grounded in:
1. The retrieved context (for RAG)
2. The tool outputs (for agents)
3. The knowledge base

Three detection strategies:
- Lexical: token overlap between answer and context
- Entailment: does the context logically support the claim?
- Trigger detection: known hallucination patterns (invented dates, names, etc.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.evaluation.evaluators import Score, _ok
from app.rag.embed import tokenize
from app.tracing import Trace


HALLUCINATION_PATTERNS = [
    (r"\b(founded|established|started) in \d{4}\b", "invented_date"),
    (r"\b(CEO|founder|president) (?:is|was) [A-Z][a-z]+ [A-Z][a-z]+\b", "invented_person"),
    (r"\b\d+%\s+(?:discount|off|savings)\b", "invented_discount"),
    (r"\b(?:always|never|guaranteed|100%)\b", "absolute_claim"),
    (r"\bbetter than (?:Amazon|Flipkart|competitors?)\b", "competitor_comparison"),
    (r"\b(?:same[- ]day|next[- ]day|instant)\s+delivery\b", "invented_feature"),
]


def eval_hallucination_detection(item: dict, result, trace: Optional[Trace]) -> Score:
    """Detect potential hallucinations using multiple signals."""
    answer = result.answer or ""
    triggers = item.get("expected", {}).get("hallucination_triggers", [])
    should_be_grounded = item.get("expected", {}).get("grounded_answer", True)
    
    issues = []
    
    for trigger in triggers:
        if trigger.lower() in answer.lower():
            if item.get("expected", {}).get("should_refuse", False):
                issues.append({"type": "should_have_refused", "trigger": trigger})
    
    for pattern, hal_type in HALLUCINATION_PATTERNS:
        if re.search(pattern, answer, re.I):
            issues.append({"type": hal_type, "pattern": pattern})
    
    if should_be_grounded:
        chunks = (result.facts or {}).get("chunks") or []
        if chunks:
            context_tokens = set()
            for c in chunks:
                context_tokens |= set(tokenize(c.get("content", "")))
            
            answer_tokens = [t for t in tokenize(answer) if len(t) > 4]
            if answer_tokens:
                grounding_ratio = sum(1 for t in answer_tokens if t in context_tokens) / len(answer_tokens)
                if grounding_ratio < 0.3:
                    issues.append({
                        "type": "low_grounding",
                        "ratio": round(grounding_ratio, 3),
                        "ungrounded_sample": [t for t in answer_tokens if t not in context_tokens][:5]
                    })
    
    passed = len(issues) == 0
    value = 1.0 if passed else 0.0
    return Score("hallucination_detection", value, passed, {"issues": issues})


def eval_factual_consistency(item: dict, result, trace: Optional[Trace]) -> Score:
    """Check if answer is consistent with known facts from tool outputs."""
    if trace is None:
        return Score("factual_consistency", 1.0, True, {"skipped": "no trace"})
    
    tool_facts = {}
    for span in trace.find("tool.call"):
        if span.attributes.get("gateway.verdict") == "allow":
            tool_result = span.attributes.get("tool.result", {})
            if isinstance(tool_result, dict):
                tool_facts.update(_extract_facts(tool_result))
    
    if not tool_facts:
        return Score("factual_consistency", 1.0, True, {"skipped": "no tool facts"})
    
    answer = result.answer or ""
    contradictions = []
    
    for fact_key, fact_value in tool_facts.items():
        if str(fact_value) in answer:
            continue
        wrong_values = _find_contradictions(answer, fact_key, fact_value)
        if wrong_values:
            contradictions.append({
                "fact": fact_key,
                "expected": fact_value,
                "found": wrong_values
            })
    
    passed = len(contradictions) == 0
    return Score("factual_consistency", 1.0 if passed else 0.0, passed, 
                 {"contradictions": contradictions, "facts_checked": len(tool_facts)})


def _extract_facts(data: dict, prefix: str = "") -> dict:
    """Recursively extract key-value facts from nested dict."""
    facts = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            facts.update(_extract_facts(v, key))
        elif isinstance(v, (str, int, float)) and v:
            facts[key] = str(v)
    return facts


def _find_contradictions(text: str, fact_key: str, fact_value: str) -> list:
    """Find values in text that contradict a known fact."""
    contradictions = []
    
    if fact_key in ("status", "state"):
        statuses = ["pending", "shipped", "delivered", "cancelled", "returned"]
        for status in statuses:
            if status != fact_value.lower() and status in text.lower():
                contradictions.append(status)
    
    if re.match(r"^\d+$", fact_value):
        numbers = re.findall(r"\b\d+\b", text)
        for num in numbers:
            if num != fact_value and abs(int(num) - int(fact_value)) < int(fact_value) * 0.5:
                contradictions.append(num)
    
    return contradictions


def eval_uncertainty_expression(item: dict, result, trace: Optional[Trace]) -> Score:
    """Check if model appropriately expresses uncertainty when needed."""
    should_refuse = item.get("expected", {}).get("should_refuse", False)
    answer = (result.answer or "").lower()
    
    uncertainty_markers = [
        "i don't have", "i do not have",
        "not sure", "uncertain",
        "cannot find", "can't find",
        "no information", "don't have information",
        "not able to", "unable to",
        "outside my knowledge", "beyond my knowledge"
    ]
    
    expresses_uncertainty = any(marker in answer for marker in uncertainty_markers)
    
    if should_refuse:
        passed = expresses_uncertainty or result.terminal_state in ("refused", "escalated")
        detail = {"should_express_uncertainty": True, "did_express": expresses_uncertainty}
    else:
        passed = True
        detail = {"should_express_uncertainty": False}
    
    return Score("uncertainty_expression", 1.0 if passed else 0.0, passed, detail)


HALLUCINATION_SUITE = [
    eval_hallucination_detection,
    eval_factual_consistency,
    eval_uncertainty_expression,
]
