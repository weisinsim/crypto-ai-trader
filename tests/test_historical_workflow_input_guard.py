"""Regression tests for the manually triggered historical research workflow."""
from pathlib import Path
import re
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "historical-research-data.yml"


class HistoricalWorkflowInputGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_dates_are_mapped_to_job_environment(self):
        self.assertRegex(self.text, r"START_DATE: \\$\\{\\{ inputs\\.start_date(?: \\|\\| '[^']+')? \\}\\}")
        self.assertRegex(self.text, r"END_DATE: \\$\\{\\{ inputs\\.end_date(?: \\|\\| '[^']+')? \\}\\}")

    def test_validation_precedes_every_download(self):
        validation = self.text.index("- name: Validate requested UTC date window")
        first_download = self.text.index("- name: Download closed 1h candles")
        self.assertLess(validation, first_download)
        self.assertIn("date.fromisoformat(start)", self.text)
        self.assertIn("date.fromisoformat(end)", self.text)
        self.assertIn("if end_date <= start_date:", self.text)

    def test_dates_are_not_interpolated_into_shell_commands(self):
        run_blocks = re.findall(
            r"(?ms)^      - name: .*?\n        run:.*?(?=^      - name: |\Z)",
            self.text,
        )
        for block in run_blocks:
            self.assertNotRegex(block, r"\$\{\{\s*inputs\.(?:start_date|end_date)\s*\}\}")

    def test_download_and_audits_use_validated_environment_values(self):
        self.assertGreaterEqual(self.text.count('"$START_DATE"'), 2)
        self.assertGreaterEqual(self.text.count('"$END_DATE"'), 2)
        self.assertIn('"${START_DATE}T00:00:00Z"', self.text)
        self.assertIn('"${END_DATE}T00:00:00Z"', self.text)

    def test_failed_research_still_uploads_diagnostics(self):
        upload = self.text.index("- name: Upload raw data and research outputs")
        block = self.text[upload:]
        self.assertIn("if: always()", block)
        self.assertIn("research-results/", block)


if __name__ == "__main__":
    unittest.main()
