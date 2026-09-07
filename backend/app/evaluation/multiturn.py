"""Multi-turn conversation evaluators.

Evaluates conversation quality across multiple turns:
- Context retention: Does the model remember previous context?
- Coreference resolution: Can it resolve pronouns (it, that, the first one)?
- Topic tracking: Does it handle topic switches correctly?
- Correction handling: Does it respect user corrections?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List

from app.evaluation.evaluators import Score
from app.tracing import Trace


@dataclass
class TurnResult:
    """Result from a single turn in a conversation."""
    turn_index: int
    user_text: str
    answer: str
    tools_used: list
    context_entities: list


def eval_context_retention(item: dict, results: List[TurnResult], traces: List[Trace]) -> Score:
    """Check if context from earlier turns is retained in later turns."""
    expected_retention = item.get("expected", {}).get("context_retention", [])
    if not expected_retention:
        return Score("context_retention", 1.0, True, {"skipped": "no retention requirements"})
    
    retained = []
    lost = []
    
    for entity in expected_retention:
        found_in_later = False
        for i, result in enumerate(results[1:], start=1):
            combined = f"{result.answer} {' '.join(result.tools_used)}"
            if entity.lower() in combined.lower():
                found_in_later = True
                retained.append({"entity": entity, "found_in_turn": i})
                break
        
        if not found_in_later:
            lost.append(entity)
    
    value = len(retained) / len(expected_retention) if expected_retention else 1.0
    passed = len(lost) == 0
    
    return Score("context_retention", value, passed, 
                 {"retained": retained, "lost": lost})


def eval_coreference_resolution(item: dict, results: List[TurnResult], traces: List[Trace]) -> Score:
    """Check if pronouns and references are correctly resolved."""
    pronouns_to_resolve = item.get("expected", {}).get("pronoun_resolved", [])
    if not pronouns_to_resolve:
        return Score("coreference_resolution", 1.0, True, {"skipped": "no pronouns to check"})
    
    resolved = []
    failed = []
    
    first_turn_entities = _extract_entities(results[0].answer if results else "")
    
    for pronoun in pronouns_to_resolve:
        for i, result in enumerate(results[1:], start=1):
            if pronoun.lower() in results[i-1].user_text.lower() if i > 0 else False:
                if any(entity in result.answer for entity in first_turn_entities):
                    resolved.append({"pronoun": pronoun, "turn": i})
                else:
                    failed.append({"pronoun": pronoun, "turn": i})
    
    total = len(pronouns_to_resolve)
    value = len(resolved) / total if total else 1.0
    passed = len(failed) == 0
    
    return Score("coreference_resolution", value, passed,
                 {"resolved": resolved, "failed": failed})


def eval_topic_continuity(item: dict, results: List[TurnResult], traces: List[Trace]) -> Score:
    """Check if topic switches are handled correctly."""
    topic_switch_expected = item.get("expected", {}).get("topic_switch_detected", False)
    
    if not results or len(results) < 2:
        return Score("topic_continuity", 1.0, True, {"skipped": "insufficient turns"})
    
    topic_changes = []
    for i in range(1, len(results)):
        prev_tools = set(results[i-1].tools_used)
        curr_tools = set(results[i].tools_used)
        
        if prev_tools and curr_tools and not prev_tools & curr_tools:
            topic_changes.append({"from_turn": i-1, "to_turn": i})
    
    detected_switch = len(topic_changes) > 0
    
    if topic_switch_expected:
        passed = detected_switch
    else:
        passed = True
    
    return Score("topic_continuity", 1.0 if passed else 0.0, passed,
                 {"topic_changes": topic_changes, "switch_expected": topic_switch_expected})


def eval_correction_handling(item: dict, results: List[TurnResult], traces: List[Trace]) -> Score:
    """Check if user corrections are acknowledged and applied."""
    correction_expected = item.get("expected", {}).get("correction_acknowledged", False)
    final_context = item.get("expected", {}).get("final_context", {})
    
    if not correction_expected:
        return Score("correction_handling", 1.0, True, {"skipped": "no correction expected"})
    
    correction_words = ["sorry", "meant", "actually", "no i meant", "correction", "wrong"]
    correction_turn = -1
    
    for i, result in enumerate(results):
        if any(word in result.user_text.lower() for word in correction_words):
            correction_turn = i
            break
    
    if correction_turn < 0:
        return Score("correction_handling", 0.0, False, {"error": "no correction found in turns"})
    
    issues = []
    if final_context:
        last_answer = results[-1].answer if results else ""
        for key, value in final_context.items():
            if str(value) not in last_answer:
                issues.append({"expected": f"{key}={value}", "not_in_final": True})
    
    passed = len(issues) == 0
    return Score("correction_handling", 1.0 if passed else 0.0, passed,
                 {"correction_turn": correction_turn, "issues": issues})


def eval_no_repeated_lookups(item: dict, results: List[TurnResult], traces: List[Trace]) -> Score:
    """Check that the model doesn't redundantly re-fetch the same data."""
    no_repeat_expected = item.get("expected", {}).get("no_repeated_lookups", False)
    
    if not no_repeat_expected:
        return Score("no_repeated_lookups", 1.0, True, {"skipped": "not checking repeats"})
    
    all_tool_calls = []
    for trace in traces:
        for span in trace.find("tool.call"):
            tool_name = span.attributes.get("tool.name")
            args = span.attributes.get("tool.arguments", {})
            all_tool_calls.append((tool_name, str(args)))
    
    seen = set()
    repeats = []
    for call in all_tool_calls:
        if call in seen:
            repeats.append(call)
        seen.add(call)
    
    passed = len(repeats) == 0
    return Score("no_repeated_lookups", 1.0 if passed else 0.0, passed,
                 {"repeats": [{"tool": r[0], "args": r[1]} for r in repeats]})


def _extract_entities(text: str) -> list:
    """Extract potential entities from text (order IDs, product names, etc.)."""
    import re
    entities = []
    
    entities.extend(re.findall(r"OR-\d+", text))
    entities.extend(re.findall(r"PRD-[A-Z]\d+", text))
    entities.extend(re.findall(r"[A-Z][a-z]+\s+\d+(?:\s+[A-Za-z]+)?", text))
    
    return entities


MULTITURN_SUITE = [
    eval_context_retention,
    eval_coreference_resolution,
    eval_topic_continuity,
    eval_correction_handling,
    eval_no_repeated_lookups,
]
