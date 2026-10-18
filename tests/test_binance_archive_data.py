import csv
import hashlib
import io
import json
import tempfile
import zipfile
import unittest
from pathlib import Path

from research.binance_archive_data import _read_zip, _validated_candle, _deduplicate_candles, _strict_timestamp, build_output_manifest, download_archive


def make_zip(rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(["open_time", "open", "high", "low", "close", "volume",
                         "close_time", "quote_volume"])
        writer.writerows(rows)
        archive.writestr("sample.csv", text.getvalue())
    return buffer.getvalue()


class BinanceArchiveDataTests(unittest.TestCase):
    def test_read_zip_skips_header_and_keeps_closed_rows_in_requested_range(self):
        hour = 3_600_000
        payload = make_zip([
            [0, "10", "12", "9", "11", "5", hour - 1, "55"],
            [hour, "11", "13", "10", "12", "6", 2 * hour - 1, "72"],
            [2 * hour, "12", "14", "11", "13", "7", 3 * hour - 1, "91"],
        ])
        rows = _read_zip(payload, 0, 2 * hour, hour)
        self.assertEqual([row["t"] for row in rows], [0, hour])
        self.assertEqual(rows[0]["q"], 55.0)

    def test_output_manifest_fingerprints_csv_and_does_not_claim_authenticity(self):
        payload = b"t,o,h,l,c,v,q\\n0,1,2,0,1,1,1\\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.csv"
            path.write_bytes(payload)
            manifest = build_output_manifest(
                path, symbol="btcusdt", interval="1h",
                start_ms=0, end_ms=3_600_000, bars=1,
                request_log=[{"source": "binance_vision_monthly_archive", "status_code": 200,
                              "payload_bytes": 123, "payload_sha256": "abc"}],
            )
        self.assertEqual(manifest["status"], "STRUCTURAL_COVERAGE_COMPLETE")
        self.assertEqual(manifest["symbol"], "BTCUSDT")
        self.assertEqual(manifest["file"]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(manifest["file"]["size_bytes"], len(payload))
        self.assertFalse(manifest["source_authenticity_verified"])
        self.assertEqual(manifest["requested_range_ms"]["end_exclusive"], 3_600_000)
        self.assertEqual(manifest["request_evidence"]["request_count"], 1)
        self.assertEqual(manifest["request_evidence"]["requests"][0]["status_code"], 200)
        self.assertIn("do not independently prove upstream authenticity", manifest["request_evidence"]["note"])

    def test_strict_timestamp_accepts_integer_string(self):
        self.assertEqual(_strict_timestamp(" 123 ", field="test timestamp"), 123)

    def test_strict_timestamp_rejects_fractional_values(self):
        for value in (1.5, "1.5"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid test timestamp"):
                    _strict_timestamp(value, field="test timestamp")

    def test_strict_timestamp_rejects_boolean_and_negative_values(self):
        for value in (True, -1, "-1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "invalid test timestamp|negative timestamp"):
                    _strict_timestamp(value, field="test timestamp")

    def test_strict_timestamp_rejects_non_finite_float(self):
        with self.assertRaisesRegex(ValueError, "finite integer"):
            _strict_timestamp(float("nan"), field="test timestamp")

    def test_rejects_non_finite_candle_prices(self):
        with self.assertRaisesRegex(ValueError, "non-finite"):
            _validated_candle([0, "10", "12", "9", "nan", "5", 3599999, "55"], 0)

    def test_rejects_inconsistent_high_low(self):
        with self.assertRaisesRegex(ValueError, "high price inconsistent"):
            _validated_candle([0, "10", "10", "9", "11", "5", 3599999, "55"], 0)

    def test_rejects_negative_volume(self):
        with self.assertRaisesRegex(ValueError, "negative volume"):
            _validated_candle([0, "10", "12", "9", "11", "-1", 3599999, "55"], 0)

    def test_accepts_valid_ohlcv(self):
        candle = _validated_candle([0, "10", "12", "9", "11", "5", 3599999, "55"], 0)
        self.assertEqual(candle, {"t": 0, "o": 10.0, "h": 12.0, "l": 9.0,
                                  "c": 11.0, "v": 5.0, "q": 55.0})

    def test_rejects_duplicate_candle_timestamp(self):
        candle = {"t": 0, "o": 10.0, "h": 12.0, "l": 9.0,
                  "c": 11.0, "v": 5.0, "q": 55.0}
        with self.assertRaisesRegex(ValueError, "duplicate candle timestamp"):
            _deduplicate_candles([candle, dict(candle)])

    def test_rejects_conflicting_duplicate_candle_timestamp(self):
        first = {"t": 0, "o": 10.0, "h": 12.0, "l": 9.0,
                 "c": 11.0, "v": 5.0, "q": 55.0}
        conflicting = {**first, "c": 10.5}
        with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
            _deduplicate_candles([first, conflicting])

    def test_rejects_invalid_interval(self):
        with self.assertRaisesRegex(ValueError, "unsupported interval"):
            download_archive("BTCUSDT", "15m", 0, 3_600_000)

    def test_rejects_unaligned_range(self):
        with self.assertRaisesRegex(ValueError, "aligned"):
            download_archive("BTCUSDT", "1h", 1, 3_600_000)


if __name__ == "__main__":
    unittest.main()
