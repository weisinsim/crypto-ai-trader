import unittest

from notifications.pionex_exit_reason import ExitReasonError, classify_exit_reason


def order(oid="o1", reason="TAKE_PROFIT"):
    return {"order_id": oid, "close_effect": "CLOSE",
            "execution_status": "EXECUTED", "trigger_kind": reason}


def verified(orders):
    return classify_exit_reason(orders, schema_verified=True,
                                all_fills_matched=True, coverage_complete=True)


class ExitReasonTests(unittest.TestCase):
    def test_verified_tp_sl_and_manual(self):
        self.assertEqual(verified([order()]), "TAKE_PROFIT")
        self.assertEqual(verified([order(reason="STOP_LOSS")]), "STOP_LOSS")
        self.assertEqual(verified([order(reason="MANUAL")]), "MANUAL")

    def test_mixed_and_unknown_remain_unverified(self):
        self.assertEqual(verified([order(), order("o2", "STOP_LOSS")]),
                         "EXIT_REASON_UNVERIFIED")
        self.assertEqual(verified([order(reason="UNKNOWN")]),
                         "EXIT_REASON_UNVERIFIED")

    def test_unverified_schema_or_missing_fill_coverage_fails(self):
        for kwargs in (
            {"schema_verified": False, "all_fills_matched": True, "coverage_complete": True},
            {"schema_verified": True, "all_fills_matched": False, "coverage_complete": True},
            {"schema_verified": True, "all_fills_matched": True, "coverage_complete": False},
        ):
            with self.assertRaises(ExitReasonError):
                classify_exit_reason([order()], **kwargs)

    def test_unexecuted_or_open_order_rejected(self):
        for key, value in (("execution_status", "OPEN"), ("close_effect", "OPEN")):
            candidate = order()
            candidate[key] = value
            with self.assertRaises(ExitReasonError):
                verified([candidate])

    def test_duplicate_order_rejected(self):
        with self.assertRaises(ExitReasonError):
            verified([order(), order()])


if __name__ == "__main__":
    unittest.main()
