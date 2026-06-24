"""Text-to-speech support for Volcengine Doubao TTS large-model 2.0."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.tts import TextToSpeechEntity, TtsAudioType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import api
from .const import (
    CONF_API_KEY,
    CONF_EMOTION,
    CONF_RESOURCE_ID,
    CONF_SPEECH_RATE,
    CONF_VOICE,
    DEFAULT_EMOTION,
    DEFAULT_LANGUAGE,
    DEFAULT_RESOURCE_ID,
    DEFAULT_SPEECH_RATE,
    DEFAULT_VOICE,
    DOMAIN,
    MAX_SPEECH_RATE,
    MIN_SPEECH_RATE,
    SUPPORT_LANGUAGES,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Doubao TTS platform from a config entry."""
    entity = DoubaoTTSEntity(hass, config_entry)
    # Expose the entity so the `broadcast` service can reuse its config/synth.
    hass.data.setdefault(DOMAIN, {}).setdefault(config_entry.entry_id, {})[
        "tts_entity"
    ] = entity
    async_add_entities([entity])


class DoubaoTTSEntity(TextToSpeechEntity):
    """Doubao TTS entity backed by the Volcengine V3 HTTP API."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self.hass = hass
        self._entry = config_entry
        self._attr_name = "Doubao TTS"
        self._attr_unique_id = f"{DOMAIN}_tts_{config_entry.entry_id}"

    # --- config accessors (options override data) ----------------------------
    def _conf(self, key: str, default: Any) -> Any:
        return self._entry.options.get(key, self._entry.data.get(key, default))

    @property
    def _api_key(self) -> str:
        return self._entry.data[CONF_API_KEY]

    @property
    def _resource_id(self) -> str:
        return self._conf(CONF_RESOURCE_ID, DEFAULT_RESOURCE_ID)

    @property
    def _default_voice(self) -> str:
        return self._conf(CONF_VOICE, DEFAULT_VOICE)

    @property
    def _default_speech_rate(self) -> int:
        return int(self._conf(CONF_SPEECH_RATE, DEFAULT_SPEECH_RATE))

    @property
    def _default_emotion(self) -> str:
        return self._conf(CONF_EMOTION, DEFAULT_EMOTION)

    # --- HA TTS interface ----------------------------------------------------
    @property
    def default_language(self) -> str:
        """Return the default language."""
        return DEFAULT_LANGUAGE

    @property
    def supported_languages(self) -> list[str]:
        """Return the list of supported languages."""
        return SUPPORT_LANGUAGES

    @property
    def supported_options(self) -> list[str]:
        """Return per-call options accepted via tts.speak."""
        return [CONF_VOICE, CONF_SPEECH_RATE, CONF_EMOTION]

    @property
    def default_options(self) -> dict[str, Any]:
        """Return default per-call options."""
        return {
            CONF_VOICE: self._default_voice,
            CONF_SPEECH_RATE: self._default_speech_rate,
            CONF_EMOTION: self._default_emotion,
        }

    async def async_synthesize(
        self,
        message: str,
        voice: str | None = None,
        speech_rate: int | None = None,
        emotion: str | None = None,
    ) -> bytes:
        """Synthesise a message to mp3 bytes (shared with the broadcast service)."""
        session = async_get_clientsession(self.hass)
        return await api.synthesize(
            session,
            self._api_key,
            self._resource_id,
            message,
            voice or self._default_voice,
            self._default_speech_rate if speech_rate is None else int(speech_rate),
            self._default_emotion if emotion is None else emotion,
        )

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        """Load TTS audio from the Doubao API."""
        voice = options.get(CONF_VOICE, self._default_voice)
        speech_rate = int(options.get(CONF_SPEECH_RATE, self._default_speech_rate))
        emotion = options.get(CONF_EMOTION, self._default_emotion)

        if not MIN_SPEECH_RATE <= speech_rate <= MAX_SPEECH_RATE:
            _LOGGER.warning(
                "speech_rate %s out of range (%d..%d); clamping",
                speech_rate, MIN_SPEECH_RATE, MAX_SPEECH_RATE,
            )
            speech_rate = max(MIN_SPEECH_RATE, min(MAX_SPEECH_RATE, speech_rate))

        try:
            audio = await self.async_synthesize(message, voice, speech_rate, emotion)
        except api.DoubaoError as err:
            _LOGGER.error("Doubao TTS failed: %s", err)
            return None, None

        _LOGGER.debug(
            "Doubao TTS: %d bytes, voice=%s, rate=%d", len(audio), voice, speech_rate
        )
        return "mp3", audio
