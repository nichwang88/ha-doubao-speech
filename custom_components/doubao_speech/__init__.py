"""Doubao Speech integration for Home Assistant (火山引擎豆包语音合成大模型 2.0)."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import get_url

from . import api
from .const import (
    CONF_EMOTION,
    CONF_SPEECH_RATE,
    CONF_VOICE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.TTS]

SERVICE_BROADCAST = "broadcast"
BROADCAST_FILE = "doubao_broadcast.mp3"

BROADCAST_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Required("media_player_entity_id"): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Optional(CONF_VOICE): cv.string,
        vol.Optional(CONF_EMOTION): cv.string,
        vol.Optional(CONF_SPEECH_RATE): vol.Coerce(int),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Doubao Speech from a config entry."""
    hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_BROADCAST):
        hass.services.async_register(
            DOMAIN, SERVICE_BROADCAST, _make_broadcast_handler(hass),
            schema=BROADCAST_SCHEMA,
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not any(
            isinstance(v, dict) and v.get("tts_entity")
            for v in hass.data[DOMAIN].values()
        ) and hass.services.has_service(DOMAIN, SERVICE_BROADCAST):
            hass.services.async_remove(DOMAIN, SERVICE_BROADCAST)
    return unload_ok


def _get_tts_entity(hass: HomeAssistant):
    """Return any configured Doubao TTS entity."""
    for data in hass.data.get(DOMAIN, {}).values():
        if isinstance(data, dict) and data.get("tts_entity"):
            return data["tts_entity"]
    return None


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


async def _to_homepod_mp3(hass: HomeAssistant, audio: bytes) -> bytes | None:
    """Re-encode to mono 24k mp3 with metadata stripped (HomePod/AirPlay safe)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "0",
            "-ac", "1", "-ar", "24000",
            "-codec:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input=audio)
        if proc.returncode == 0 and out:
            return out
        _LOGGER.error(
            "broadcast: ffmpeg failed (rc=%s): %s",
            proc.returncode, err.decode("utf-8", "ignore")[:200],
        )
    except FileNotFoundError:
        _LOGGER.warning("broadcast: ffmpeg not found; using raw audio")
        return audio
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("broadcast: transcode error: %s", err)
    return None


def _make_broadcast_handler(hass: HomeAssistant):
    """Build the broadcast service handler.

    Doubao 2.0 understands semantics natively, so the whole message is sent in
    one go (auto-split only when it exceeds the per-request byte budget) and the
    model performs the emotional delivery. An optional `emotion` hint biases the
    overall tone. The clip is rendered to a file first, then played — this
    avoids HA's lazy-TTS / pyatv stream timeouts on HomePod.
    """
    async def _handle(call: ServiceCall) -> None:
        entity = _get_tts_entity(hass)
        if entity is None:
            _LOGGER.error("doubao_speech.broadcast: no TTS entity configured")
            return

        message = call.data["message"]
        voice = call.data.get(CONF_VOICE)
        emotion = call.data.get(CONF_EMOTION)
        speech_rate = call.data.get(CONF_SPEECH_RATE)

        try:
            audio = await entity.async_synthesize(message, voice, speech_rate, emotion)
        except api.DoubaoError as err:
            _LOGGER.error("doubao_speech.broadcast: synth failed: %s", err)
            return

        audio = await _to_homepod_mp3(hass, audio)
        if not audio:
            return

        www = hass.config.path("www")
        await hass.async_add_executor_job(lambda: os.makedirs(www, exist_ok=True))
        out_path = os.path.join(www, BROADCAST_FILE)
        await hass.async_add_executor_job(_write_file, out_path, audio)

        try:
            base = get_url(hass, prefer_internal=True, allow_external=True)
        except Exception:  # noqa: BLE001
            base = hass.config.internal_url or ""
        url = f"{base}/local/{BROADCAST_FILE}?v={int(time.time())}"

        await hass.services.async_call(
            "media_player", "play_media",
            {
                "entity_id": call.data["media_player_entity_id"],
                "media_content_id": url,
                "media_content_type": "music",
            },
            blocking=False,
        )
        _LOGGER.debug("doubao_speech.broadcast: playing %s", url)

    return _handle
