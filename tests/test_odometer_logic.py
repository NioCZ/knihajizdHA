"""Tests for cloud odometer completion signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_odometer_logic",
    ROOT / "custom_components/kniha_jizd/odometer_logic.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OdometerLogicTest(unittest.TestCase):
    """Require both a new timestamp and increased counter when possible."""

    def test_accepts_post_disconnect_update_and_increase(self) -> None:
        """Use the first cloud value satisfying both primary signals."""
        disconnected = datetime(2026, 8, 21, 10, tzinfo=UTC)

        signal = MODULE.odometer_update_signal(
            disconnected,
            1000.0,
            disconnected + timedelta(seconds=30),
            1012.0,
        )

        self.assertEqual(signal, "post_disconnect_update_and_increase")

    def test_rejects_old_timestamp_or_unchanged_value(self) -> None:
        """Do not finish on a stale cloud state or attribute-only refresh."""
        disconnected = datetime(2026, 8, 21, 10, tzinfo=UTC)

        self.assertIsNone(
            MODULE.odometer_update_signal(
                disconnected, 1000.0, disconnected, 1012.0
            )
        )
        self.assertIsNone(
            MODULE.odometer_update_signal(
                disconnected,
                1000.0,
                disconnected + timedelta(seconds=30),
                1000.0,
            )
        )

    def test_timestamp_is_usable_when_start_counter_is_missing(self) -> None:
        """Keep timestamp-only recovery available without a start value."""
        disconnected = datetime(2026, 8, 21, 10, tzinfo=UTC)

        signal = MODULE.odometer_update_signal(
            disconnected,
            None,
            disconnected + timedelta(seconds=30),
            1012.0,
        )

        self.assertEqual(signal, "post_disconnect_update")


if __name__ == "__main__":
    unittest.main()
