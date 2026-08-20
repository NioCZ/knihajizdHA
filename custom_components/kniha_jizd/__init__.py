"""Kniha jízd custom integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
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
from .export import export_excel
from .geocoding import NominatimGeocoder
from .manager import KnihaJizdManager
from .nearby_search import NearbyInstitutionSearcher
from .storage import KnihaJizdRepository

EXPORT_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_PATH, default=DEFAULT_EXPORT_PATH): str})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-level services."""

    async def _async_export_service(call: ServiceCall) -> dict[str, Any]:
        manager = _get_loaded_manager(hass)
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

        try:
            return await hass.async_add_executor_job(
                export_excel, manager.repository.raw_path, output_path
            )
        except (OSError, ValueError, TypeError) as err:
            raise ServiceValidationError(f"Excel export failed: {err}") from err

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
    await manager.async_shutdown()
    return True


def _get_loaded_manager(hass: HomeAssistant) -> KnihaJizdManager:
    """Return the single loaded manager or raise a service error."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and isinstance(
            entry.runtime_data, KnihaJizdManager
        ):
            return entry.runtime_data
    raise ServiceValidationError("Kniha jízd has no loaded configuration entry")
