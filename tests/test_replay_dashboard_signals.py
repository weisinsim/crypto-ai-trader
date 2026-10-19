import unittest
import tempfile
from pathlib import Path

from validation.replay_dashboard_signals import ema, rsi, atr, adx, replay, ts_ms, read_rows, closed_rows_at, signal_state_for, risk_levels, validate_candle_cadence


class ReplayDashboardSignalsTests(unittest.TestCase):
    def test_adx_optimized_matches_original_rolling_calculation(self):
        def original_adx(h, l, c, n=14):
            if len(c) < n*2+1: return None
            tr=[]; plus=[]; minus=[]
            for i in range(1,len(c)):
                up=h[i]-h[i-1]; dn=l[i-1]-l[i]
                tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
                plus.append(up if up>dn and up>0 else 0)
                minus.append(dn if dn>up and dn>0 else 0)
            def avg(a): return sum(a[-n:])/n if len(a)>=n else None
            dxs=[]
            for j in range(n,len(tr)):
                t,p,m=avg(tr[:j+1]),avg(plus[:j+1]),avg(minus[:j+1])
                if t and t>0:
                    pdi=100*p/t; mdi=100*m/t
                    dxs.append(100*abs(pdi-mdi)/max(pdi+mdi,1e-9))
            return sum(dxs[-n:])/min(n,len(dxs)) if dxs else None

        close = [100 + i * 0.13 + ((i % 7) - 3) * 0.4 for i in range(180)]
        high = [v + 0.8 + (i % 3) * 0.1 for i, v in enumerate(close)]
        low = [v - 0.7 - (i % 4) * 0.1 for i, v in enumerate(close)]
        for end in (29, 40, 75, 120, len(close)):
            self.assertAlmostEqual(adx(high[:end], low[:end], close[:end]), original_adx(high[:end], low[:end], close[:end]), places=10)

    def test_adx_flat_history_matches_original_zero_true_range_handling(self):
        def original_adx(h, l, c, n=14):
            if len(c) < n*2+1: return None
            tr=[]; plus=[]; minus=[]
            for i in range(1,len(c)):
                up=h[i]-h[i-1]; dn=l[i-1]-l[i]
                tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
                plus.append(up if up>dn and up>0 else 0)
                minus.append(dn if dn>up and dn>0 else 0)
            def avg(a): return sum(a[-n:])/n if len(a)>=n else None
            dxs=[]
            for j in range(n,len(tr)):
                t,p,m=avg(tr[:j+1]),avg(plus[:j+1]),avg(minus[:j+1])
                if t and t>0:
                    pdi=100*p/t; mdi=100*m/t
                    dxs.append(100*abs(pdi-mdi)/max(pdi+mdi,1e-9))
            return sum(dxs[-n:])/min(n,len(dxs)) if dxs else None
        close = [100.0] * 40 + [100.5, 101, 100.7, 102, 101.4, 102.2, 103]
        high = [v + (0 if i < 40 else 0.5) for i, v in enumerate(close)]
        low = [v - (0 if i < 40 else 0.5) for i, v in enumerate(close)]
        self.assertAlmostEqual(adx(high, low, close), original_adx(high, low, close), places=10)

    def test_zero_atr_uses_live_model_fallback_for_risk_levels(self):
        btc_stop, btc_target, btc_target2 = risk_levels("BTCUSDT", 100, 1, 0, 1.2, 1.5)
        self.assertAlmostEqual(btc_stop, 99.4)
        self.assertAlmostEqual(btc_target, 100.75)
        self.assertAlmostEqual(btc_target2, 101.125)

        xrp_stop, xrp_target, xrp_target2 = risk_levels("XRPUSDT", 100, 1, 0, 1.7, 2.8)
        self.assertAlmostEqual(xrp_stop, 98.3)
        self.assertAlmostEqual(xrp_target, 102.8)
        self.assertAlmostEqual(xrp_target2, 103.5)

    def test_candle_cadence_accepts_consecutive_bars(self):
        rows = [{"ts": 1000}, {"ts": 4600}, {"ts": 8200}]
        self.assertEqual(validate_candle_cadence(rows, 3600), rows)

    def test_candle_cadence_rejects_missing_bar(self):
        rows = [{"ts": 1000}, {"ts": 4600}, {"ts": 11800}]
        with self.assertRaisesRegex(ValueError, "missing or duplicate bars"):
            validate_candle_cadence(rows, 3600, "fixture.csv")

    def test_candle_cadence_rejects_invalid_interval(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            validate_candle_cadence([], 0)

    def test_signal_state_mapping_preserves_direction(self):
        self.assertEqual(signal_state_for("", "WATCH_LONG", 1), "WATCH-LONG")
        self.assertEqual(signal_state_for("", "WATCH_SHORT", -1), "WATCH-SHORT")
        self.assertEqual(signal_state_for("", "WATCH_SCORE_BELOW_75", 1), "WATCH-LONG")
        self.assertEqual(signal_state_for("", "WATCH_SCORE_BELOW_75", -1), "WATCH-SHORT")
        self.assertEqual(signal_state_for("LONG", "ENTRY", 1), "LONG")
        self.assertEqual(signal_state_for("", "XRP_KILL", 0), "NO-TRADE")

    def test_timezone_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            ts_ms("2025-01-01T00:00:00")

    def test_submillisecond_timestamp_rejected_instead_of_truncated(self):
        with self.assertRaisesRegex(ValueError, "millisecond precision"):
            ts_ms("2025-01-01T00:00:00.000001Z")

    def test_millisecond_timestamp_precision_accepted(self):
        self.assertEqual(ts_ms("2025-01-01T00:00:00.123Z") % 1000, 123)

    def test_invalid_ohlc_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\n2025-01-01T01:00:00Z,100,99,98,100,1,100\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_rows(path)

    def test_missing_volume_columns_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "missing_volume.csv"
            path.write_text("timestamp,open,high,low,close\n2025-01-01T01:00:00Z,100,101,99,100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing required columns"):
                read_rows(path)

    def test_malformed_numeric_field_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad_number.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\n2025-01-01T01:00:00Z,not-a-number,101,99,100,1,100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid timestamp or numeric OHLCV field"):
                read_rows(path)

    def test_non_finite_volume_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad_volume.csv"
            path.write_text("timestamp,open,high,low,close,volume,quote_volume\n2025-01-01T01:00:00Z,100,101,99,100,NaN,100\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_rows(path)

    def test_only_closed_higher_timeframe_bars_are_used(self):
        bars = [
            {"ts": 0, "c": 1},                 # 00:00-04:00, closed
            {"ts": 14_400_000, "c": 2},         # 04:00-08:00, closed
            {"ts": 28_800_000, "c": 3},         # 08:00-12:00, still open
        ]
        # At 11:00, only candles whose OPEN + 4h <= decision time are usable.
        self.assertEqual(closed_rows_at(bars, 11 * 3_600_000), bars[:2])

    def test_closed_higher_timeframe_bar_is_included_at_exact_close(self):
        bars = [{"ts": 4 * 3_600_000, "c": 1}, {"ts": 8 * 3_600_000, "c": 2}]
        self.assertEqual(closed_rows_at(bars, 8 * 3_600_000), bars[:1])

    def test_incremental_four_hour_index_matches_closed_row_filter(self):
        bars = [{"ts": i * 14_400_000, "c": i} for i in range(12)]
        decisions = [
            2 * 3_600_000,
            4 * 3_600_000,
            7 * 3_600_000,
            12 * 3_600_000,
            20 * 3_600_000,
            40 * 3_600_000,
        ]
        index = 0
        for decision_close_ms in decisions:
            index = closed_four_count_at(bars, decision_close_ms, index)
            expected = len(closed_rows_at(bars, decision_close_ms, 14_400_000))
            self.assertEqual(index, expected)
            self.assertTrue(all(bars[j]["ts"] + 14_400_000 <= decision_close_ms for j in range(index)))
            if index < len(bars):
                self.assertGreater(bars[index]["ts"] + 14_400_000, decision_close_ms)

    def test_incremental_four_hour_index_rejects_no_closed_rows_without_advancing(self):
        bars = [{"ts": 14_400_000, "c": 1}, {"ts": 28_800_000, "c": 2}]
        self.assertEqual(closed_four_count_at(bars, 14_400_000, 0), 0)
        self.assertEqual(closed_four_count_at(bars, 14_400_000, 0), 0)

    def test_closed_rows_rejects_invalid_interval(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            closed_rows_at([], 1000, interval_ms=0)

    def test_ema_seed_and_trend(self):
        self.assertEqual(ema([1, 2, 3], 2), 2.5)
        self.assertGreater(ema(list(range(1, 30)), 5), ema(list(range(1, 30)), 10))

    def test_rsi_rising_series_is_100(self):
        self.assertEqual(rsi(list(range(1, 30))), 100.0)

    def test_atr_positive(self):
        close = [100 + i for i in range(20)]
        high = [x + 2 for x in close]
        low = [x - 1 for x in close]
        self.assertGreater(atr(high, low, close), 0)

    def test_xrp_watch_state_matches_live_watch_score(self):
        source = (Path(__file__).resolve().parents[1] / "validation" / "replay_dashboard_signals.py").read_text(encoding="utf-8")
        self.assertIn("evaluate_xrp_signal", source)
        self.assertIn('return "WATCH-LONG"', source)
        self.assertIn('return "WATCH-SHORT"', source)
        shared = (Path(__file__).resolve().parents[1] / "validation" / "xrp_model.py").read_text(encoding="utf-8")
        self.assertIn('score = 43 if signal == "WATCH-LONG"', shared)
        self.assertIn('score = 43 if signal == "WATCH-LONG" else 75 if signal == "LONG" else 0', shared)

    def test_eth_is_suppressed(self):
        one = []
        four = []
        for i in range(240):
            t = 1_700_000_000_000 + i * 3_600_000
            c = 100 + i * 0.2
            one.append({"timestamp": "2023-11-14T00:00:00Z", "ts": t, "o": c, "h": c+1, "l": c-1, "c": c, "v": 10, "q": 1000})
        for i in range(70):
            t = 1_700_000_000_000 + (i+1)*14_400_000 - 1
            c = 100 + i * 0.8
            four.append({"timestamp": "2023-11-14T00:00:00Z", "ts": t, "o": c, "h": c+2, "l": c-1, "c": c, "v": 40, "q": 4000})
        self.assertEqual(replay("ETHUSDT", one, four), [])
        states = replay("ETHUSDT", one, four, include_states=True)
        self.assertTrue(states)
        self.assertTrue(all(row["status"] == "NO-TRADE" for row in states))
        self.assertTrue(all(row["signal"] == "NO-TRADE" for row in states))
        self.assertTrue(all(row["reason"] == "NO_PRODUCTION" for row in states))
        self.assertTrue(all(row["target2"] is None for row in states))



    def test_out_of_order_candles_are_rejected_instead_of_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out_of_order.csv"
            path.write_text(
                "timestamp,open,high,low,close,volume,quote_volume\n"
                "2025-01-01T02:00:00Z,100,101,99,100,1,100\n"
                "2025-01-01T01:00:00Z,100,101,99,100,1,100\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Out-of-order candle timestamp"):
                read_rows(path)

    def test_duplicate_candles_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate.csv"
            path.write_text(
                "timestamp,open,high,low,close,volume,quote_volume\n"
                "2025-01-01T01:00:00Z,100,101,99,100,1,100\n"
                "2025-01-01T01:00:00Z,100,101,99,100,1,100\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate candle timestamp"):
                read_rows(path)


if __name__ == "__main__":
    unittest.main()
