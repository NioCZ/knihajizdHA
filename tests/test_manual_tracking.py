"""Tests for manual/calculated tracking mode without car sensors."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).parents[1]

# Stub minimal Home Assistant environment
if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
    aiohttp_stub.ClientSession = object
    sys.modules["aiohttp"] = aiohttp_stub

ha_stub = sys.modules.get("homeassistant") or types.ModuleType("homeassistant")
ha_core_stub = sys.modules.get("homeassistant.core") or types.ModuleType("homeassistant.core")
ha_const_stub = sys.modules.get("homeassistant.const") or types.ModuleType("homeassistant.const")
ha_config_entries = sys.modules.get("homeassistant.config_entries") or types.ModuleType("homeassistant.config_entries")
ha_helpers = sys.modules.get("homeassistant.helpers") or types.ModuleType("homeassistant.helpers")
ha_event = sys.modules.get("homeassistant.helpers.event") or types.ModuleType("homeassistant.helpers.event")
ha_storage = sys.modules.get("homeassistant.helpers.storage") or types.ModuleType("homeassistant.helpers.storage")
ha_util = sys.modules.get("homeassistant.util") or types.ModuleType("homeassistant.util")
ha_dt = sys.modules.get("homeassistant.util.dt") or types.ModuleType("homeassistant.util.dt")

ha_core_stub.HomeAssistant = object
ha_core_stub.Event = type("Event", (), {})
ha_core_stub.State = type("State", (), {})
ha_core_stub.callback = lambda f: f
ha_const_stub.ATTR_DOMAIN = "domain"
ha_const_stub.ATTR_SERVICE = "service"
ha_const_stub.EVENT_SERVICE_REGISTERED = "service_registered"
ha_const_stub.EVENT_SERVICE_REMOVED = "service_removed"
ha_config_entries.ConfigEntry = type("ConfigEntry", (), {"entry_id": "test_entry"})
ha_event.async_track_state_change_event = MagicMock(return_value=lambda: None)
ha_dt.as_local = lambda dt: dt
ha_dt.now = lambda: datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
ha_dt.parse_datetime = lambda val: datetime.fromisoformat(val) if val else None

class _DummyStore:
    def __init__(self, *args, **kwargs) -> None:
        self.data = {}

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

ha_storage.Store = _DummyStore

sys.modules["homeassistant"] = ha_stub
sys.modules["homeassistant.core"] = ha_core_stub
sys.modules["homeassistant.const"] = ha_const_stub
sys.modules["homeassistant.config_entries"] = ha_config_entries
sys.modules["homeassistant.helpers"] = ha_helpers
sys.modules["homeassistant.helpers.event"] = ha_event
sys.modules["homeassistant.helpers.storage"] = ha_storage
sys.modules["homeassistant.util"] = ha_util
sys.modules["homeassistant.util.dt"] = ha_dt

pkg = types.ModuleType("custom_components.kniha_jizd")
pkg.__path__ = [str(ROOT / "custom_components/kniha_jizd")]
sys.modules.setdefault("custom_components.kniha_jizd", pkg)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(
        f"custom_components.kniha_jizd.{name}", path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"custom_components.kniha_jizd.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


const_mod = _load_module("const", ROOT / "custom_components/kniha_jizd/const.py")
address_rules_mod = _load_module(
    "address_rules", ROOT / "custom_components/kniha_jizd/address_rules.py"
)
routing_mod = _load_module("routing", ROOT / "custom_components/kniha_jizd/routing.py")
storage_mod = _load_module("storage", ROOT / "custom_components/kniha_jizd/storage.py")
workflow_logic_mod = _load_module(
    "workflow_logic", ROOT / "custom_components/kniha_jizd/workflow_logic.py"
)
trip_context_mod = _load_module(
    "trip_context", ROOT / "custom_components/kniha_jizd/trip_context.py"
)
journey_chain_mod = _load_module(
    "journey_chain", ROOT / "custom_components/kniha_jizd/journey_chain.py"
)
location_logic_mod = _load_module(
    "location_logic", ROOT / "custom_components/kniha_jizd/location_logic.py"
)
odometer_logic_mod = _load_module(
    "odometer_logic", ROOT / "custom_components/kniha_jizd/odometer_logic.py"
)
input_parsing_mod = _load_module(
    "input_parsing", ROOT / "custom_components/kniha_jizd/input_parsing.py"
)
nearby_search_mod = _load_module(
    "nearby_search", ROOT / "custom_components/kniha_jizd/nearby_search.py"
)
geocoding_mod = _load_module(
    "geocoding", ROOT / "custom_components/kniha_jizd/geocoding.py"
)
manager_mod = _load_module("manager", ROOT / "custom_components/kniha_jizd/manager.py")
REAL_MANAGER_CLASS = manager_mod.KnihaJizdManager


def _make_state(state: str, attributes: dict | None = None) -> MagicMock:
    mock = MagicMock()
    mock.state = state
    mock.attributes = attributes or {}
    mock.last_updated = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    return mock


class ManualTrackingModeTest(unittest.IsolatedAsyncioTestCase):
    """Test logbook operation in manual_calculated mode without car sensors."""

    async def asyncSetUp(self) -> None:
        self.hass = MagicMock()
        self.hass.async_add_executor_job = lambda func, *args: asyncio.to_thread(
            func, *args
        )
        self.hass.states.get.return_value = None
        self.hass.services.has_service.return_value = True
        self.hass.services.async_call = AsyncMock()
        self.hass.bus.async_listen.return_value = lambda: None
        self.hass.async_create_task = lambda coro, name=None: asyncio.create_task(coro)

        self.entry = MagicMock()
        self.entry.entry_id = "test_entry"

        self.temp_dir = tempfile.TemporaryDirectory()
        self.hass.config.config_dir = self.temp_dir.name
        self.hass.config.path = lambda *args: str(Path(self.temp_dir.name, *args))
        self.repository = storage_mod.KnihaJizdRepository(self.hass)
        await self.repository.async_initialize()

        self.geocoder = MagicMock()
        self.geocoder.async_reverse = AsyncMock(return_value=None)
        self.institution_searcher = MagicMock()
        self.institution_searcher.async_search = AsyncMock(return_value=[])
        self.institution_searcher.async_search_with_diagnostics = AsyncMock(
            return_value=([], {})
        )

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_manager(
        self,
        tracking_mode: str = "manual_calculated",
        initial_odometer: float = 120000.0,
    ):
        config = {
            const_mod.CONF_TRACKING_MODE: tracking_mode,
            const_mod.CONF_TRIGGER_ENTITY: "binary_sensor.test_trigger",
            const_mod.CONF_GPS_ENTITY: "device_tracker.test_phone",
            const_mod.CONF_ADDRESS_ENTITY: "sensor.test_address",
            const_mod.CONF_ODOMETER_ENTITY: "sensor.skoda_odometer",
            const_mod.CONF_INITIAL_ODOMETER: initial_odometer,
            const_mod.CONF_ROAD_FACTOR: 1.27,
            const_mod.CONF_NOTIFY_SERVICE: "mobile_app_phone",
            const_mod.CONF_LOCATION_SETTLE_SECONDS: 0,
            const_mod.CONF_RETURN_CONTEXT_HOURS: 18,
            const_mod.CONF_TRANSIENT_STOP_MINUTES: 10,
            const_mod.CONF_PENDING_REVIEW_HOURS: 24,
            const_mod.CONF_HOME_RADIUS: 300,
            const_mod.CONF_COMPANY_RADIUS: 300,
            const_mod.CONF_CLIENT_RADIUS: 500,
            const_mod.CONF_PRIVATE_RADIUS: 250,
            const_mod.CONF_TRANSIENT_RADIUS: 200,
            const_mod.CONF_INSTITUTION_SEARCH_RADIUS: 3000,
        }
        return REAL_MANAGER_CLASS(
            self.hass,
            self.entry,
            config,
            self.repository,
            self.geocoder,
            self.institution_searcher,
        )

    async def test_manual_mode_readiness_without_car_odometer(self) -> None:
        """In manual_calculated mode, missing odometer sensor must not block readiness."""
        trigger_state = _make_state("off")
        gps_state = _make_state(
            "not_home", {"latitude": 50.08, "longitude": 14.43}
        )
        address_state = _make_state("Praha")

        def get_state(entity_id):
            if entity_id == "binary_sensor.test_trigger":
                return trigger_state
            if entity_id == "device_tracker.test_phone":
                return gps_state
            if entity_id == "sensor.test_address":
                return address_state
            return None  # No odometer sensor!

        self.hass.states.get.side_effect = get_state

        manager = self._create_manager("manual_calculated", initial_odometer=125000.0)
        await manager.async_start()

        diag = manager.diagnostics
        self.assertTrue(diag["ready"])
        self.assertTrue(diag["odometer_ok"])
        self.assertEqual(diag["tracking_mode"], "manual_calculated")
        self.assertEqual(diag["virtual_odometer_km"], 125000.0)

    async def test_api_mode_readiness_requires_odometer_sensor(self) -> None:
        """In api mode, missing odometer sensor makes odometer_ok False."""
        trigger_state = _make_state("off")
        gps_state = _make_state(
            "not_home", {"latitude": 50.08, "longitude": 14.43}
        )
        address_state = _make_state("Praha")

        def get_state(entity_id):
            if entity_id == "binary_sensor.test_trigger":
                return trigger_state
            if entity_id == "device_tracker.test_phone":
                return gps_state
            if entity_id == "sensor.test_address":
                return address_state
            return None  # No odometer sensor

        self.hass.states.get.side_effect = get_state

        manager = self._create_manager("api")
        await manager.async_start()

        diag = manager.diagnostics
        self.assertFalse(diag["odometer_ok"])
        self.assertFalse(diag["ready"])
        self.assertEqual(diag["tracking_mode"], "api")

    async def test_manual_mode_trip_lifecycle_and_virtual_odometer(self) -> None:
        """A completed trip in manual mode calculates distance and advances virtual odometer."""
        # 1. Start location: Prague (50.0755, 14.4378)
        start_gps = _make_state(
            "not_home", {"latitude": 50.0755, "longitude": 14.4378}
        )
        start_addr = _make_state("Praha, Wilsonova")

        self.hass.states.get.side_effect = lambda entity: (
            start_gps
            if entity == "device_tracker.test_phone"
            else start_addr
            if entity == "sensor.test_address"
            else None
        )

        manager = self._create_manager("manual_calculated", initial_odometer=100000.0)
        await manager.async_start()

        # Start trip
        start_res = await manager.async_start_trip()
        self.assertEqual(start_res["status"], "started")
        active = manager._active
        self.assertIsNotNone(active)
        self.assertEqual(active["start_odometer_km"], 100000.0)
        self.assertIsNone(active["validation_error"])

        # 2. End location: Pruhonice (~15 km away, 50.0012, 14.5612)
        end_gps = _make_state(
            "not_home", {"latitude": 50.0012, "longitude": 14.5612}
        )
        end_addr = _make_state("Průhonice, Květnové náměstí")

        self.hass.states.get.side_effect = lambda entity: (
            end_gps
            if entity == "device_tracker.test_phone"
            else end_addr
            if entity == "sensor.test_address"
            else None
        )

        # End trip
        end_res = await manager.async_end_trip()
        self.assertEqual(end_res["status"], "ended")

        # Let async completion tasks finish
        await asyncio.sleep(0.05)

        # Check closing or pending segment
        seg_id = end_res["segment_id"]
        seg = manager._find_runtime_segment(seg_id)
        self.assertIsNotNone(seg)
        self.assertTrue(seg["odometer_ready"])
        self.assertGreater(seg["distance_km"], 10)
        self.assertGreater(seg["end_odometer_km"], 100010.0)
        self.assertGreaterEqual(manager._virtual_odometer_km, seg["end_odometer_km"])

    async def test_set_odometer_service_reconciles_day(self) -> None:
        """Calling set_odometer adjusts the virtual odometer and reconciles segments."""
        manager = self._create_manager("manual_calculated", initial_odometer=50000.0)
        await manager.async_start()

        # Add two trips on the same day
        await manager.async_add_trip(
            date_str="2026-10-31",
            trip_type="business",
            purpose="Klient A",
            start_address="Praha",
            end_address="Kladno",
            distance_km=30,
        )
        await manager.async_add_trip(
            date_str="2026-10-31",
            trip_type="business",
            purpose="Klient B",
            start_address="Kladno",
            end_address="Slaný",
            distance_km=20,
        )

        # Initial total is 50 km, end odometer 50050.0
        self.assertEqual(manager._virtual_odometer_km, 50050.0)

        # User checks car dashboard: real odometer is 50060.0 (actual driven 60 km, +10 km delta)
        result = await manager.async_set_odometer(
            odometer_km=50060.0, date_str="2026-10-31"
        )
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(manager._virtual_odometer_km, 50060.0)

        # Check history rows: sum of whole km must equal exactly 60 km
        history = await manager.repository.async_get_history(
            "2026-10", "2026-10-31"
        )
        rows = history["rows"]
        self.assertEqual(len(rows), 2)
        total_reconciled = sum(row["distance_km"] for row in rows)
        self.assertEqual(total_reconciled, 60)

    async def test_pending_trips_diagnostics_and_attention_count(self) -> None:
        """Unfinished segments across any date must be visible in pending_trips diagnostics."""
        manager = self._create_manager("manual_calculated", initial_odometer=100000.0)
        await manager.async_start()

        # Simulate stuck segment from a past date waiting in _closing
        stuck_seg_1 = {
            "id": "stuck-1",
            "date": "2026-10-15",
            "started_at": "2026-10-15T08:00:00+02:00",
            "ended_at": "2026-10-15T08:30:00+02:00",
            "start_address": "Praha",
            "end_address": "Benešov",
            "start_latitude": 50.08,
            "start_longitude": 14.43,
            "end_latitude": 49.78,
            "end_longitude": 14.69,
            "start_odometer_km": 100000.0,
            "end_odometer_km": None,
            "distance_km": None,
            "odometer_ready": False,
            "trip_type": "unclassified",
            "purpose": None,
        }
        # Simulate segment waiting for classification in _pending
        stuck_seg_2 = {
            "id": "stuck-2",
            "date": "2026-10-20",
            "started_at": "2026-10-20T10:00:00+02:00",
            "ended_at": "2026-10-20T10:45:00+02:00",
            "start_address": "Benešov",
            "end_address": "Tábor",
            "start_latitude": 49.78,
            "start_longitude": 14.69,
            "end_latitude": 49.41,
            "end_longitude": 14.66,
            "start_odometer_km": 100040.0,
            "end_odometer_km": 100085.0,
            "distance_km": 45,
            "odometer_ready": True,
            "trip_type": "unclassified",
            "purpose": None,
        }
        manager._closing["stuck-1"] = stuck_seg_1
        manager._pending["stuck-2"] = stuck_seg_2

        diag = manager.diagnostics
        self.assertEqual(diag["attention_count"], 2)
        pending = diag["pending_trips"]
        self.assertEqual(len(pending), 2)
        ids = {p["id"] for p in pending}
        self.assertEqual(ids, {"stuck-1", "stuck-2"})

    async def test_process_pending_trips_resolves_stuck_trips(self) -> None:
        """async_process_pending_trips calculates distance and marks segments odometer_ready."""
        manager = self._create_manager("manual_calculated", initial_odometer=100000.0)
        await manager.async_start()

        # Add a stuck segment with GPS coordinates but no distance/odometer
        manager._closing["stuck-trip"] = {
            "id": "stuck-trip",
            "date": "2026-10-15",
            "started_at": "2026-10-15T08:00:00+02:00",
            "ended_at": "2026-10-15T08:30:00+02:00",
            "start_address": "Praha",
            "end_address": "Průhonice",
            "start_latitude": 50.0755,
            "start_longitude": 14.4378,
            "end_latitude": 50.0012,
            "end_longitude": 14.5612,
            "start_odometer_km": 100000.0,
            "end_odometer_km": None,
            "distance_km": None,
            "odometer_ready": False,
            "trip_type": "unclassified",
            "purpose": None,
        }

        result = await manager.async_process_pending_trips()
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["processed_count"], 1)

        seg = manager._find_runtime_segment("stuck-trip")
        self.assertIsNotNone(seg)
        self.assertTrue(seg["odometer_ready"])
        self.assertGreater(seg["distance_km"], 10)
        self.assertIsNotNone(seg["end_odometer_km"])

    async def test_discard_trip(self) -> None:
        """async_discard_trip removes unwanted/phantom trips from runtime."""
        manager = self._create_manager("manual_calculated", initial_odometer=100000.0)
        await manager.async_start()

        manager._closing["phantom-1"] = {"id": "phantom-1"}
        manager._pending["phantom-2"] = {"id": "phantom-2"}

        res1 = await manager.async_discard_trip("phantom-1")
        self.assertEqual(res1["status"], "discarded")
        self.assertNotIn("phantom-1", manager._closing)

        res2 = await manager.async_discard_trip("phantom-2")
        self.assertEqual(res2["status"], "discarded")
        self.assertNotIn("phantom-2", manager._pending)

        res3 = await manager.async_discard_trip("non-existent")
        self.assertEqual(res3["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
