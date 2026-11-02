import unittest

from research.run_v12_matrix import _development_score, _funding_audit_allows_research


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
        self.assertEqual(_development_score(report)[0], 1)

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
        self.assertEqual(_development_score(report)[0], 0)

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
        self.assertEqual(_development_score(report)[0], 0)


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
        self.assertEqual(_development_score(report)[0], 0)

    def test_funding_audit_review_required_or_unknown_status_blocks_research(self):
        self.assertFalse(_funding_audit_allows_research({"status": "REVIEW_REQUIRED"}))
        self.assertFalse(_funding_audit_allows_research({"status": "UNKNOWN"}))
        self.assertFalse(_funding_audit_allows_research(None))

    def test_timestamp_aligned_but_unreconciled_funding_is_labeled_not_reconciled(self):
        self.assertTrue(_funding_audit_allows_research({
            "status": "TIMESTAMP_ALIGNMENT_PASS_NOT_RECONCILED"
        }))
        self.assertTrue(_funding_audit_allows_research({
            "status": "BOUNDARY_JITTER_NORMALIZED_ALIGNMENT_PASS_NOT_RECONCILED"
        }))


if __name__ == "__main__":
    unittest.main()
