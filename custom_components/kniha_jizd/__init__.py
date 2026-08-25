"""Kniha jízd custom integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_MONTH,
    ATTR_DISTANCE_KM,
    ATTR_END_ADDRESS,
    ATTR_PATH,
    ATTR_PURPOSE,
    ATTR_SEGMENT_ID,
    ATTR_START_ADDRESS,
    ATTR_TRIP_TYPE,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_LATITUDE,
    CONF_COMPANY_LABEL,
    CONF_COMPANY_LONGITUDE,
    CONF_COMPANY_RADIUS,
    CONF_CLIENT_RADIUS,
    CONF_HOME_ADDRESS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_LOCATION_SETTLE_SECONDS,
    CONF_NOMINATIM_EMAIL,
    CONF_NOMINATIM_URL,
    CONF_NOMINATIM_USER_AGENT,
    CONF_OVERPASS_URL,
    CONF_PENDING_REVIEW_HOURS,
    CONF_PLACE_RADIUS,
    CONF_PRIVATE_RADIUS,
    CONF_RELEVANCE_KEYWORDS,
    CONF_RETURN_CONTEXT_HOURS,
    CONF_TRANSIENT_STOP_MINUTES,
    CONF_TRANSIENT_RADIUS,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_LATITUDE,
    DEFAULT_COMPANY_LABEL,
    DEFAULT_COMPANY_LONGITUDE,
    DEFAULT_COMPANY_RADIUS,
    DEFAULT_CLIENT_RADIUS,
    DEFAULT_EXPORT_PATH,
    DEFAULT_HOME_ADDRESS,
    DEFAULT_HOME_LATITUDE,
    DEFAULT_HOME_LONGITUDE,
    DEFAULT_HOME_RADIUS,
    DEFAULT_INSTITUTION_SEARCH_RADIUS,
    DEFAULT_LOCATION_SETTLE_SECONDS,
    DEFAULT_OVERPASS_URL,
    DEFAULT_PENDING_REVIEW_HOURS,
    DEFAULT_PLACE_RADIUS,
    DEFAULT_PRIVATE_RADIUS,
    DEFAULT_RELEVANCE_KEYWORDS,
    DEFAULT_RETURN_CONTEXT_HOURS,
    DEFAULT_TRANSIENT_STOP_MINUTES,
    DEFAULT_TRANSIENT_RADIUS,
    DOMAIN,
    SERVICE_EXPORT_EXCEL,
    SERVICE_UPDATE_TRIP,
)
from .export import export_excel
from .geocoding import NominatimGeocoder
from .manager import KnihaJizdManager
from .nearby_search import NearbyInstitutionSearcher
from .panel import async_register_panel, async_unregister_panel
from .storage import KnihaJizdRepository

_LOGGER = logging.getLogger(__name__)

EXPORT_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PATH, default=DEFAULT_EXPORT_PATH): str,
        vol.Optional(ATTR_MONTH): vol.Match(r"^\d{4}-(0[1-9]|1[0-2])$"),
    }
)
UPDATE_TRIP_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SEGMENT_ID): str,
        vol.Optional(ATTR_PURPOSE, default=""): str,
        vol.Required(ATTR_TRIP_TYPE): vol.In(["business", "private"]),
        vol.Optional(ATTR_START_ADDRESS): str,
        vol.Optional(ATTR_END_ADDRESS): str,
        vol.Optional(ATTR_DISTANCE_KM): vol.All(vol.Coerce(float), vol.Range(min=0)),
    }
)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level services."""

    # Keep optional panel HTTP modules out of config-flow imports. This lets the
    # settings form load even before the integration and its panel are running.
    from .download import KnihaJizdDownloadView
    from .history_api import KnihaJizdHistoryView
    from .map_api import KnihaJizdMapView
    from .places_api import KnihaJizdPlacesView

    hass.http.register_view(KnihaJizdDownloadView(hass))
    hass.http.register_view(KnihaJizdHistoryView(hass))
    hass.http.register_view(KnihaJizdMapView(hass))
    hass.http.register_view(KnihaJizdPlacesView(hass))

    async def _async_export_service(call: ServiceCall) -> dict[str, Any]:
        manager = _get_loaded_manager(hass)
        month = str(call.data.get(ATTR_MONTH) or dt_util.now().strftime("%Y-%m"))
        config_directory = Path(hass.config.config_dir).resolve()
        requested = Path(str(call.data[ATTR_PATH]))
        output_path = (
            requested.resolve()
            if requested.is_absolute()
            else (config_directory / requested).resolve()
        )
        if not output_path.is_relative_to(config_directory):
            raise ServiceValidationError("Export path must stay inside /config")
        if output_path.suffix.casefold() != ".xlsx":
            raise ServiceValidationError("Export path must end in .xlsx")

        manager.set_export_running(month)
        try:
            result = await hass.async_add_executor_job(
                export_excel, manager.repository.raw_path, output_path, month
            )
        except (ImportError, OSError, ValueError, TypeError) as err:
            manager.set_export_error(str(err))
            raise ServiceValidationError(f"Excel export failed: {err}") from err
        manager.set_export_success(output_path, month)
        result["download_url"] = manager.export_status["download_url"]
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_EXCEL,
        _async_export_service,
        schema=EXPORT_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _async_update_trip_service(call: ServiceCall) -> dict[str, Any]:
        manager = _get_loaded_manager(hass)
        try:
            return await manager.async_update_trip(
                str(call.data[ATTR_SEGMENT_ID]),
                str(call.data[ATTR_PURPOSE]),
                str(call.data[ATTR_TRIP_TYPE]),
                call.data.get(ATTR_START_ADDRESS),
                call.data.get(ATTR_END_ADDRESS),
                call.data.get(ATTR_DISTANCE_KM),
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_TRIP,
        _async_update_trip_service,
        schema=UPDATE_TRIP_SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kniha jízd from a config entry."""
    merged_config = {**entry.data, **entry.options}
    merged_config.setdefault(
        CONF_INSTITUTION_SEARCH_RADIUS, DEFAULT_INSTITUTION_SEARCH_RADIUS
    )
    merged_config.setdefault(
        CONF_LOCATION_SETTLE_SECONDS, DEFAULT_LOCATION_SETTLE_SECONDS
    )
    merged_config.setdefault(CONF_OVERPASS_URL, DEFAULT_OVERPASS_URL)
    merged_config.setdefault(CONF_RELEVANCE_KEYWORDS, DEFAULT_RELEVANCE_KEYWORDS)
    merged_config.setdefault(
        CONF_RETURN_CONTEXT_HOURS, DEFAULT_RETURN_CONTEXT_HOURS
    )
    merged_config.setdefault(
        CONF_TRANSIENT_STOP_MINUTES, DEFAULT_TRANSIENT_STOP_MINUTES
    )
    merged_config.setdefault(CONF_HOME_ADDRESS, DEFAULT_HOME_ADDRESS)
    merged_config.setdefault(CONF_HOME_LATITUDE, DEFAULT_HOME_LATITUDE)
    merged_config.setdefault(CONF_HOME_LONGITUDE, DEFAULT_HOME_LONGITUDE)
    merged_config.setdefault(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS)
    merged_config.setdefault(CONF_COMPANY_LATITUDE, DEFAULT_COMPANY_LATITUDE)
    merged_config.setdefault(CONF_COMPANY_LONGITUDE, DEFAULT_COMPANY_LONGITUDE)
    merged_config.setdefault(CONF_COMPANY_LABEL, DEFAULT_COMPANY_LABEL)
    legacy_radius = _legacy_radius(merged_config)
    merged_config.setdefault(CONF_HOME_RADIUS, min(legacy_radius, DEFAULT_HOME_RADIUS))
    merged_config.setdefault(
        CONF_COMPANY_RADIUS, min(legacy_radius, DEFAULT_COMPANY_RADIUS)
    )
    merged_config.setdefault(
        CONF_CLIENT_RADIUS, min(legacy_radius, DEFAULT_CLIENT_RADIUS)
    )
    merged_config.setdefault(
        CONF_PRIVATE_RADIUS, min(legacy_radius, DEFAULT_PRIVATE_RADIUS)
    )
    merged_config.setdefault(
        CONF_TRANSIENT_RADIUS, min(legacy_radius, DEFAULT_TRANSIENT_RADIUS)
    )
    merged_config.setdefault(
        CONF_PENDING_REVIEW_HOURS, DEFAULT_PENDING_REVIEW_HOURS
    )
    repository = KnihaJizdRepository(hass)
    try:
        await repository.async_initialize()
    except (OSError, ValueError) as err:
        raise ConfigEntryError(f"Cannot initialize Kniha jízd data files: {err}") from err

    session = async_get_clientsession(hass)
    geocoder = NominatimGeocoder(
        session,
        str(merged_config[CONF_NOMINATIM_URL]),
        str(merged_config[CONF_NOMINATIM_USER_AGENT]),
        str(merged_config.get(CONF_NOMINATIM_EMAIL, "")),
    )
    institution_searcher = NearbyInstitutionSearcher(
        session,
        str(merged_config[CONF_OVERPASS_URL]),
        str(merged_config[CONF_NOMINATIM_USER_AGENT]),
        str(merged_config[CONF_RELEVANCE_KEYWORDS]),
    )
    manager = KnihaJizdManager(
        hass,
        entry,
        merged_config,
        repository,
        geocoder,
        institution_searcher,
    )
    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    try:
        await async_register_panel(hass)
    except Exception:  # noqa: BLE001 - the optional panel must not break trip logging
        _LOGGER.exception("Could not register the optional Kniha jízd sidebar panel")
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate entries to configured places and current journey matching."""
    if entry.version >= 9:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    for values in (data, options):
        if not values:
            continue
        if entry.version < 2 and values.get(CONF_PLACE_RADIUS) == 150:
            values[CONF_PLACE_RADIUS] = DEFAULT_PLACE_RADIUS
        values.setdefault(
            CONF_INSTITUTION_SEARCH_RADIUS, DEFAULT_INSTITUTION_SEARCH_RADIUS
        )
        values.setdefault(
            CONF_LOCATION_SETTLE_SECONDS, DEFAULT_LOCATION_SETTLE_SECONDS
        )
        values.setdefault(CONF_OVERPASS_URL, DEFAULT_OVERPASS_URL)
        values.setdefault(CONF_RELEVANCE_KEYWORDS, DEFAULT_RELEVANCE_KEYWORDS)
        values.setdefault(CONF_RETURN_CONTEXT_HOURS, DEFAULT_RETURN_CONTEXT_HOURS)
        values.setdefault(
            CONF_TRANSIENT_STOP_MINUTES, DEFAULT_TRANSIENT_STOP_MINUTES
        )
        values.setdefault(CONF_HOME_ADDRESS, DEFAULT_HOME_ADDRESS)
        values.setdefault(CONF_HOME_LATITUDE, DEFAULT_HOME_LATITUDE)
        values.setdefault(CONF_HOME_LONGITUDE, DEFAULT_HOME_LONGITUDE)
        values.setdefault(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS)
        values.setdefault(CONF_COMPANY_LATITUDE, DEFAULT_COMPANY_LATITUDE)
        values.setdefault(CONF_COMPANY_LONGITUDE, DEFAULT_COMPANY_LONGITUDE)
        values.setdefault(CONF_COMPANY_LABEL, DEFAULT_COMPANY_LABEL)
        legacy_radius = _legacy_radius(values)
        values.setdefault(CONF_HOME_RADIUS, min(legacy_radius, DEFAULT_HOME_RADIUS))
        values.setdefault(
            CONF_COMPANY_RADIUS, min(legacy_radius, DEFAULT_COMPANY_RADIUS)
        )
        values.setdefault(
            CONF_CLIENT_RADIUS, min(legacy_radius, DEFAULT_CLIENT_RADIUS)
        )
        values.setdefault(
            CONF_PRIVATE_RADIUS, min(legacy_radius, DEFAULT_PRIVATE_RADIUS)
        )
        values.setdefault(
            CONF_TRANSIENT_RADIUS, min(legacy_radius, DEFAULT_TRANSIENT_RADIUS)
        )
        values.setdefault(
            CONF_PENDING_REVIEW_HOURS, DEFAULT_PENDING_REVIEW_HOURS
        )
        values.pop(CONF_PLACE_RADIUS, None)
        values.pop("wait_timeout", None)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=9,
    )
    return True


def _legacy_radius(values: dict[str, Any]) -> int:
    """Return a validated legacy radius for split-radius migration."""
    try:
        return max(25, int(values.get(CONF_PLACE_RADIUS, DEFAULT_PLACE_RADIUS)))
    except (TypeError, ValueError):
        return DEFAULT_PLACE_RADIUS


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Kniha jízd and preserve unfinished work."""
    manager: KnihaJizdManager = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    await manager.async_shutdown()
    async_unregister_panel(hass)
    return True


def _get_loaded_manager(hass: HomeAssistant) -> KnihaJizdManager:
    """Return the single loaded manager or raise a service error."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and isinstance(
            entry.runtime_data, KnihaJizdManager
        ):
            return entry.runtime_data
    raise ServiceValidationError("Kniha jízd has no loaded configuration entry")
