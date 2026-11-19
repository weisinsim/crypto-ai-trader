import unittest
from unittest.mock import patch

import app as app_module


class ConfirmedSignalPromotionGateTests(unittest.TestCase):
    def _entry(self, signal="LONG"):
        return {
            "signal": signal,
            "opportunity_tier": "A",
            "entry": 100.0,
            "sl": 98.0,
            "tp1": 104.0,
            "tp2": 106.0,
            "score": 82,
            "score_label": "A",
            "entry_location": "OPTIMAL",
            "confirmed_signal": "NO-TRADE",
            "confirmed_status": "WAITING",
        }

    def _validated_model(self, status):
        return {
            "name": "Test Model",
            "status": status,
            "validation": {
                "holdout_passed": True,
                "walk_forward_passed": True,
                "cost_stress_passed": True,
                "report_id": "validation-report-001",
            },
        }

    def test_validated_candidate_and_production_models_can_confirm(self):
        for status in ("CANDIDATE", "PRODUCTION"):
            with self.subTest(status=status):
                entry = self._entry()
                with patch.dict(app_module.cache, {"BTCUSDT": entry}), patch.dict(
                    app_module.confirmed_state, {"BTCUSDT": "NO-TRADE"}
                ), patch.dict(app_module.signal_history, {"BTCUSDT": []}), patch.dict(
                    app_module.MODEL_LIBRARY, {"BTCUSDT": self._validated_model(status)}
                ):
                    self.assertTrue(app_module.record_confirmed_signal("BTCUSDT", 1_800_000_000_000))
                    self.assertEqual(entry["confirmed_signal"], "LONG")
                    self.assertEqual(entry["confirmed_status"], "ACTIVE")
                    self.assertIs(entry["model_validation_passed"], True)

    def test_research_model_cannot_confirm_entry(self):
        entry = self._entry()
        model = self._validated_model("RESEARCH")
        with patch.dict(app_module.cache, {"BTCUSDT": entry}), patch.dict(
            app_module.confirmed_state, {"BTCUSDT": "NO-TRADE"}
        ), patch.dict(app_module.signal_history, {"BTCUSDT": []}), patch.dict(
            app_module.MODEL_LIBRARY, {"BTCUSDT": model}
        ):
            self.assertFalse(app_module.record_confirmed_signal("BTCUSDT", 1_800_000_000_000))
            self.assertEqual(entry["confirmed_status"], "WAITING_VALIDATION")
            self.assertIs(entry["model_validation_passed"], False)

    def test_revoked_validation_invalidates_unchanged_confirmed_direction(self):
        entry = self._entry()
        entry.update({
            "confirmed_signal": "LONG",
            "confirmed_signal_time": 1_799_999_000_000,
            "confirmed_entry": 100.0,
            "confirmed_sl": 98.0,
            "confirmed_tp1": 104.0,
            "confirmed_tp2": 106.0,
            "confirmed_status": "ACTIVE",
        })
        history = [{"status": "ACTIVE"}]
        model = self._validated_model("CANDIDATE")
        model["validation"]["cost_stress_passed"] = False
        with patch.dict(app_module.cache, {"BTCUSDT": entry}), patch.dict(
            app_module.confirmed_state, {"BTCUSDT": "LONG"}
        ), patch.dict(app_module.signal_history, {"BTCUSDT": history}), patch.dict(
            app_module.MODEL_LIBRARY, {"BTCUSDT": model}
        ):
            self.assertFalse(app_module.record_confirmed_signal("BTCUSDT", 1_800_000_000_000))
            self.assertEqual(entry["confirmed_signal"], "NO-TRADE")
            self.assertEqual(entry["confirmed_status"], "WAITING_VALIDATION")
            self.assertEqual(history[0]["status"], "INVALIDATED")


if __name__ == "__main__":
    unittest.main()
