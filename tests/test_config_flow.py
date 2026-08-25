"""Regression tests for the integration options flow."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "kniha_jizd"


class _Marker:
    def __init__(self, key, *, default=None, description=None) -> None:
        self.schema = key
        self.default = default
        self.description = description or {}

    def __hash__(self) -> int:
        return hash((type(self), self.schema))

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and self.schema == other.schema


class _Schema:
    def __init__(self, schema) -> None:
        self.schema = schema


class _Validator:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


class _Selector:
    def __init__(self, config=None) -> None:
        self.config = config or {}


class _NumberSelector(_Selector):
    pass


class _ConfigEntry:
    def __init__(self, data: dict, options: dict) -> None:
        self.data = data
        self.options = options


class _ConfigFlow:
    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__()


class _OptionsFlow:
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


def _load_config_flow():
    """Load config_flow with a minimal HA surface and no entry injection."""
    package_name = "_kniha_jizd_config_flow_test"
    package = ModuleType(package_name)
    package.__path__ = [str(COMPONENT)]

    homeassistant = ModuleType("homeassistant")
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = _ConfigEntry
    config_entries.ConfigFlow = _ConfigFlow
    config_entries.OptionsFlow = _OptionsFlow
    homeassistant.config_entries = config_entries

    core = ModuleType("homeassistant.core")
    core.callback = lambda function: function
    helpers = ModuleType("homeassistant.helpers")
    selector = ModuleType("homeassistant.helpers.selector")
    selector.EntitySelector = _Selector
    selector.EntitySelectorConfig = lambda **kwargs: kwargs
    selector.TextSelector = _Selector
    selector.NumberSelector = _NumberSelector
    selector.NumberSelectorConfig = lambda **kwargs: kwargs
    selector.NumberSelectorMode = type("NumberSelectorMode", (), {"BOX": "box"})
    helpers.selector = selector
    voluptuous = ModuleType("voluptuous")
    voluptuous.Schema = _Schema
    voluptuous.Marker = _Marker
    voluptuous.Required = _Marker
    voluptuous.Optional = _Marker
    voluptuous.All = _Validator
    voluptuous.Any = _Validator
    voluptuous.Coerce = _Validator
    voluptuous.Range = _Validator

    stubs = {
        package_name: package,
        "homeassistant": homeassistant,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.selector": selector,
        "voluptuous": voluptuous,
    }
    module_name = f"{package_name}.config_flow"
    spec = importlib.util.spec_from_file_location(
        module_name, COMPONENT / "config_flow.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module


class ConfigFlowTests(unittest.TestCase):
    def test_coordinates_use_serializable_number_selectors(self) -> None:
        """HA 2026.8 cannot serialize Any('', All(float, Range(...)))."""
        module = _load_config_flow()

        schema = module._schema(
            {
                module.CONF_HOME_LATITUDE: "49.123456",
                module.CONF_HOME_LONGITUDE: "",
            }
        )
        fields = {marker.schema: (marker, value) for marker, value in schema.schema.items()}

        for key in (
            module.CONF_HOME_LATITUDE,
            module.CONF_HOME_LONGITUDE,
            module.CONF_COMPANY_LATITUDE,
            module.CONF_COMPANY_LONGITUDE,
        ):
            marker, validator = fields[key]
            self.assertIsInstance(validator, _NumberSelector)
            self.assertEqual("any", validator.config["step"])
            self.assertEqual("box", validator.config["mode"])
            self.assertNotIsInstance(validator, _Validator)
        self.assertEqual(
            49.123456,
            fields[module.CONF_HOME_LATITUDE][0].description["suggested_value"],
        )
        self.assertNotIn(
            "suggested_value", fields[module.CONF_HOME_LONGITUDE][0].description
        )

    def test_options_flow_keeps_entry_on_ha_without_injection(self) -> None:
        """Opening options must not depend on a newer HA base-class property."""
        module = _load_config_flow()
        entry = _ConfigEntry(
            {"gps_entity": "device_tracker.car", "wait_timeout": 300},
            {"wait_timeout": 420},
        )
        flow = module.KnihaJizdConfigFlow.async_get_options_flow(entry)
        module._schema = lambda defaults: defaults

        result = asyncio.run(flow.async_step_init())

        self.assertEqual("form", result["type"])
        self.assertEqual("device_tracker.car", result["data_schema"]["gps_entity"])
        self.assertEqual(420, result["data_schema"]["wait_timeout"])

    def test_schema_has_no_odometer_timeout(self) -> None:
        """Kilometres must wait for a trustworthy counter update without a limit."""
        module = _load_config_flow()

        schema = module._schema({"wait_timeout": 600})
        fields = {marker.schema for marker in schema.schema}

        self.assertNotIn("wait_timeout", fields)

    def test_options_flow_persists_cleared_coordinates(self) -> None:
        """An omitted optional number must override an older saved coordinate."""
        module = _load_config_flow()
        entry = _ConfigEntry({}, {})
        flow = module.KnihaJizdConfigFlow.async_get_options_flow(entry)

        result = asyncio.run(flow.async_step_init({"location_settle_seconds": 60}))

        self.assertEqual("create_entry", result["type"])
        for key in (
            module.CONF_HOME_LATITUDE,
            module.CONF_HOME_LONGITUDE,
            module.CONF_COMPANY_LATITUDE,
            module.CONF_COMPANY_LONGITUDE,
        ):
            self.assertEqual("", result["data"][key])


if __name__ == "__main__":
    unittest.main()
