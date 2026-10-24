import tempfile
import unittest
from pathlib import Path

from validation.diagnose_funding_alignment import diagnose


class FundingAlignmentDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _csv(self, name, body):
        path = self.root / name
        path.write_text(body, encoding='utf-8')
        return path

    def test_reports_five_hour_offset_without_shifting_timestamps(self):
        candles = self._csv('candles.csv',
            'timestamp,open,high,low,close,volume\n'
            '2025-01-01T00:00:00Z,1,1,1,1,1\n'
            '2025-01-01T01:00:00Z,1,1,1,1,1\n')
        funding = self._csv('funding.csv',
            'funding_time_ms,funding_rate\n'
            '1735711200000,0.0001\n')
        result = diagnose(candles, funding)
        self.assertEqual(result['funding_timestamp_field'], 'funding_time_ms')
        self.assertEqual(result['funding_events_outside_candle_timestamp_range'], 1)
        self.assertEqual(result['nearest_delta_examples_ms'], [18000000])
        self.assertIn('no timestamps were shifted', result['notice'])
        self.assertEqual(result['candle_first_10_utc'][0], '2025-01-01T00:00:00Z')
        self.assertEqual(result['funding_first_10_utc'][0], '2025-01-01T06:00:00Z')
        self.assertEqual(result['funding_mod_hour_ms_counts'], {'0': 1})

    def test_accepts_epoch_milliseconds_and_iso_utc(self):
        candles = self._csv('candles.csv',
            't,open,high,low,close,volume\n'
            '1735689600000,1,1,1,1,1\n')
        funding = self._csv('funding.csv',
            'timestamp,funding_rate\n'
            '2025-01-01T00:00:00Z,0.0001\n')
        result = diagnose(candles, funding)
        self.assertEqual(result['nearest_delta_examples_ms'], [0])
        self.assertEqual(result['funding_events_outside_candle_timestamp_range'], 0)

    def test_converts_binance_close_timestamp_to_open_grid(self):
        # 01:00 candle close is 01:59:59.999; its corresponding open is 01:00.
        candles = self._csv('candles.csv',
            'timestamp,open,high,low,close,volume\n'
            '2025-01-01T01:59:59.999Z,1,1,1,1,1\n')
        funding = self._csv('funding.csv',
            'timestamp,funding_rate\n'
            '2025-01-01T01:00:00Z,0.0001\n')
        result = diagnose(candles, funding, candle_timestamp_kind='close', interval_ms=3600000)
        self.assertEqual(result['nearest_delta_examples_ms'], [0])
        self.assertTrue(result['candle_timestamps_converted_to_open_grid'])

    def test_rejects_timezone_naive_iso_timestamp(self):
        candles = self._csv('candles.csv', 'timestamp\n2025-01-01T00:00:00\n')
        funding = self._csv('funding.csv', 'timestamp\n2025-01-01T00:00:00Z\n')
        with self.assertRaisesRegex(ValueError, 'must include timezone'):
            diagnose(candles, funding)


if __name__ == '__main__':
    unittest.main()
