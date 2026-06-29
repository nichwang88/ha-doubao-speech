"""Low-level client for the Volcengine Doubao TTS large-model V3 HTTP API.

Wire format (V3 single-direction streaming, endpoint ``.../tts/unidirectional``):

Request headers::

    X-Api-Key:         <api key>          # 新版控制台 API Key
    X-Api-Resource-Id: seed-tts-2.0       # model family
    X-Api-Request-Id:  <uuid>             # unique per request
    Content-Type:      application/json

Request body::

    {
      "user": {"uid": "..."},
      "req_params": {
        "text": "...",
        "speaker": "zh_female_vv_uranus_bigtts",
        "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": 0},
        "additions": "{\\"context_texts\\": [\\"用温柔的语气\\"]}"   # optional, a JSON *string*
      }
    }

Response: newline-delimited JSON (NDJSON). Each line is one chunk::

    {"code": 0, "message": "", "data": "<base64 audio>"}
    ...
    {"code": 20000000}                 # optional terminator

Errors arrive either flat (``{"code": 5..., "message": ...}``) or wrapped
(``{"header": {"code": 4..., "message": ...}}``); both shapes are handled.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import re
import struct
import uuid
from typing import Iterable

import aiohttp

from .const import (
    AUDIO_GENERATION_API_URL,
    AUDIO_GENERATION_MODEL,
    DEFAULT_FORMAT,
    DEFAULT_SAMPLE_RATE,
    STT_API_URL,
    STT_SAMPLE_RATE,
    TTS_API_URL,
    TTS_MAX_BYTES,
)

_LOGGER = logging.getLogger(__name__)

# code == 0 -> audio chunk; code == 20000000 -> success/end-of-stream marker.
_SUCCESS_CODES = (0, 20000000)

# Friendly mapping for the common failure messages the API returns.
_ERROR_HINTS = {
    "app key not found": "鉴权头缺失或错误，请检查 API Key",
    "no token or access_key": "鉴权头缺失或错误，请检查 API Key",
    "load grant": "鉴权失败，请检查 API Key 是否正确",
    "quota exceeded": "用量/额度已用完，请在控制台开通正式版或充值",
    "concurrency": "并发超过限制，请稍后重试",
    "resource id is mismatched": "音色与 Resource ID 不匹配（2.0 音色用 seed-tts-2.0，复刻音色用 seed-icl-2.0）",
    "access denied": "音色未授权，请在控制台开通/购买该音色",
    "illegal input text": "无效文本（可能为空、纯标点或语种不匹配）",
    "init engine instance failed": "音色或资源配置错误",
}


class DoubaoError(Exception):
    """Base error from the Doubao TTS API."""


class DoubaoAuthError(DoubaoError):
    """Authentication failed (bad API key / resource id)."""


class DoubaoQuotaError(DoubaoError):
    """Quota or concurrency limit reached."""


def _friendly(message: str | None, code: int | None) -> str:
    msg = (message or "").lower()
    for needle, hint in _ERROR_HINTS.items():
        if needle in msg:
            return hint
    return message or f"未知错误 (code={code})"


def _raise_for_error(code: int | None, message: str | None) -> None:
    hint = _friendly(message, code)
    msg = (message or "").lower()
    if code in (45000000,) or "grant" in msg or "app key" in msg or "token" in msg:
        raise DoubaoAuthError(hint)
    if "quota" in msg or "concurrency" in msg:
        raise DoubaoQuotaError(hint)
    raise DoubaoError(hint)


def build_headers(api_key: str, resource_id: str) -> dict[str, str]:
    """Build the auth + routing headers for one request."""
    return {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }


def build_body(
    text: str,
    voice: str,
    speech_rate: int = 0,
    emotion: str = "",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    audio_format: str = DEFAULT_FORMAT,
    uid: str = "home-assistant",
) -> dict:
    """Build the request body for one synthesis call."""
    req_params: dict = {
        "text": text,
        "speaker": voice,
        "audio_params": {
            "format": audio_format,
            "sample_rate": sample_rate,
            "speech_rate": int(speech_rate),
        },
    }
    if emotion:
        # Doubao 2.0 reads a natural-language tone hint; only the first element
        # is used. Must be a JSON-serialised *string*, not an object.
        req_params["additions"] = json.dumps(
            {"context_texts": [emotion]}, ensure_ascii=False
        )
    return {"user": {"uid": uid}, "req_params": req_params}


def split_text(text: str, max_bytes: int = TTS_MAX_BYTES) -> list[str]:
    """Split text into <= max_bytes (UTF-8) chunks on sentence boundaries."""
    text = text.strip()
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    # Split keeping the trailing punctuation attached to each sentence.
    sentences = [s for s in re.split(r"(?<=[。！？!?；;\n])", text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        candidate = buf + sentence
        if len(candidate.encode("utf-8")) <= max_bytes:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(sentence.encode("utf-8")) <= max_bytes:
            buf = sentence
        else:
            chunks.extend(_hard_split(sentence, max_bytes))
    if buf:
        chunks.append(buf)
    return chunks


def _hard_split(text: str, max_bytes: int) -> list[str]:
    """Fallback: split a long punctuation-free run on character boundaries."""
    out: list[str] = []
    buf = ""
    for ch in text:
        if len((buf + ch).encode("utf-8")) > max_bytes:
            out.append(buf)
            buf = ch
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def parse_ndjson(raw: str) -> tuple[bytes, int | None, str | None]:
    """Parse an NDJSON response into (audio_bytes, last_error_code, error_msg)."""
    audio = bytearray()
    err_code: int | None = None
    err_msg: str | None = None
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            _LOGGER.debug("doubao: skipping unparsable line: %s", line[:120])
            continue
        # Errors may be flat or wrapped under "header".
        header = obj.get("header") if isinstance(obj.get("header"), dict) else {}
        code = obj.get("code", header.get("code"))
        message = obj.get("message", header.get("message"))
        data = obj.get("data")
        if data:
            try:
                audio += base64.b64decode(data)
            except (ValueError, TypeError):
                _LOGGER.warning("doubao: bad base64 chunk")
        if code is not None and code not in _SUCCESS_CODES:
            err_code, err_msg = code, message
    return bytes(audio), err_code, err_msg


async def synthesize(
    session: aiohttp.ClientSession,
    api_key: str,
    resource_id: str,
    text: str,
    voice: str,
    speech_rate: int = 0,
    emotion: str = "",
    timeout: int = 45,
) -> bytes:
    """Synthesise ``text`` -> audio bytes (mp3). Auto-splits long text.

    Raises DoubaoAuthError / DoubaoQuotaError / DoubaoError on failure.
    """
    chunks = split_text(text)
    if not chunks:
        raise DoubaoError("合成文本不能为空")

    audio = bytearray()
    for chunk in chunks:
        body = build_body(chunk, voice, speech_rate, emotion)
        headers = build_headers(api_key, resource_id)
        async with session.post(
            TTS_API_URL,
            json=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            raw = await resp.text()
            if resp.status != 200:
                # Surface whatever the body says (often {"header":{...}}).
                _, code, message = parse_ndjson(raw)
                _LOGGER.error("doubao: HTTP %s: %s", resp.status, raw[:200])
                _raise_for_error(code, message or f"HTTP {resp.status}")
            part, code, message = parse_ndjson(raw)
            if not part:
                _raise_for_error(code, message)
            audio += part
    return bytes(audio)


async def synthesize_audio_generation(
    session: aiohttp.ClientSession,
    api_key: str,
    prompt: str,
    speech_rate: int = 0,
    pitch_rate: int = 0,
    loudness_rate: int = 0,
    timeout: int = 150,
) -> bytes:
    """Generate a complete audio scene with Doubao seed-audio-1.0.

    This endpoint can render non-TTS audio elements when the prompt is written
    as an audio-scene description. It returns either base64 audio or a short
    lived URL; the URL is downloaded immediately so HA can serve a stable local
    `/local/...` file to HomePod/AirPlay.
    """
    prompt = prompt.strip()
    if not prompt:
        raise DoubaoError("音频生成提示词不能为空")

    body = {
        "model": AUDIO_GENERATION_MODEL,
        "text_prompt": prompt,
        "audio_config": {
            "format": DEFAULT_FORMAT,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "speech_rate": int(speech_rate),
            "pitch_rate": int(pitch_rate),
            "loudness_rate": int(loudness_rate),
        },
    }
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    async with session.post(
        AUDIO_GENERATION_API_URL,
        json=body,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        raw = await resp.text()
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as err:
            raise DoubaoError(f"音频生成返回非 JSON: HTTP {resp.status}") from err

        code = obj.get("code")
        message = obj.get("message")
        if resp.status != 200 or (code not in (None, 0)):
            _LOGGER.error("doubao audio generation: HTTP %s: %s", resp.status, raw[:300])
            _raise_for_error(code, message or f"HTTP {resp.status}")

        audio_b64 = obj.get("audio")
        if audio_b64:
            try:
                return base64.b64decode(audio_b64)
            except (ValueError, TypeError) as err:
                raise DoubaoError("音频生成返回了无效 base64 音频") from err

        audio_url = obj.get("url")
        if audio_url:
            async with session.get(
                audio_url,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as audio_resp:
                if audio_resp.status != 200:
                    raise DoubaoError(f"下载音频生成结果失败: HTTP {audio_resp.status}")
                data = await audio_resp.read()
                if data:
                    return data

        raise DoubaoError("音频生成返回为空")


def joined(parts: Iterable[bytes]) -> bytes:
    """Concatenate already-encoded audio parts."""
    out = bytearray()
    for p in parts:
        out += p
    return bytes(out)


# ─── STT: 大模型流式语音识别 (V3 binary WebSocket) ────────────────────────────
# Frame: header(4) + big-endian u32 payload length + (gzip) payload.
# header = [ver<<4|hdrsize, msgtype<<4|flags, serial<<4|compress, 0x00]
_ASR_PROTO_VER = 0b0001
_ASR_HDR_SIZE = 0b0001
_ASR_M_FULL_REQ = 0b0001
_ASR_M_AUDIO = 0b0010
_ASR_M_ERROR = 0b1111
_ASR_F_NONE = 0b0000
_ASR_F_LAST = 0b0010
_ASR_S_NONE = 0b0000
_ASR_S_JSON = 0b0001
_ASR_C_GZIP = 0b0001


def _asr_header(mtype: int, flags: int, serial: int, compress: int) -> bytes:
    return bytes([
        (_ASR_PROTO_VER << 4) | _ASR_HDR_SIZE,
        (mtype << 4) | flags,
        (serial << 4) | compress,
        0x00,
    ])


def _asr_frame(header: bytes, payload: bytes) -> bytes:
    return header + struct.pack(">I", len(payload)) + payload


def _asr_parse(data: bytes):
    """Parse one server frame -> ('resp', json, is_last) | ('error', code, msg)."""
    mtype = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    serial = (data[2] >> 4) & 0x0F
    compress = data[2] & 0x0F
    off = (data[0] & 0x0F) * 4
    if mtype == _ASR_M_ERROR:
        code = struct.unpack(">I", data[off:off + 4])[0]; off += 4
        size = struct.unpack(">I", data[off:off + 4])[0]; off += 4
        return ("error", code, data[off:off + size].decode("utf-8", "ignore"))
    if flags & 0b0001:  # has sequence number
        off += 4
    size = struct.unpack(">I", data[off:off + 4])[0]; off += 4
    payload = data[off:off + size]
    if compress == _ASR_C_GZIP and payload:
        payload = gzip.decompress(payload)
    obj = json.loads(payload.decode("utf-8")) if serial == _ASR_S_JSON and payload else {}
    return ("resp", obj, bool(flags & 0b0010))  # is_last


def strip_wav_header(data: bytes) -> bytes:
    """Return raw PCM from a WAV container; pass through if not a WAV."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        i = 12
        while i + 8 <= len(data):
            cid = data[i:i + 4]
            size = int.from_bytes(data[i + 4:i + 8], "little")
            if cid == b"data":
                return data[i + 8:i + 8 + size] if size else data[i + 8:]
            i += 8 + size + (size & 1)
    return data


