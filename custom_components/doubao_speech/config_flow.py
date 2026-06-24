"""Config flow for the Doubao Speech integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import api
from .const import (
    CONF_API_KEY,
    CONF_EMOTION,
    CONF_RESOURCE_ID,
    CONF_SPEECH_RATE,
    CONF_VOICE,
    DEFAULT_EMOTION,
    DEFAULT_RESOURCE_ID,
    DEFAULT_SPEECH_RATE,
    DEFAULT_VOICE,
    DOMAIN,
    MAX_SPEECH_RATE,
    MIN_SPEECH_RATE,
    RESOURCE_IDS,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the config/options schema with the given defaults."""
    return vol.Schema(
        {
            vol.Required(
                CONF_API_KEY, default=defaults.get(CONF_API_KEY, "")
            ): str,
            vol.Optional(
                CONF_RESOURCE_ID,
                default=defaults.get(CONF_RESOURCE_ID, DEFAULT_RESOURCE_ID),
            ): vol.In(RESOURCE_IDS),
            vol.Optional(
                CONF_VOICE, default=defaults.get(CONF_VOICE, DEFAULT_VOICE)
            ): str,
            vol.Optional(
                CONF_SPEECH_RATE,
                default=defaults.get(CONF_SPEECH_RATE, DEFAULT_SPEECH_RATE),
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SPEECH_RATE, max=MAX_SPEECH_RATE)),
            vol.Optional(
                CONF_EMOTION, default=defaults.get(CONF_EMOTION, DEFAULT_EMOTION)
            ): str,
        }
    )


async def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> None:
    """Validate credentials with a tiny test synthesis."""
    session = async_get_clientsession(hass)
    try:
        await api.synthesize(
            session,
            user_input[CONF_API_KEY],
            user_input.get(CONF_RESOURCE_ID, DEFAULT_RESOURCE_ID),
            "你好",
            user_input.get(CONF_VOICE, DEFAULT_VOICE),
            0,
            "",
            timeout=20,
        )
    except api.DoubaoAuthError as err:
        raise InvalidAuth(str(err)) from err
    except (api.DoubaoError, aiohttp.ClientError, TimeoutError) as err:
        raise CannotConnect(str(err)) from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Doubao Speech."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Get the options flow handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{DOMAIN}_{user_input[CONF_API_KEY][-8:]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Doubao Speech", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=_schema({}), errors=errors
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Doubao Speech (edit voice / rate / emotion / key)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the options step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, **user_input},
                )
                return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_schema(dict(self._config_entry.data)),
            errors=errors,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate invalid authentication."""
