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
    CONF_GPS_ENTITY,
    CONF_HOME_ADDRESS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_NOMINATIM_EMAIL,
    CONF_NOMINATIM_URL,
    CONF_NOMINATIM_USER_AGENT,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_OVERPASS_URL,
    CONF_PLACE_RADIUS,
    CONF_RELEVANCE_KEYWORDS,
    CONF_RETURN_CONTEXT_HOURS,
    CONF_TRANSIENT_STOP_MINUTES,
    CONF_TRIGGER_ENTITY,
    CONF_WAIT_TIMEOUT,
    DEFAULT_ADDRESS_ENTITY,
    DEFAULT_COMPANY_ADDRESS,
    DEFAULT_COMPANY_LATITUDE,
    DEFAULT_COMPANY_LABEL,
    DEFAULT_COMPANY_LONGITUDE,
    DEFAULT_GPS_ENTITY,
    DEFAULT_HOME_ADDRESS,
    DEFAULT_HOME_LATITUDE,
    DEFAULT_HOME_LONGITUDE,
    DEFAULT_INSTITUTION_SEARCH_RADIUS,
    DEFAULT_NOMINATIM_URL,
    DEFAULT_NOMINATIM_USER_AGENT,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_ODOMETER_ENTITY,
    DEFAULT_OVERPASS_URL,
    DEFAULT_PLACE_RADIUS,
    DEFAULT_RELEVANCE_KEYWORDS,
    DEFAULT_RETURN_CONTEXT_HOURS,
    DEFAULT_TRANSIENT_STOP_MINUTES,
    DEFAULT_TRIGGER_ENTITY,
    DEFAULT_WAIT_TIMEOUT,
    DOMAIN,
    NAME,
)


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
                CONF_WAIT_TIMEOUT,
                default=defaults.get(CONF_WAIT_TIMEOUT, DEFAULT_WAIT_TIMEOUT),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=60, max=1800),
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
            vol.Optional(
                CONF_HOME_ADDRESS,
                default=defaults.get(CONF_HOME_ADDRESS, DEFAULT_HOME_ADDRESS),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_HOME_LATITUDE,
                default=defaults.get(CONF_HOME_LATITUDE, DEFAULT_HOME_LATITUDE),
            ): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
            vol.Optional(
                CONF_HOME_LONGITUDE,
                default=defaults.get(CONF_HOME_LONGITUDE, DEFAULT_HOME_LONGITUDE),
            ): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
            vol.Optional(
                CONF_COMPANY_ADDRESS,
                default=defaults.get(CONF_COMPANY_ADDRESS, DEFAULT_COMPANY_ADDRESS),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_COMPANY_LATITUDE,
                default=defaults.get(
                    CONF_COMPANY_LATITUDE, DEFAULT_COMPANY_LATITUDE
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
            vol.Optional(
                CONF_COMPANY_LONGITUDE,
                default=defaults.get(
                    CONF_COMPANY_LONGITUDE, DEFAULT_COMPANY_LONGITUDE
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
            vol.Optional(
                CONF_COMPANY_LABEL,
                default=defaults.get(CONF_COMPANY_LABEL, DEFAULT_COMPANY_LABEL),
            ): selector.TextSelector(),
            vol.Required(
                CONF_PLACE_RADIUS,
                default=defaults.get(CONF_PLACE_RADIUS, DEFAULT_PLACE_RADIUS),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=25, max=5000),
            ),
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

    VERSION = 6

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
        """Store the entry without relying on newer OptionsFlow helpers."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
