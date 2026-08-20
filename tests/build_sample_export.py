"""Build a sample workbook for integration-level visual verification."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_export", ROOT / "custom_components/kniha_jizd/export.py"
)
assert SPEC is not None and SPEC.loader is not None
EXPORT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT_MODULE)

OUTPUT_DIRECTORY = ROOT / "test-output"
OUTPUT_DIRECTORY.mkdir(exist_ok=True)
result = EXPORT_MODULE.export_excel(
    ROOT / "tests/fixtures/raw_sample.json",
    OUTPUT_DIRECTORY / "kniha_jizd_sample.xlsx",
)
print(result)
