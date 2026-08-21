"""Import and smoke tests for the new Home Assistant entity platforms."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]


class _Entity:
    """Small stand-in for the HA Entity base class."""

    def async_on_remove(self, callback):
        """Accept an unsubscribe callback."""
        self._unsubscribe = callback


class _SensorEntity(_Entity):
    """Stand-in sensor entity."""


class _BinarySensorEntity(_Entity):
    """Stand-in binary sensor entity."""


class _ButtonEntity(_Entity):
    """Stand-in button entity."""


class _ConfigEntry:
    """Stand-in config entry."""

    entry_id = "entry"


class _DeviceInfo(dict):
    """Accept DeviceInfo keyword fields."""

    def __init__(self, **kwargs):
        super().__init__(kwargs)


class _Manager:
    """Provide the properties consumed by diagnostic entities."""

    status = "idle"
    diagnostics = {
        "ready": True,
        "closing_count": 1,
        "pending_count": 2,
        "transient_count": 3,
    }
    statistics = {
        "today_business_km": 12.5,
        "today_private_km": 3.0,
        "today_segments": 2,
        "segments_total": 10,
        "last_segment": {"distance_km": 4.5, "purpose": "Laboratoř"},
    }
    export_status = {"state": "ready", "download_url": "/download"}

    def async_add_listener(self, listener):
        """Return a no-op unsubscriber."""
        return lambda: None


def _module(name: str) -> types.ModuleType:
    module = sys.modules.get(name) or types.ModuleType(name)
    sys.modules[name] = module
    return module


homeassistant = _module("homeassistant")
components = _module("homeassistant.components")
sensor_component = _module("homeassistant.components.sensor")
sensor_component.SensorEntity = _SensorEntity
binary_component = _module("homeassistant.components.binary_sensor")
binary_component.BinarySensorEntity = _BinarySensorEntity
button_component = _module("homeassistant.components.button")
button_component.ButtonEntity = _ButtonEntity
frontend_component = _module("homeassistant.components.frontend")
panel_custom_component = _module("homeassistant.components.panel_custom")
http_component = _module("homeassistant.components.http")
http_component.StaticPathConfig = object
components.frontend = frontend_component
components.panel_custom = panel_custom_component
config_entries = _module("homeassistant.config_entries")
config_entries.ConfigEntry = _ConfigEntry
core = _module("homeassistant.core")
core.HomeAssistant = object
helpers = _module("homeassistant.helpers")
entity_helper = _module("homeassistant.helpers.entity")
entity_helper.DeviceInfo = _DeviceInfo
entity_helper.Entity = _Entity
entity_platform = _module("homeassistant.helpers.entity_platform")
entity_platform.AddEntitiesCallback = object

package = _module("custom_components.kniha_jizd")
package.__path__ = [str(ROOT / "custom_components/kniha_jizd")]
manager_module = _module("custom_components.kniha_jizd.manager")
manager_module.KnihaJizdManager = _Manager


def _load(module_name: str, filename: str):
    specification = importlib.util.spec_from_file_location(
        module_name, ROOT / "custom_components/kniha_jizd" / filename
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[module_name] = module
    specification.loader.exec_module(module)
    return module


if "custom_components.kniha_jizd.const" not in sys.modules:
    _load("custom_components.kniha_jizd.const", "const.py")
_load("custom_components.kniha_jizd.entity", "entity.py")
SENSOR_MODULE = _load("custom_components.kniha_jizd.sensor", "sensor.py")
BINARY_MODULE = _load(
    "custom_components.kniha_jizd.binary_sensor", "binary_sensor.py"
)
BUTTON_MODULE = _load("custom_components.kniha_jizd.button", "button.py")
PANEL_MODULE = _load("custom_components.kniha_jizd.panel", "panel.py")


class PlatformEntityTest(unittest.TestCase):
    """Verify the platform classes import and expose expected values."""

    def test_sensor_values_and_mro(self) -> None:
        """Instantiate representative sensor classes."""
        manager = _Manager()
        entry = _ConfigEntry()
        status = SENSOR_MODULE.KnihaJizdStatusSensor(manager, entry)
        business = SENSOR_MODULE.KnihaJizdStatisticSensor(
            manager, entry, "today_business_km", "dnes služební", "km"
        )
        pending = SENSOR_MODULE.KnihaJizdPendingSensor(manager, entry)
        last_trip = SENSOR_MODULE.KnihaJizdLastTripSensor(manager, entry)

        self.assertEqual(status.native_value, "idle")
        self.assertEqual(business.native_value, 12.5)
        self.assertEqual(pending.native_value, 6)
        self.assertEqual(last_trip.native_value, 4.5)

    def test_binary_sensor_button_and_export_sensor_import(self) -> None:
        """Instantiate the remaining platform types."""
        manager = _Manager()
        entry = _ConfigEntry()
        ready = BINARY_MODULE.KnihaJizdReadyBinarySensor(manager, entry)
        button = BUTTON_MODULE.KnihaJizdExportButton(manager, entry)
        export = SENSOR_MODULE.KnihaJizdExportSensor(manager, entry)

        self.assertTrue(ready.is_on)
        self.assertEqual(button._kind, "export_button")
        self.assertEqual(export.native_value, "ready")

    def test_panel_exists_falls_back_on_older_home_assistant(self) -> None:
        """Use frontend_panels when the newer helper is unavailable."""
        hass = types.SimpleNamespace(
            data={"frontend_panels": {"kniha-jizd": object()}}
        )

        self.assertTrue(PANEL_MODULE._panel_exists(hass))
        hass.data["frontend_panels"].clear()
        self.assertFalse(PANEL_MODULE._panel_exists(hass))


if __name__ == "__main__":
    unittest.main()
