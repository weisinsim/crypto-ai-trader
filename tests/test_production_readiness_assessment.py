import unittest

from execution.production_readiness_assessment import (
    ReadinessLimits,
    assess_production_readiness,
)


def snapshot(**changes):
    value = {
        "read_only": True,
        "order_submission_supported": False,
        "account_permissions": True,
        "available_balance_usdt": 500.0,
        "total_wallet_balance_usdt": 600.0,
        "gross_open_notional_usdt": 0.0,
        "open_position_count": 0,
        "open_positions": [],
        "position_mode": {"dualSidePosition": False},
    }
    value.update(changes)
    return value


def rules(**changes):
    value = {
        "status": "TRADING",
        "contract_type": "PERPETUAL",
        "quote_asset": "USDT",
        "filters": [{"filterType": "LOT_SIZE"}],
    }
    value.update(changes)
    return value


class ProductionReadinessAssessmentTests(unittest.TestCase):
    def test_readiness_is_assessment_only_even_when_all_checks_pass(self):
        result = assess_production_readiness(snapshot(), rules(), explicit_operator_approval=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["order_submission_authorized"])
        self.assertTrue(result["assessment_only"])

    def test_missing_operator_approval_blocks(self):
        result = assess_production_readiness(snapshot(), rules())
        self.assertIn("explicit_operator_approval_missing", result["blockers"])
        self.assertFalse(result["order_submission_authorized"])

    def test_open_position_blocks_until_reconciled(self):
        result = assess_production_readiness(
            snapshot(open_position_count=1, gross_open_notional_usdt=250.0),
            rules(),
            explicit_operator_approval=True,
        )
        self.assertIn("open_positions_must_be_reconciled", result["blockers"])
        self.assertIn("gross_notional_above_readiness_limit", result["blockers"])

    def test_nontrading_symbol_and_missing_filters_block(self):
        result = assess_production_readiness(
            snapshot(), rules(status="BREAK", filters=[]), explicit_operator_approval=True
        )
        self.assertIn("symbol_not_trading", result["blockers"])
        self.assertIn("symbol_filters_missing", result["blockers"])

    def test_invalid_snapshot_fails_closed(self):
        result = assess_production_readiness({"read_only": False}, None)
        self.assertFalse(result["ready"])
        self.assertIn("read_only_snapshot_required", result["blockers"])
        self.assertFalse(result["order_submission_authorized"])

    def test_invalid_numeric_values_block(self):
        result = assess_production_readiness(
            snapshot(available_balance_usdt=float("nan")), rules(),
            explicit_operator_approval=True,
        )
        self.assertIn("invalid_available_balance_usdt", result["blockers"])


if __name__ == "__main__":
    unittest.main()
