"""Constants for the Doubao Speech (火山引擎豆包语音合成大模型 2.0) integration."""

DOMAIN = "doubao_speech"

# Volcengine Doubao TTS large-model V3 HTTP single-direction (unidirectional)
# streaming endpoint. Returns NDJSON; see api.py for the wire format.
TTS_API_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

# --- Config keys -------------------------------------------------------------
CONF_API_KEY = "api_key"
CONF_RESOURCE_ID = "resource_id"
CONF_VOICE = "voice"
CONF_SPEECH_RATE = "speech_rate"
CONF_EMOTION = "emotion"

# --- Resource IDs (X-Api-Resource-Id) ---------------------------------------
# Selects the model family / billing. Must match the speaker family or the API
# returns "resource ID is mismatched with speaker related resource".
RESOURCE_TTS_2_0 = "seed-tts-2.0"  # official 2.0 voices (default)
RESOURCE_TTS_1_0 = "seed-tts-1.0"  # official 1.0 voices
RESOURCE_ICL_2_0 = "seed-icl-2.0"  # cloned (声音复刻) 2.0 voices, speaker = S_xxx
RESOURCE_IDS = [RESOURCE_TTS_2_0, RESOURCE_TTS_1_0, RESOURCE_ICL_2_0]

# --- Defaults ----------------------------------------------------------------
DEFAULT_RESOURCE_ID = RESOURCE_TTS_2_0
# 豆包 2.0 女声 VV — verified working. 2.0 voices carry "_uranus_" in the id.
# Full, current voice list: https://www.volcengine.com/docs/6561/1257544
DEFAULT_VOICE = "zh_female_vv_uranus_bigtts"
DEFAULT_SPEECH_RATE = 0
DEFAULT_EMOTION = ""
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_FORMAT = "mp3"

# speech_rate range per Volcengine: -50 (slowest) .. 100 (fastest), 0 = normal.
MIN_SPEECH_RATE = -50
MAX_SPEECH_RATE = 100

# Per-request text budget (UTF-8 bytes). Longer text is split on sentence
# boundaries and synthesised in multiple requests, then concatenated.
TTS_MAX_BYTES = 1000

# Doubao synthesises Chinese/English mixed natively; the chosen voice may also
# support more. Language is informational for HA — the API infers it from text.
SUPPORT_LANGUAGES = ["zh", "en", "ja", "ko", "fr", "de", "es", "pt", "id"]
DEFAULT_LANGUAGE = "zh"

# --- STT (语音识别大模型, V3 streaming WebSocket) -----------------------------
# Binary protocol over wss; see api.py. Audio is PCM 16-bit mono.
STT_API_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

CONF_STT_RESOURCE_ID = "stt_resource_id"
# volc.bigasr.sauc.duration = 大模型流式语音识别（按时长计费，默认，verified）
# volc.seedasr.sauc.duration = Seed ASR 流式
STT_RES_BIGASR = "volc.bigasr.sauc.duration"
STT_RES_SEEDASR = "volc.seedasr.sauc.duration"
STT_RESOURCE_IDS = [STT_RES_BIGASR, STT_RES_SEEDASR]
DEFAULT_STT_RESOURCE_ID = STT_RES_BIGASR

# STT input audio (HA Assist sends 16k 16-bit mono PCM/WAV).
STT_SAMPLE_RATE = 16000
