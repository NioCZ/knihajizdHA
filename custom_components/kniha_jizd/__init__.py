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
    ATTR_PATH,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_NOMINATIM_EMAIL,
    CONF_NOMINATIM_URL,
    CONF_NOMINATIM_USER_AGENT,
    CONF_OVERPASS_URL,
    CONF_PLACE_RADIUS,
    CONF_RELEVANCE_KEYWORDS,
    DEFAULT_EXPORT_PATH,
    DEFAULT_INSTITUTION_SEARCH_RADIUS,
    DEFAULT_OVERPASS_URL,
    DEFAULT_PLACE_RADIUS,
    DEFAULT_RELEVANCE_KEYWORDS,
    DOMAIN,
    SERVICE_EXPORT_EXCEL,
)
from .download import KnihaJizdDownloadView
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
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level services."""

    hass.http.register_view(KnihaJizdDownloadView(hass))

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
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Kniha jízd from a config entry."""
    merged_config = {**entry.data, **entry.options}
    merged_config.setdefault(
        CONF_INSTITUTION_SEARCH_RADIUS, DEFAULT_INSTITUTION_SEARCH_RADIUS
    )
    merged_config.setdefault(CONF_OVERPASS_URL, DEFAULT_OVERPASS_URL)
    merged_config.setdefault(CONF_RELEVANCE_KEYWORDS, DEFAULT_RELEVANCE_KEYWORDS)
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
    """Migrate version 1 entries to institution-aware location matching."""
    if entry.version >= 2:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    for values in (data, options):
        if not values:
            continue
        if values.get(CONF_PLACE_RADIUS) == 150:
            values[CONF_PLACE_RADIUS] = DEFAULT_PLACE_RADIUS
        values.setdefault(
            CONF_INSTITUTION_SEARCH_RADIUS, DEFAULT_INSTITUTION_SEARCH_RADIUS
        )
        values.setdefault(CONF_OVERPASS_URL, DEFAULT_OVERPASS_URL)
        values.setdefault(CONF_RELEVANCE_KEYWORDS, DEFAULT_RELEVANCE_KEYWORDS)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
    )
    return True


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
