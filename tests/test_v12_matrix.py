import unittest

from research.run_v12_matrix import _development_score, _funding_audit_allows_research


def _with_development_stress(report):
    report["development_cost_stress_0_30pct_round_trip"] = {
        "walk_forward_folds": report["walk_forward_folds"],
        "walk_forward_aggregate": report["walk_forward_aggregate"],
    }
    return report


class V12MatrixTests(unittest.TestCase):
    def test_development_selection_requires_majority_positive_folds_and_pf(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": -0.2}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.2,
                "net_return_pct": 2.0, "max_drawdown_pct": 8.0,
            },
        }
        self.assertEqual(_development_score(_with_development_stress(report))[0], 1)

    def test_development_selection_rejects_weak_walk_forward(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": -0.5}},
                {"test_metrics": {"net_return_pct": -0.2}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.2,
                "net_return_pct": 2.0, "max_drawdown_pct": 8.0,
            },
        }
        self.assertEqual(_development_score(_with_development_stress(report))[0], 0)

    def test_development_selection_rejects_insufficient_trades(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": 0.2}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 19, "profit_factor": 1.2,
                "net_return_pct": 2.0, "max_drawdown_pct": 8.0,
            },
        }
        self.assertEqual(_development_score(_with_development_stress(report))[0], 0)


    def test_development_selection_rejects_nonfinite_or_boolean_metrics(self):
        base = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": -0.2}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.2,
                "net_return_pct": 2.0, "max_drawdown_pct": 8.0,
            },
        }
        for field, value in (
            ("trades", True),
            ("profit_factor", float("inf")),
            ("net_return_pct", float("nan")),
            ("max_drawdown_pct", float("inf")),
            ("max_drawdown_pct", -0.1),
            ("net_return_pct", 10 ** 400),
        ):
            report = {
                "walk_forward_folds": [dict(row) for row in base["walk_forward_folds"]],
                "walk_forward_aggregate": dict(base["walk_forward_aggregate"]),
            }
            report["walk_forward_aggregate"][field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(_development_score(_with_development_stress(report))[0], 0)

    def test_development_selection_requires_exact_cost_stress_metrics(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": 0.2}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.5,
                "net_return_pct": 3.0, "max_drawdown_pct": 8.0,
            },
            "development_cost_stress_0_30pct_round_trip": {
                "walk_forward_folds": [
                    {"test_metrics": {"net_return_pct": -1.0}},
                    {"test_metrics": {"net_return_pct": -0.5}},
                    {"test_metrics": {"net_return_pct": 0.2}},
                    {"test_metrics": {"net_return_pct": -0.8}},
                ],
                "walk_forward_aggregate": {
                    "trades": 40, "profit_factor": 0.8,
                    "net_return_pct": -2.0, "max_drawdown_pct": 8.0,
                },
            },
        }
        self.assertEqual(_development_score(report)[0], 0)

    def test_development_selection_rejects_missing_cost_stress_metrics(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": 0.2}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.5,
                "net_return_pct": 3.0, "max_drawdown_pct": 8.0,
            },
        }
        self.assertEqual(_development_score(report)[0], 0)

    def test_development_selection_rejects_incomplete_fold_metrics(self):
        report = {
            "walk_forward_folds": [
                {"test_metrics": {"net_return_pct": 1.0}},
                {"test_metrics": {"net_return_pct": 0.5}},
                {"test_metrics": {"net_return_pct": float("nan")}},
                {"test_metrics": {"net_return_pct": 0.8}},
            ],
            "walk_forward_aggregate": {
                "trades": 40, "profit_factor": 1.2,
                "net_return_pct": 2.0, "max_drawdown_pct": 8.0,
            },
        }
        self.assertEqual(_development_score(_with_development_stress(report))[0], 0)

    def test_funding_audit_review_required_or_unknown_status_blocks_research(self):
        self.assertFalse(_funding_audit_allows_research({"status": "REVIEW_REQUIRED"}))
        self.assertFalse(_funding_audit_allows_research({"status": "UNKNOWN"}))
        self.assertFalse(_funding_audit_allows_research(None))

    def test_timestamp_aligned_but_unreconciled_funding_is_labeled_not_reconciled(self):
        funding = {
            "source_labels": ["binance_funding_rate_endpoint_markPrice"],
            "venue_labels": ["binance_usdm"],
            "records_outside_candle_range": 0,
            "nonmatching_in_range": 0,
            "records_missing_or_invalid_source_label": 0,
            "records_missing_or_invalid_venue_label": 0,
        }
        common = {
            "candle_timestamps_strictly_increasing": True,
            "duplicate_candle_records": 0,
            "duplicate_event_records": 0,
            "intervals_exceeding_gap_threshold": 0,
            "funding": funding,
        }
        self.assertTrue(_funding_audit_allows_research({
            **common, "status": "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED"
        }))
        self.assertTrue(_funding_audit_allows_research({
            **common, "status": "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED"
        }))

    def test_funding_audit_rejects_unknown_provenance_and_bad_coverage(self):
        base = {
            "status": "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED",
            "candle_timestamps_strictly_increasing": True,
            "duplicate_candle_records": 0,
            "duplicate_event_records": 0,
            "intervals_exceeding_gap_threshold": 0,
            "funding": {
                "source_labels": ["binance_funding_rate_endpoint_markPrice"],
                "venue_labels": ["binance_usdm"],
                "records_outside_candle_range": 0,
                "nonmatching_in_range": 0,
                "records_missing_or_invalid_source_label": 0,
                "records_missing_or_invalid_venue_label": 0,
            },
        }
        invalid = []
        unknown_source = {**base, "funding": {**base["funding"], "source_labels": ["unknown"]}}
        missing_venue = {**base, "funding": {**base["funding"], "venue_labels": []}}
        duplicate = {**base, "duplicate_event_records": 1}
        outside = {**base, "funding": {**base["funding"], "records_outside_candle_range": 1}}
        invalid.extend([unknown_source, missing_venue, duplicate, outside])
        for audit in invalid:
            with self.subTest(audit=audit):
                self.assertFalse(_funding_audit_allows_research(audit))


if __name__ == "__main__":
    unittest.main()
