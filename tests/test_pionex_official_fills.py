import unittest

from notifications.pionex_official_fills import (
    PionexFillSchemaError, validate_fills_window,
)


def response(fills):
    return {"result": True, "data": {"fills": fills}}


def fill(fid=1, ts=1500):
    return {"id": fid, "orderId": 9, "symbol": "BTC_USDT_PERP",
            "side": "SELL", "size": "0.25", "timestamp": ts}


class OfficialFillSchemaTests(unittest.TestCase):
    def normalize(self, data, **kwargs):
        return validate_fills_window(data, symbol="BTC_USDT_PERP",
                                     start_ms=1000, end_ms=2000, **kwargs)

    def test_documented_response(self):
        normalized = self.normalize(response([fill()]))
        self.assertEqual(normalized[0]["fill_id"], "1")
        self.assertEqual(normalized[0]["quantity"], "0.25")
        self.assertNotIn("effect", normalized)

    def test_limit_boundary_is_not_proof_of_completeness(self):
        with self.assertRaises(PionexFillSchemaError):
            self.normalize(response([fill(1), fill(2)]), limit=2)

    def test_wrong_symbol_and_duplicate_ids(self):
        wrong = fill()
        wrong["symbol"] = "ETH_USDT_PERP"
        for data in ([wrong], [fill(), fill()]):
            with self.assertRaises(PionexFillSchemaError):
                self.normalize(response(data))

    def test_numeric_and_string_fill_ids_cannot_alias(self):
        with self.assertRaises(PionexFillSchemaError):
            self.normalize(response([fill(1), fill("1")]))

    def test_rejects_boolean_timestamp_and_fill_identity(self):
        for bad in (dict(fill(), timestamp=True),
                    dict(fill(), id=True),
                    dict(fill(), orderId=False)):
            with self.subTest(bad=bad), self.assertRaises(PionexFillSchemaError):
                self.normalize(response([bad]))

    def test_rejects_nonfinite_and_nonpositive_sizes(self):
        for size in ("NaN", "Infinity", "-1", "0", "not-a-number"):
            with self.subTest(size=size), self.assertRaises(PionexFillSchemaError):
                self.normalize(response([dict(fill(), size=size)]))

    def test_out_of_window_and_failed_response(self):
        with self.assertRaises(PionexFillSchemaError):
            self.normalize(response([fill(ts=999)]))
        with self.assertRaises(PionexFillSchemaError):
            self.normalize({"result": False, "data": {"fills": []}})


if __name__ == "__main__":
    unittest.main()