async def recognize(
    session: aiohttp.ClientSession,
    api_key: str,
    resource_id: str,
    pcm: bytes,
    sample_rate: int = STT_SAMPLE_RATE,
    timeout: int = 30,
) -> str:
    """Recognise 16-bit mono PCM via streaming ASR. Returns the transcript.

    Raises DoubaoAuthError / DoubaoError on failure.
    """
    if not pcm:
        raise DoubaoError("空音频")
    cid = str(uuid.uuid4())
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": cid,
        "X-Api-Request-Id": cid,
        "X-Api-Sequence": "-1",
    }
    cfg = {
        "user": {"uid": "home-assistant"},
        "audio": {"format": "pcm", "codec": "raw", "rate": sample_rate,
                  "bits": 16, "channel": 1},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                    "result_type": "full", "show_utterances": True},
    }
    final = ""
    try:
        async with asyncio.timeout(timeout):
            async with session.ws_connect(STT_API_URL, headers=headers, max_msg_size=0) as ws:
                req = gzip.compress(json.dumps(cfg).encode())
                await ws.send_bytes(_asr_frame(
                    _asr_header(_ASR_M_FULL_REQ, _ASR_F_NONE, _ASR_S_JSON, _ASR_C_GZIP), req))
                step = 3200  # ~100ms @ 16kHz/16-bit/mono
                for i in range(0, len(pcm), step):
                    chunk = pcm[i:i + step]
                    flags = _ASR_F_LAST if i + step >= len(pcm) else _ASR_F_NONE
                    await ws.send_bytes(_asr_frame(
                        _asr_header(_ASR_M_AUDIO, flags, _ASR_S_NONE, _ASR_C_GZIP),
                        gzip.compress(chunk)))
                    await asyncio.sleep(0.01)
                while True:
                    msg = await ws.receive()
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        kind, a, b = _asr_parse(msg.data)
                        if kind == "error":
                            _raise_for_error(a, b)
                        text = (a.get("result") or {}).get("text", "")
                        if text:
                            final = text
                        if b:  # is_last
                            return final
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                      aiohttp.WSMsgType.ERROR):
                        return final
    except asyncio.TimeoutError as err:
        raise DoubaoError("ASR 识别超时") from err
    return final
