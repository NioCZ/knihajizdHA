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
    helpers.selector = selector
    voluptuous = ModuleType("voluptuous")

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


if __name__ == "__main__":
    unittest.main()
