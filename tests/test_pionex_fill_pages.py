import unittest

from notifications.pionex_fill_pages import PaginationError, collect_verified_fill_pages


SYMBOL = "BTC_USDT_PERP"


def page(request_cursor, next_cursor, fill_ids):
    return {
        "request_cursor": request_cursor, "next_cursor": next_cursor,
        "fills": [{"symbol": SYMBOL, "fill_id": fill_id} for fill_id in fill_ids],
    }


class FillPageTests(unittest.TestCase):
    def collect(self, pages, **kwargs):
        return collect_verified_fill_pages(
            pages, expected_symbol=SYMBOL, complete=True, **kwargs)

    def test_complete_chain(self):
        self.assertEqual(
            [fill["fill_id"] for fill in self.collect([
                page(None, "c1", ["f1"]), page("c1", None, ["f2"]),
            ])], ["f1", "f2"])

    def test_incomplete_chain_rejected(self):
        with self.assertRaises(PaginationError):
            self.collect([page(None, "c1", ["f1"])])
        with self.assertRaises(PaginationError):
            self.collect([page(None, "c1", ["f1"]), page("wrong", None, ["f2"])])
        with self.assertRaises(PaginationError):
            collect_verified_fill_pages(
                [page(None, None, ["f1"])], expected_symbol=SYMBOL)

    def test_duplicate_fill_rejected(self):
        with self.assertRaises(PaginationError):
            self.collect([page(None, "c1", ["f1"]), page("c1", None, ["f1"])])

    def test_repeated_cursor_rejected(self):
        with self.assertRaises(PaginationError):
            self.collect([page(None, "c1", ["f1"]), page("c1", "c1", ["f2"]),
                          page("c1", None, ["f3"])])

    def test_symbol_mismatch_rejected(self):
        p = page(None, None, ["f1"])
        p["fills"][0]["symbol"] = "ETH_USDT_PERP"
        with self.assertRaises(PaginationError):
            self.collect([p])

    def test_page_and_fill_caps(self):
        with self.assertRaises(PaginationError):
            self.collect([page(None, None, ["f1", "f2"])], max_fills=1)
        with self.assertRaises(PaginationError):
            self.collect([page(None, "c1", []), page("c1", None, [])], max_pages=1)


if __name__ == "__main__":
    unittest.main()
