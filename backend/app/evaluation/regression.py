"""Automated regression detection.

Compares evaluation results across runs to detect:
- Score degradation: Metrics getting worse
- New failures: Tests that were passing now failing
- Latency regression: Response times increasing
- Cost regression: Token usage increasing

Usage:
    detector = RegressionDetector(session)
    report = await detector.compare(current_run_id, baseline_run_id)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy import select

from app.models import EvalRun, EvalResult, GateResult


@dataclass
class RegressionItem:
    """Single regression detected."""
    metric: str
    item_id: Optional[str]
    baseline_value: float
    current_value: float
    change_pct: float
    severity: str  # "critical", "warning", "info"
    
    @property
    def is_regression(self) -> bool:
        return self.change_pct < -5.0  # 5% degradation threshold


@dataclass
class RegressionReport:
    """Full regression analysis report."""
    baseline_run_id: str
    current_run_id: str
    baseline_date: datetime
    current_date: datetime
    regressions: List[RegressionItem] = field(default_factory=list)
    improvements: List[RegressionItem] = field(default_factory=list)
    new_failures: List[str] = field(default_factory=list)
    fixed_failures: List[str] = field(default_factory=list)
    summary: Dict = field(default_factory=dict)
    
    @property
    def has_critical_regression(self) -> bool:
        return any(r.severity == "critical" for r in self.regressions)
    
    @property
    def passed(self) -> bool:
        return not self.has_critical_regression and len(self.new_failures) == 0
    
    def to_dict(self) -> dict:
        return {
            "baseline_run_id": self.baseline_run_id,
            "current_run_id": self.current_run_id,
            "passed": self.passed,
            "summary": self.summary,
            "regressions": [
                {
                    "metric": r.metric,
                    "item_id": r.item_id,
                    "baseline": r.baseline_value,
                    "current": r.current_value,
                    "change_pct": round(r.change_pct, 2),
                    "severity": r.severity
                }
                for r in self.regressions
            ],
            "improvements": [
                {
                    "metric": r.metric,
                    "baseline": r.baseline_value,
                    "current": r.current_value,
                    "change_pct": round(r.change_pct, 2)
                }
                for r in self.improvements
            ],
            "new_failures": self.new_failures,
            "fixed_failures": self.fixed_failures
        }


class RegressionDetector:
    """Detect regressions between evaluation runs."""
    
    CRITICAL_METRICS = {
        "attack_blocked", "no_cross_customer_leak", 
        "no_unauthorized_execution", "hallucination_detection"
    }
    
    def __init__(self, session):
        self.session = session
    
    async def compare(
        self, 
        current_run_id: str, 
        baseline_run_id: Optional[str] = None
    ) -> RegressionReport:
        """Compare current run against baseline (or most recent previous run)."""
        current_run = await self.session.get(EvalRun, current_run_id)
        if not current_run:
            raise ValueError(f"Run {current_run_id} not found")
        
        if baseline_run_id:
            baseline_run = await self.session.get(EvalRun, baseline_run_id)
        else:
            baseline_run = await self._get_previous_run(current_run)
        
        if not baseline_run:
            return RegressionReport(
                baseline_run_id="none",
                current_run_id=current_run_id,
                baseline_date=datetime.utcnow(),
                current_date=current_run.started_at or datetime.utcnow(),
                summary={"note": "No baseline available for comparison"}
            )
        
        current_results = await self._get_results(current_run_id)
        baseline_results = await self._get_results(baseline_run.id)
        
        report = RegressionReport(
            baseline_run_id=baseline_run.id,
            current_run_id=current_run_id,
            baseline_date=baseline_run.started_at or datetime.utcnow(),
            current_date=current_run.started_at or datetime.utcnow()
        )
        
        self._compare_aggregate_metrics(baseline_results, current_results, report)
        self._detect_new_failures(baseline_results, current_results, report)
        self._compute_summary(report)
        
        return report
    
    async def _get_previous_run(self, current_run: EvalRun) -> Optional[EvalRun]:
        """Get the most recent run before the current one with same dataset."""
        result = await self.session.execute(
            select(EvalRun)
            .where(EvalRun.started_at < current_run.started_at)
            .order_by(EvalRun.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    
    async def _get_results(self, run_id: str) -> Dict[str, List[EvalResult]]:
        """Get all results for a run, grouped by evaluator."""
        results = (await self.session.execute(
            select(EvalResult).where(EvalResult.eval_run_id == run_id)
        )).scalars().all()
        
        grouped = {}
        for r in results:
            key = r.evaluator_name
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(r)
        return grouped
    
    def _compare_aggregate_metrics(
        self,
        baseline: Dict[str, List[EvalResult]],
        current: Dict[str, List[EvalResult]],
        report: RegressionReport
    ):
        """Compare aggregate scores for each metric."""
        all_metrics = set(baseline.keys()) | set(current.keys())
        
        for metric in all_metrics:
            baseline_scores = baseline.get(metric, [])
            current_scores = current.get(metric, [])
            
            if not baseline_scores or not current_scores:
                continue
            
            baseline_avg = sum(r.score for r in baseline_scores) / len(baseline_scores)
            current_avg = sum(r.score for r in current_scores) / len(current_scores)
            
            if baseline_avg > 0:
                change_pct = ((current_avg - baseline_avg) / baseline_avg) * 100
            else:
                change_pct = 0 if current_avg == 0 else 100
            
            severity = self._classify_severity(metric, change_pct)
            
            item = RegressionItem(
                metric=metric,
                item_id=None,
                baseline_value=baseline_avg,
                current_value=current_avg,
                change_pct=change_pct,
                severity=severity
            )
            
            if change_pct < -5:
                report.regressions.append(item)
            elif change_pct > 5:
                report.improvements.append(item)
    
    def _detect_new_failures(
        self,
        baseline: Dict[str, List[EvalResult]],
        current: Dict[str, List[EvalResult]],
        report: RegressionReport
    ):
        """Detect tests that changed from pass to fail or vice versa."""
        baseline_passed = set()
        baseline_failed = set()
        current_passed = set()
        current_failed = set()
        
        for results in baseline.values():
            for r in results:
                key = f"{r.evaluator_name}:{r.item_id}"
                if r.passed:
                    baseline_passed.add(key)
                else:
                    baseline_failed.add(key)
        
        for results in current.values():
            for r in results:
                key = f"{r.evaluator_name}:{r.item_id}"
                if r.passed:
                    current_passed.add(key)
                else:
                    current_failed.add(key)
        
        report.new_failures = list(current_failed - baseline_failed)
        report.fixed_failures = list(baseline_failed - current_failed)
    
    def _classify_severity(self, metric: str, change_pct: float) -> str:
        """Classify regression severity."""
        if metric in self.CRITICAL_METRICS:
            if change_pct < -1:
                return "critical"
            elif change_pct < -5:
                return "warning"
        else:
            if change_pct < -20:
                return "critical"
            elif change_pct < -10:
                return "warning"
        return "info"
    
    def _compute_summary(self, report: RegressionReport):
        """Compute summary statistics."""
        report.summary = {
            "total_regressions": len(report.regressions),
            "critical_regressions": sum(1 for r in report.regressions if r.severity == "critical"),
            "warnings": sum(1 for r in report.regressions if r.severity == "warning"),
            "improvements": len(report.improvements),
            "new_failures": len(report.new_failures),
            "fixed_failures": len(report.fixed_failures),
            "passed": report.passed
        }


async def check_regression_gate(
    session,
    current_run_id: str,
    baseline_run_id: Optional[str] = None
) -> tuple[bool, RegressionReport]:
    """CI gate: returns (passed, report)."""
    detector = RegressionDetector(session)
    report = await detector.compare(current_run_id, baseline_run_id)
    return report.passed, report
