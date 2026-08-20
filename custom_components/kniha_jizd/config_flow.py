"""Config flow for Kniha jízd."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ADDRESS_ENTITY,
    CONF_GPS_ENTITY,
    CONF_NOMINATIM_EMAIL,
    CONF_NOMINATIM_URL,
    CONF_NOMINATIM_USER_AGENT,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_PLACE_RADIUS,
    CONF_TRIGGER_ENTITY,
    CONF_WAIT_TIMEOUT,
    DEFAULT_ADDRESS_ENTITY,
    DEFAULT_GPS_ENTITY,
    DEFAULT_NOMINATIM_URL,
    DEFAULT_NOMINATIM_USER_AGENT,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_ODOMETER_ENTITY,
    DEFAULT_PLACE_RADIUS,
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
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60,
                    max=1800,
                    step=30,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_PLACE_RADIUS,
                default=defaults.get(CONF_PLACE_RADIUS, DEFAULT_PLACE_RADIUS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=25,
                    max=1000,
                    step=25,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="m",
                )
            ),
            vol.Required(
                CONF_NOMINATIM_URL,
                default=defaults.get(CONF_NOMINATIM_URL, DEFAULT_NOMINATIM_URL),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Required(
                CONF_NOMINATIM_USER_AGENT,
                default=defaults.get(
                    CONF_NOMINATIM_USER_AGENT, DEFAULT_NOMINATIM_USER_AGENT
                ),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_NOMINATIM_EMAIL,
                default=defaults.get(CONF_NOMINATIM_EMAIL, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
            ),
        }
    )


class KnihaJizdConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Kniha jízd."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
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
        return KnihaJizdOptionsFlow()


class KnihaJizdOptionsFlow(OptionsFlowWithReload):
    """Allow changing entities and behavior without reinstalling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(current))
