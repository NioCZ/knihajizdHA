"""Tests for road routing and distance estimation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
    aiohttp_stub.ClientSession = object
    sys.modules["aiohttp"] = aiohttp_stub

package = types.ModuleType("custom_components.kniha_jizd")
package.__path__ = [str(ROOT / "custom_components/kniha_jizd")]
sys.modules.setdefault("custom_components.kniha_jizd", package)

address_rules_spec = importlib.util.spec_from_file_location(
    "custom_components.kniha_jizd.address_rules",
    ROOT / "custom_components/kniha_jizd/address_rules.py",
)
assert address_rules_spec is not None and address_rules_spec.loader is not None
address_rules_module = importlib.util.module_from_spec(address_rules_spec)
sys.modules["custom_components.kniha_jizd.address_rules"] = address_rules_module
address_rules_spec.loader.exec_module(address_rules_module)

SPEC = importlib.util.spec_from_file_location(
    "custom_components.kniha_jizd.routing",
    ROOT / "custom_components/kniha_jizd/routing.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules["custom_components.kniha_jizd.routing"] = MODULE
SPEC.loader.exec_module(MODULE)


class RoutingLogicTest(unittest.TestCase):
    """Test road distance estimation and OSRM fallback."""

    def test_estimate_road_distance_valid_coordinates(self) -> None:
        """Estimate road distance between Prague and Brno."""
        # Prague: 50.0755, 14.4378; Brno: 49.1951, 16.6068 (~185 km crow-flies, ~205-210 km driving)
        dist = MODULE.estimate_road_distance_km(
            50.0755, 14.4378, 49.1951, 16.6068, road_factor=1.27
        )
        self.assertIsNotNone(dist)
        self.assertGreater(dist, 200.0)
        self.assertLess(dist, 260.0)

    def test_estimate_road_distance_zero_for_same_point(self) -> None:
        """Distance between same point should be 0.0."""
        dist = MODULE.estimate_road_distance_km(50.0, 14.0, 50.0, 14.0)
        self.assertEqual(dist, 0.0)

    def test_estimate_road_distance_invalid_coordinates(self) -> None:
        """None coordinates should return None."""
        self.assertIsNone(MODULE.estimate_road_distance_km(None, 14.0, 50.0, 14.0))
        self.assertIsNone(MODULE.estimate_road_distance_km("invalid", 14.0, 50.0, 14.0))
