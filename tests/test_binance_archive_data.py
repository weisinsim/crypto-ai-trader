import csv
import io
import zipfile
import unittest

from research.binance_archive_data import _read_zip, download_archive


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

    def test_rejects_invalid_interval(self):
        with self.assertRaisesRegex(ValueError, "unsupported interval"):
            download_archive("BTCUSDT", "15m", 0, 3_600_000)

    def test_rejects_unaligned_range(self):
        with self.assertRaisesRegex(ValueError, "aligned"):
            download_archive("BTCUSDT", "1h", 1, 3_600_000)


if __name__ == "__main__":
    unittest.main()
