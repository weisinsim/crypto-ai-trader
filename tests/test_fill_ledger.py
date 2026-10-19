import unittest

from execution.fill_ledger import record_confirmed_fill_pnl


class FakeStore:
    def __init__(self):
        self.events = {}

    def record_realized_pnl(self, amount_usdt, now=None, event_id=None):
        if event_id in self.events:
            return False
        self.events[event_id] = amount_usdt
        return True


class FillLedgerTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.trade = {
            "id": 77,
            "orderId": 123,
            "clientOrderId": "client-1",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "0.25",
            "price": "65000",
            "commission": "0.01",
            "realizedPnl": "-1.25",
            "time": 1780000000000,
        }

    def test_records_pnl_with_stable_event_id(self):
        result = record_confirmed_fill_pnl(self.store, self.trade)
        self.assertTrue(result["pnl_event_recorded"])
        self.assertEqual(result["event_id"], "binance-usdm:BTCUSDT:123:77")
        self.assertEqual(self.store.events[result["event_id"]], -1.25)

    def test_duplicate_fill_does_not_double_count(self):
        first = record_confirmed_fill_pnl(self.store, self.trade)
        second = record_confirmed_fill_pnl(self.store, self.trade)
        self.assertTrue(first["pnl_event_recorded"])
        self.assertFalse(second["pnl_event_recorded"])
        self.assertEqual(len(self.store.events), 1)

    def test_missing_store_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "runtime_state_store_required"):
            record_confirmed_fill_pnl(None, self.trade)

    def test_commission_is_not_misrepresented_as_usdt(self):
        result = record_confirmed_fill_pnl(self.store, self.trade)
        self.assertEqual(result["commission_accounting"],
                         "not_converted_or_subtracted")
        self.assertEqual(result["fill"]["commission"], "0.01")


if __name__ == "__main__":
    unittest.main()
