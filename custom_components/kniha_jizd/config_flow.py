"""Config flow for Kniha jízd."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADDRESS_ENTITY,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_LATITUDE,
    CONF_COMPANY_LABEL,
    CONF_COMPANY_LONGITUDE,
    CONF_COMPANY_RADIUS,
    CONF_CLIENT_RADIUS,
    CONF_GPS_ENTITY,
    CONF_HOME_ADDRESS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_LOCATION_SETTLE_SECONDS,
    CONF_NOMINATIM_EMAIL,
    CONF_NOMINATIM_URL,
    CONF_NOMINATIM_USER_AGENT,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_OVERPASS_URL,
    CONF_PENDING_REVIEW_HOURS,
    CONF_PLACE_RADIUS,
    CONF_PRIVATE_RADIUS,
    CONF_RELEVANCE_KEYWORDS,
    CONF_RETURN_CONTEXT_HOURS,
    CONF_TRANSIENT_STOP_MINUTES,
    CONF_TRANSIENT_RADIUS,
    CONF_TRIGGER_ENTITY,
    DEFAULT_ADDRESS_ENTITY,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_LABEL,
    DEFAULT_COMPANY_RADIUS,
    DEFAULT_CLIENT_RADIUS,
    DEFAULT_GPS_ENTITY,
    DEFAULT_HOME_ADDRESS,
    DEFAULT_HOME_RADIUS,
    DEFAULT_INSTITUTION_SEARCH_RADIUS,
    DEFAULT_LOCATION_SETTLE_SECONDS,
    DEFAULT_NOMINATIM_URL,
    DEFAULT_NOMINATIM_USER_AGENT,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_ODOMETER_ENTITY,
    DEFAULT_OVERPASS_URL,
    DEFAULT_PENDING_REVIEW_HOURS,
    DEFAULT_PRIVATE_RADIUS,
    DEFAULT_RELEVANCE_KEYWORDS,
    DEFAULT_RETURN_CONTEXT_HOURS,
    DEFAULT_TRANSIENT_STOP_MINUTES,
    DEFAULT_TRANSIENT_RADIUS,
    DEFAULT_TRIGGER_ENTITY,
    DOMAIN,
    NAME,
)


def _radius_default(
    defaults: dict[str, Any], key: str, default: int, legacy_cap: int
) -> int:
    """Use a saved split radius or conservatively migrate the legacy radius."""
    if key in defaults:
        try:
            return int(defaults[key])
        except (TypeError, ValueError):
            return default
    try:
        legacy = int(defaults.get(CONF_PLACE_RADIUS, default))
    except (TypeError, ValueError):
        legacy = default
    return max(25, min(legacy, legacy_cap))


_COORDINATE_KEYS = (
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_COMPANY_LATITUDE,
    CONF_COMPANY_LONGITUDE,
)


def _coordinate_marker(
    defaults: dict[str, Any], key: str, minimum: float, maximum: float
) -> vol.Marker:
    """Create a clearable optional coordinate with a numeric suggestion."""
    try:
        suggested = float(defaults.get(key))
    except (TypeError, ValueError):
        return vol.Optional(key)
    if not minimum <= suggested <= maximum:
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": suggested})


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the user/options schema with suggested values."""
    return vol.Schema(
        {
            vol.Required(
                CONF_TRIGGER_ENTITY,
                default=defaults.get(CONF_TRIGGER_ENTITY, DEFAULT_TRIGGER_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(
                CONF_GPS_ENTITY,
                default=defaults.get(CONF_GPS_ENTITY, DEFAULT_GPS_ENTITY),
            ): selector.EntitySelector(),
            vol.Required(
                CONF_ADDRESS_ENTITY,
                default=defaults.get(CONF_ADDRESS_ENTITY, DEFAULT_ADDRESS_ENTITY),
            ): selector.EntitySelector(),
            vol.Required(
                CONF_ODOMETER_ENTITY,
                default=defaults.get(CONF_ODOMETER_ENTITY, DEFAULT_ODOMETER_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_NOTIFY_SERVICE,
                default=defaults.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
            ): selector.TextSelector(),
            vol.Required(
                CONF_LOCATION_SETTLE_SECONDS,
                default=defaults.get(
                    CONF_LOCATION_SETTLE_SECONDS, DEFAULT_LOCATION_SETTLE_SECONDS
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=180),
            ),
            vol.Required(
                CONF_RETURN_CONTEXT_HOURS,
                default=defaults.get(
                    CONF_RETURN_CONTEXT_HOURS, DEFAULT_RETURN_CONTEXT_HOURS
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=72),
            ),
            vol.Required(
                CONF_TRANSIENT_STOP_MINUTES,
                default=defaults.get(
                    CONF_TRANSIENT_STOP_MINUTES, DEFAULT_TRANSIENT_STOP_MINUTES
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=5, max=180),
            ),
            vol.Required(
                CONF_PENDING_REVIEW_HOURS,
                default=defaults.get(
                    CONF_PENDING_REVIEW_HOURS, DEFAULT_PENDING_REVIEW_HOURS
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
            vol.Optional(
                CONF_HOME_ADDRESS,
                default=defaults.get(CONF_HOME_ADDRESS, DEFAULT_HOME_ADDRESS),
            ): selector.TextSelector(),
            _coordinate_marker(
                defaults, CONF_HOME_LATITUDE, -90, 90
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-90,
                    max=90,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            _coordinate_marker(
                defaults, CONF_HOME_LONGITUDE, -180, 180
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180,
                    max=180,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_COMPANY_ADDRESS,
                default=defaults.get(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS),
            ): selector.TextSelector(),
            _coordinate_marker(
                defaults, CONF_COMPANY_LATITUDE, -90, 90
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-90,
                    max=90,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            _coordinate_marker(
                defaults, CONF_COMPANY_LONGITUDE, -180, 180
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180,
                    max=180,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_COMPANY_LABEL,
                default=defaults.get(CONF_COMPANY_LABEL, DEFAULT_COMPANY_LABEL),
            ): selector.TextSelector(),
            vol.Required(
                CONF_HOME_RADIUS,
                default=_radius_default(
                    defaults, CONF_HOME_RADIUS, DEFAULT_HOME_RADIUS, 300
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=25, max=1000)),
            vol.Required(
                CONF_COMPANY_RADIUS,
                default=_radius_default(
                    defaults, CONF_COMPANY_RADIUS, DEFAULT_COMPANY_RADIUS, 300
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=25, max=1000)),
            vol.Required(
                CONF_CLIENT_RADIUS,
                default=_radius_default(
                    defaults, CONF_CLIENT_RADIUS, DEFAULT_CLIENT_RADIUS, 500
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=25, max=2000)),
            vol.Required(
                CONF_PRIVATE_RADIUS,
                default=_radius_default(
                    defaults, CONF_PRIVATE_RADIUS, DEFAULT_PRIVATE_RADIUS, 250
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=25, max=1000)),
            vol.Required(
                CONF_TRANSIENT_RADIUS,
                default=_radius_default(
                    defaults, CONF_TRANSIENT_RADIUS, DEFAULT_TRANSIENT_RADIUS, 200
                ),
            ): vol.All(vol.Coerce(int), vol.Range(min=25, max=1000)),
            vol.Required(
                CONF_INSTITUTION_SEARCH_RADIUS,
                default=defaults.get(
                    CONF_INSTITUTION_SEARCH_RADIUS,
                    DEFAULT_INSTITUTION_SEARCH_RADIUS,
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=500, max=10000),
            ),
            vol.Required(
                CONF_OVERPASS_URL,
                default=defaults.get(CONF_OVERPASS_URL, DEFAULT_OVERPASS_URL),
            ): str,
            vol.Required(
                CONF_RELEVANCE_KEYWORDS,
                default=defaults.get(
                    CONF_RELEVANCE_KEYWORDS, DEFAULT_RELEVANCE_KEYWORDS
                ),
            ): str,
            vol.Required(
                CONF_NOMINATIM_URL,
                default=defaults.get(CONF_NOMINATIM_URL, DEFAULT_NOMINATIM_URL),
            ): str,
            vol.Required(
                CONF_NOMINATIM_USER_AGENT,
                default=defaults.get(
                    CONF_NOMINATIM_USER_AGENT, DEFAULT_NOMINATIM_USER_AGENT
                ),
            ): str,
            vol.Optional(
                CONF_NOMINATIM_EMAIL,
                default=defaults.get(CONF_NOMINATIM_EMAIL, ""),
            ): str,
        }
    )


class KnihaJizdConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kniha jízd."""

    VERSION = 10

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title=NAME, data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> KnihaJizdOptionsFlow:
        """Create the options flow."""
        return KnihaJizdOptionsFlow(config_entry)


class KnihaJizdOptionsFlow(config_entries.OptionsFlow):
    """Allow changing entities and behavior without reinstalling."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Keep compatibility with HA versions without config_entry injection."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manage the options."""
        if user_input is not None:
            updated = dict(user_input)
            for key in _COORDINATE_KEYS:
                updated.setdefault(key, "")
            return self.async_create_entry(data=updated)

        current = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
