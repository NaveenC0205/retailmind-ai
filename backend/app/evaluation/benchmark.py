"""Model comparison benchmarking.

Compare different LLM providers (OpenAI, Gemini, Claude, etc.) on:
- Accuracy: Correct answers and tool usage
- Latency: Response time percentiles
- Cost: Token usage and pricing
- Safety: Refusal rate, injection resistance
- Reliability: Error rate, timeout rate

Usage:
    benchmark = ModelBenchmark(session)
    results = await benchmark.run(
        dataset="benchmark/model_comparison",
        models=["openai:gpt-4o-mini", "gemini:gemini-2.0-flash"]
    )
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

from app.agents.orchestrator import Orchestrator
from app.config import get_settings
from app.db import session_scope
from app.evaluation import datasets as ds_mod
from app.evaluation.evaluators import (
    eval_tool_selection, eval_task_completion, eval_answer_contains,
    eval_attack_blocked, eval_no_cross_customer_leak
)
from app.llm.router import LLMRouter, build_provider
from app.security import customer
from app.tracing import new_trace, set_trace


@dataclass
class ModelMetrics:
    """Metrics for a single model."""
    model_name: str
    provider: str
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    timeouts: int = 0
    
    accuracy_score: float = 0.0
    tool_accuracy: float = 0.0
    safety_score: float = 0.0
    
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_inr: float = 0.0
    avg_cost_per_request: float = 0.0
    
    latencies: List[int] = field(default_factory=list)
    scores_by_category: Dict[str, float] = field(default_factory=dict)
    
    def compute_percentiles(self):
        if not self.latencies:
            return
        sorted_lat = sorted(self.latencies)
        n = len(sorted_lat)
        self.latency_p50_ms = sorted_lat[int(n * 0.5)]
        self.latency_p95_ms = sorted_lat[int(n * 0.95)]
        self.latency_p99_ms = sorted_lat[min(int(n * 0.99), n - 1)]
    
    def to_dict(self) -> dict:
        return {
            "model": self.model_name,
            "provider": self.provider,
            "requests": {
                "total": self.total_requests,
                "successful": self.successful,
                "failed": self.failed,
                "timeouts": self.timeouts,
                "success_rate": round(self.successful / max(1, self.total_requests), 3)
            },
            "accuracy": {
                "overall": round(self.accuracy_score, 3),
                "tool_accuracy": round(self.tool_accuracy, 3),
                "safety": round(self.safety_score, 3)
            },
            "latency_ms": {
                "p50": self.latency_p50_ms,
                "p95": self.latency_p95_ms,
                "p99": self.latency_p99_ms
            },
            "cost": {
                "total_inr": round(self.total_cost_inr, 4),
                "avg_per_request": round(self.avg_cost_per_request, 4),
                "tokens_in": self.total_tokens_in,
                "tokens_out": self.total_tokens_out
            },
            "by_category": self.scores_by_category
        }


@dataclass
class BenchmarkResult:
    """Complete benchmark comparison result."""
    dataset_name: str
    started_at: datetime
    completed_at: datetime
    models: List[ModelMetrics]
    winner_accuracy: str
    winner_latency: str
    winner_cost: str
    
    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset_name,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_s": (self.completed_at - self.started_at).total_seconds(),
            "models": [m.to_dict() for m in self.models],
            "winners": {
                "accuracy": self.winner_accuracy,
                "latency": self.winner_latency,
                "cost": self.winner_cost
            },
            "comparison_table": self._make_comparison_table()
        }
    
    def _make_comparison_table(self) -> List[dict]:
        """Create a comparison table for easy viewing."""
        return [
            {
                "model": m.model_name,
                "accuracy": f"{m.accuracy_score:.1%}",
                "p95_latency": f"{m.latency_p95_ms}ms",
                "cost_per_req": f"₹{m.avg_cost_per_request:.4f}",
                "safety": f"{m.safety_score:.1%}"
            }
            for m in sorted(self.models, key=lambda x: -x.accuracy_score)
        ]


class ModelBenchmark:
    """Run benchmarks comparing multiple LLM providers."""
    
    def __init__(self, session_factory):
        self.session_factory = session_factory
    
    async def run(
        self,
        dataset_name: str = "benchmark/model_comparison",
        models: List[str] = None,
        concurrency: int = 2
    ) -> BenchmarkResult:
        """
        Run benchmark on multiple models.
        
        Args:
            dataset_name: Name of benchmark dataset
            models: List of "provider:model" strings, e.g. ["openai:gpt-4o-mini"]
            concurrency: Max concurrent requests per model
        """
        if models is None:
            s = get_settings()
            models = [f"{s.llm_provider}:{s.openai_model}"]
        
        dataset = ds_mod.load(dataset_name)
        started_at = datetime.utcnow()
        
        results = []
        for model_spec in models:
            provider, model = self._parse_model_spec(model_spec)
            metrics = await self._benchmark_model(
                dataset, provider, model, concurrency
            )
            results.append(metrics)
        
        completed_at = datetime.utcnow()
        
        return BenchmarkResult(
            dataset_name=dataset_name,
            started_at=started_at,
            completed_at=completed_at,
            models=results,
            winner_accuracy=max(results, key=lambda m: m.accuracy_score).model_name,
            winner_latency=min(results, key=lambda m: m.latency_p95_ms or float('inf')).model_name,
            winner_cost=min(results, key=lambda m: m.avg_cost_per_request or float('inf')).model_name
        )
    
    def _parse_model_spec(self, spec: str) -> tuple[str, str]:
        """Parse 'provider:model' format."""
        if ":" in spec:
            provider, model = spec.split(":", 1)
        else:
            provider = spec
            model = None
        return provider, model
    
    async def _benchmark_model(
        self,
        dataset,
        provider: str,
        model: Optional[str],
        concurrency: int
    ) -> ModelMetrics:
        """Run benchmark for a single model."""
        metrics = ModelMetrics(
            model_name=model or provider,
            provider=provider
        )
        
        semaphore = asyncio.Semaphore(concurrency)
        
        async def run_item(item):
            async with semaphore:
                return await self._run_single_item(item, provider, model, metrics)
        
        tasks = [run_item(item) for item in dataset.items]
        item_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        accuracy_scores = []
        tool_scores = []
        safety_scores = []
        category_scores = {}
        
        for item, result in zip(dataset.items, item_results):
            if isinstance(result, Exception):
                metrics.failed += 1
                continue
            
            run_result, trace, scores = result
            
            for score in scores:
                if score.name == "answer_contains" or score.name == "task_completion":
                    accuracy_scores.append(score.value)
                elif score.name == "tool_selection":
                    tool_scores.append(score.value)
                elif score.name in ("attack_blocked", "no_cross_customer_leak"):
                    safety_scores.append(score.value)
            
            category = item.get("category", "unknown")
            if category not in category_scores:
                category_scores[category] = []
            category_scores[category].append(
                sum(s.value for s in scores) / max(1, len(scores))
            )
        
        metrics.accuracy_score = sum(accuracy_scores) / max(1, len(accuracy_scores))
        metrics.tool_accuracy = sum(tool_scores) / max(1, len(tool_scores))
        metrics.safety_score = sum(safety_scores) / max(1, len(safety_scores))
        metrics.avg_cost_per_request = metrics.total_cost_inr / max(1, metrics.total_requests)
        
        metrics.scores_by_category = {
            cat: sum(scores) / len(scores) 
            for cat, scores in category_scores.items()
        }
        
        metrics.compute_percentiles()
        
        return metrics
    
    async def _run_single_item(
        self,
        item: dict,
        provider: str,
        model: Optional[str],
        metrics: ModelMetrics
    ):
        """Run a single benchmark item."""
        metrics.total_requests += 1
        
        trace = new_trace()
        principal_str = item.get("input", {}).get("principal", "customer:CU-1001")
        _, customer_id = principal_str.split(":", 1)
        mode = item.get("input", {}).get("mode", "agent")
        text = item.get("input", {}).get("text", "")
        
        llm = build_provider(provider, model)
        router = LLMRouter(provider=llm, prompt_version="v1")
        
        try:
            start = time.perf_counter()
            async with self.session_factory() as session:
                orch = Orchestrator(
                    session, 
                    customer(customer_id), 
                    router=router,
                    persist=False
                )
                result = await orch.run(text, mode=mode)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            
            metrics.successful += 1
            metrics.latencies.append(elapsed_ms)
            metrics.total_tokens_in += result.tokens_in
            metrics.total_tokens_out += result.tokens_out
            metrics.total_cost_inr += result.cost_inr
            
            scores = self._evaluate_result(item, result, trace)
            
            return result, trace, scores
            
        except asyncio.TimeoutError:
            metrics.timeouts += 1
            raise
        except Exception:
            metrics.failed += 1
            raise
        finally:
            set_trace(None)
    
    def _evaluate_result(self, item: dict, result, trace) -> list:
        """Run evaluators on the result."""
        scores = []
        
        scores.append(eval_task_completion(item, result, trace))
        scores.append(eval_answer_contains(item, result, trace))
        
        if item.get("expected", {}).get("tools"):
            scores.append(eval_tool_selection(item, result, trace))
        
        if item.get("expected", {}).get("should_refuse"):
            scores.append(eval_attack_blocked(item, result, trace))
            scores.append(eval_no_cross_customer_leak(item, result, trace))
        
        return scores


async def compare_models(
    models: List[str],
    dataset: str = "benchmark/model_comparison"
) -> dict:
    """Convenience function to compare models."""
    benchmark = ModelBenchmark(session_scope)
    result = await benchmark.run(dataset_name=dataset, models=models)
    return result.to_dict()
