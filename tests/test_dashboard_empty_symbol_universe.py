import unittest

from validation.dashboard_health import build_health_payload


class EmptySymbolUniverseHealthTests(unittest.TestCase):
    def test_empty_symbol_universe_is_not_healthy(self):
        result = build_health_payload(
            now=1_800_000_000.0,
            symbol_cache={},
            symbols=[],
            status="CONNECTED",
            source="binance",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["symbols_ready"], 0)
        self.assertEqual(result["symbols_total"], 0)
        self.assertEqual(result["analysis_ready"], 0)
        self.assertEqual(result["analysis_total"], 0)
        self.assertEqual(result["stale_symbols"], [])


if __name__ == "__main__":
    unittest.main()
