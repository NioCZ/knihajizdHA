"""Tests for the executor-safe Excel exporter."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_export", ROOT / "custom_components/kniha_jizd/export.py"
)
assert SPEC is not None and SPEC.loader is not None
EXPORT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT_MODULE)


class ExportExcelTest(unittest.TestCase):
    """Verify workbook structure and representative aggregations."""

    def test_two_sheet_export_and_daily_totals(self) -> None:
        """Export a representative day and inspect typed workbook values."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            output_path = Path(temporary_directory) / "kniha_jizd.xlsx"
            result = EXPORT_MODULE.export_excel(
                ROOT / "tests/fixtures/raw_sample.json", output_path, "2026-08"
            )

            self.assertEqual(result["month"], "2026-08")
            self.assertEqual(result["days"], 1)
            self.assertEqual(result["segments"], 3)
            workbook = load_workbook(output_path, data_only=False)
            self.assertEqual(workbook.sheetnames, ["Kniha jízd", "Raw data"])

            summary = workbook["Kniha jízd"]
            self.assertEqual(
                [cell.value for cell in summary[1]], EXPORT_MODULE.SUMMARY_COLUMNS
            )
            self.assertEqual(summary["F2"].value, 32.65)
            self.assertEqual(summary["G2"].value, 8.6)
            self.assertIn("Průmyslová 12", summary["C2"].value)
            self.assertIn("Vinohradská 50", summary["C2"].value)

            raw = workbook["Raw data"]
            headers = [cell.value for cell in raw[1]]
            self.assertEqual(raw.max_row, 4)
            self.assertIn("odometer_wait_timed_out", headers)
            self.assertIn("map_candidates", headers)
            self.assertIn("candidate_search_radius_m", headers)

    def test_month_filter_excludes_other_periods(self) -> None:
        """Keep both workbook sheets inside the requested calendar month."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            output_path = Path(temporary_directory) / "kniha_jizd_2026-07.xlsx"
            result = EXPORT_MODULE.export_excel(
                ROOT / "tests/fixtures/raw_sample.json", output_path, "2026-07"
            )

            self.assertEqual(result["days"], 0)
            self.assertEqual(result["segments"], 0)
            workbook = load_workbook(output_path, data_only=False)
            self.assertEqual(workbook["Kniha jízd"].max_row, 1)
            self.assertEqual(workbook["Raw data"].max_row, 1)

    def test_month_filter_rejects_invalid_value(self) -> None:
        """Reject ambiguous dates before creating a misleading report."""
        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            EXPORT_MODULE.export_excel(
                ROOT / "tests/fixtures/raw_sample.json",
                ROOT / "test-output/invalid.xlsx",
                "08/2026",
            )


if __name__ == "__main__":
    unittest.main()
