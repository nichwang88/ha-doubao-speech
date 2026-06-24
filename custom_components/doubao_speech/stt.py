"""Speech-to-text support for Volcengine Doubao ASR large-model (V3 streaming)."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterable

from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import api
from .const import (
    CONF_API_KEY,
    CONF_STT_RESOURCE_ID,
    DEFAULT_STT_RESOURCE_ID,
    DOMAIN,
    STT_SAMPLE_RATE,
    SUPPORT_LANGUAGES,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Doubao STT platform from a config entry."""
    async_add_entities([DoubaoSTTEntity(hass, config_entry)])


class DoubaoSTTEntity(SpeechToTextEntity):
    """Doubao ASR speech-to-text entity (streaming V3 WebSocket)."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self.hass = hass
        self._entry = config_entry
        self._attr_name = "Doubao STT"
        self._attr_unique_id = f"{DOMAIN}_stt_{config_entry.entry_id}"

    def _conf(self, key: str, default):
        return self._entry.options.get(key, self._entry.data.get(key, default))

    @property
    def _api_key(self) -> str:
        return self._entry.data[CONF_API_KEY]

    @property
    def _resource_id(self) -> str:
        return self._conf(CONF_STT_RESOURCE_ID, DEFAULT_STT_RESOURCE_ID)

    @property
    def supported_languages(self) -> list[str]:
        """Return the list of supported languages."""
        return SUPPORT_LANGUAGES

    @property
    def supported_formats(self) -> list[AudioFormats]:
        """Return supported audio formats."""
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        """Return supported audio codecs."""
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        """Return supported bit rates."""
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        """Return supported sample rates."""
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        """Return supported channels."""
        return [AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        """Collect the audio stream and run Doubao ASR."""
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
        audio = b"".join(chunks)
        if not audio:
            _LOGGER.error("Doubao STT: empty audio stream")
            return SpeechResult("", SpeechResultState.ERROR)

        pcm = api.strip_wav_header(audio)
        sample_rate = getattr(metadata, "sample_rate", STT_SAMPLE_RATE) or STT_SAMPLE_RATE

        _LOGGER.debug(
            "Doubao STT: %d bytes (pcm %d), rate=%s, lang=%s",
            len(audio), len(pcm), sample_rate, metadata.language,
        )

        session = async_get_clientsession(self.hass)
        text = ""
        # One retry: the streaming server occasionally returns a transient
        # "[Server processing timeout]"; auth errors are not worth retrying.
        for attempt in range(2):
            try:
                text = await api.recognize(
                    session, self._api_key, self._resource_id, pcm, int(sample_rate)
                )
                break
            except api.DoubaoAuthError as err:
                _LOGGER.error("Doubao STT auth failed: %s", err)
                return SpeechResult("", SpeechResultState.ERROR)
            except api.DoubaoError as err:
                if attempt == 0:
                    _LOGGER.warning("Doubao STT transient error, retrying: %s", err)
                    continue
                _LOGGER.error("Doubao STT failed: %s", err)
                return SpeechResult("", SpeechResultState.ERROR)

        if not text:
            _LOGGER.warning("Doubao STT: empty transcript")
            return SpeechResult("", SpeechResultState.ERROR)

        _LOGGER.debug("Doubao STT result: %s", text)
        return SpeechResult(text, SpeechResultState.SUCCESS)
