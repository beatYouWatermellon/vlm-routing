"""
src/ispd_evaluator.py

ISPD 2018/2019 style evaluator. Computes routing quality scores from
RoutingMetrics and produces comparison reports.
"""

import json
import time
from pathlib import Path
from typing import Dict, Tuple, Optional, List
from dataclasses import asdict

from .routing_toolkit import RoutingMetrics


class ISPDEvaluator:
    """
    ISPD 2018/2019 contest scorer.

    Scores are lower-is-better. Provides simplified and ISPD-style scoring
    modes as well as baseline-vs-optimized comparison reports.
    """

    VIOLATION_WEIGHTS = {
        "short_count": 500,
        "short_area": 500,
        "end_of_line": 500,
        "wire_spacing": 500,
        "via_spacing": 500,
        "corner_spacing": 500,
        "adjacent_cut_spacing": 500,
        "min_area": 500,
    }

    METRIC_WEIGHTS = {
        "single_cut_via": 4,
        "multi_cut_via": 2,
        "wirelength": 0.5,
        "out_of_guide_wl": 1,
        "out_of_guide_via": 1,
        "off_track_wl": 0.5,
        "off_track_via": 1,
        "wrong_way_wl": 1,
    }

    SIMPLIFIED_WEIGHTS = {
        "drc_total": 1000,
        "wirelength": 0.01,
        "via_count": 0.1,
    }

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def compute_score(
        self, metrics: RoutingMetrics, scoring_mode: str = "simplified"
    ) -> float:
        if scoring_mode == "simplified":
            return self._compute_simplified_score(metrics)
        elif scoring_mode == "ispd2019":
            return self._compute_ispd2019_score(metrics)
        elif scoring_mode == "ispd2018":
            return self._compute_ispd2018_score(metrics)
        else:
            raise ValueError(f"Unknown scoring mode: {scoring_mode}")

    def _compute_simplified_score(self, metrics: RoutingMetrics) -> float:
        return (
            metrics.drc_total * self.SIMPLIFIED_WEIGHTS["drc_total"]
            + metrics.wirelength * self.SIMPLIFIED_WEIGHTS["wirelength"]
            + metrics.via_count * self.SIMPLIFIED_WEIGHTS["via_count"]
        )

    def _compute_ispd2019_score(self, metrics: RoutingMetrics) -> float:
        drc_score = (
            metrics.drc_short * self.VIOLATION_WEIGHTS["short_count"]
            + metrics.drc_spacing * self.VIOLATION_WEIGHTS["wire_spacing"]
            + metrics.drc_min_width * 500
            + metrics.drc_end_of_line * self.VIOLATION_WEIGHTS["end_of_line"]
            + metrics.drc_via_spacing * self.VIOLATION_WEIGHTS["via_spacing"]
        )

        quality_score = (
            metrics.wirelength * self.METRIC_WEIGHTS["wirelength"]
            + metrics.via_count * self.METRIC_WEIGHTS["single_cut_via"]
        )

        return drc_score + quality_score

    def _compute_ispd2018_score(self, metrics: RoutingMetrics) -> float:
        return (
            metrics.drc_total * 500
            + metrics.wirelength * 0.5
            + metrics.via_count * 2
        )

    def evaluate_and_compare(
        self,
        baseline_metrics: RoutingMetrics,
        optimized_metrics: RoutingMetrics,
        benchmark_name: str,
        scoring_mode: str = "simplified",
    ) -> Dict:
        baseline_score = self.compute_score(baseline_metrics, scoring_mode)
        optimized_score = self.compute_score(optimized_metrics, scoring_mode)

        if baseline_score > 0:
            improvement = (baseline_score - optimized_score) / baseline_score * 100
        else:
            improvement = 0.0

        result = {
            "benchmark": benchmark_name,
            "scoring_mode": scoring_mode,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "baseline": {
                "drc_total": baseline_metrics.drc_total,
                "drc_breakdown": baseline_metrics.drc_breakdown,
                "wirelength_um": baseline_metrics.wirelength,
                "via_count": baseline_metrics.via_count,
                "score": baseline_score,
            },
            "optimized": {
                "drc_total": optimized_metrics.drc_total,
                "drc_breakdown": optimized_metrics.drc_breakdown,
                "wirelength_um": optimized_metrics.wirelength,
                "via_count": optimized_metrics.via_count,
                "score": optimized_score,
            },
            "improvement": {
                "score_pct": round(improvement, 2),
                "drc_delta": baseline_metrics.drc_total - optimized_metrics.drc_total,
                "drc_delta_pct": self._safe_pct(
                    baseline_metrics.drc_total - optimized_metrics.drc_total,
                    baseline_metrics.drc_total,
                ),
                "wl_delta_um": baseline_metrics.wirelength - optimized_metrics.wirelength,
                "wl_delta_pct": self._safe_pct(
                    baseline_metrics.wirelength - optimized_metrics.wirelength,
                    baseline_metrics.wirelength,
                ),
                "via_delta": baseline_metrics.via_count - optimized_metrics.via_count,
                "via_delta_pct": self._safe_pct(
                    baseline_metrics.via_count - optimized_metrics.via_count,
                    baseline_metrics.via_count,
                ),
            },
            "conclusion": self._generate_conclusion(
                baseline_metrics, optimized_metrics, improvement
            ),
        }

        report_path = self.output_dir / f"{benchmark_name}_report.json"
        with open(report_path, "w") as f:
            json.dump(result, f, indent=2)

        self._print_summary(result)
        return result

    @staticmethod
    def _safe_pct(delta: float, base: float) -> float:
        if base > 0:
            return round(delta / base * 100, 2)
        return 0.0

    @staticmethod
    def _generate_conclusion(
        baseline: RoutingMetrics, optimized: RoutingMetrics, improvement: float
    ) -> str:
        if optimized.drc_total == 0 and baseline.drc_total > 0:
            return "SUCCESS: DRC completely eliminated!"
        elif optimized.drc_total < baseline.drc_total:
            return f"IMPROVED: DRC reduced by {baseline.drc_total - optimized.drc_total}"
        elif improvement > 0:
            return f"IMPROVED: Score improved by {improvement:.1f}%"
        elif improvement == 0:
            return "NO CHANGE: Results are equivalent"
        else:
            return f"REGRESSED: Score degraded by {-improvement:.1f}%"

    def _print_summary(self, result: Dict):
        b = result["baseline"]
        o = result["optimized"]
        imp = result["improvement"]

        print(f"\n{'='*60}")
        print(f"ISPD Evaluation Report: {result['benchmark']}")
        print(f"Mode: {result['scoring_mode']}")
        print(f"{'='*60}")
        print(
            f"Baseline:   DRC={b['drc_total']:4d} WL={b['wirelength_um']:12.2f} "
            f"Via={b['via_count']:6d} Score={b['score']:12.2f}"
        )
        print(
            f"Optimized:  DRC={o['drc_total']:4d} WL={o['wirelength_um']:12.2f} "
            f"Via={o['via_count']:6d} Score={o['score']:12.2f}"
        )
        print(f"{'='*60}")
        print(f"Score Improvement: {imp['score_pct']:+.2f}%")
        print(f"DRC Delta: {imp['drc_delta']:+d} ({imp['drc_delta_pct']:+.1f}%)")
        print(f"WL Delta:  {imp['wl_delta_um']:+.2f} um ({imp['wl_delta_pct']:+.1f}%)")
        print(f"Via Delta: {imp['via_delta']:+d} ({imp['via_delta_pct']:+.1f}%)")
        print(f"Conclusion: {result['conclusion']}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    evaluator = ISPDEvaluator("/tmp/eval_test")
    print("ISPDEvaluator initialized successfully")
