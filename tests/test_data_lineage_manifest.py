import hashlib
import tempfile
import unittest
from pathlib import Path

from research.run_backtest import source_file_manifest


class SourceFileManifestTests(unittest.TestCase):
    def test_fingerprints_exact_input_bytes_without_claiming_authenticity(self):
        payload = b"timestamp,open,high,low,close\n1,1,2,0,1\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candles.csv"
            path.write_bytes(payload)
            manifest = source_file_manifest(str(path))

        self.assertTrue(manifest["provided"])
        self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(manifest["size_bytes"], len(payload))
        self.assertFalse(manifest["source_authenticity_verified"])

    def test_missing_optional_file_is_explicit(self):
        self.assertEqual(
            source_file_manifest(None),
            {"provided": False, "sha256": None, "size_bytes": None},
        )


if __name__ == "__main__":
    unittest.main()
