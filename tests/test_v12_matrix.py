import unittest

from research.run_v12_matrix import _development_score


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


if __name__ == "__main__":
    unittest.main()
