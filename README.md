# Doubao Speech — 火山引擎豆包语音合成大模型 2.0 · Home Assistant 集成

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

把**火山引擎豆包语音大模型**接入 Home Assistant 的自定义集成：**TTS 引擎**（语音合成大模型 2.0 / Seed-TTS 2.0，
供语音助手 / `tts.speak` / 自动化使用）、**STT 引擎**（语音识别大模型 / 流式 ASR，供 Assist 语音助手听写）
和一个 **`doubao_speech.broadcast` 广播服务**（离线渲染再播放，规避 HomePod / AirPlay 流式超时）。

豆包 2.0 的核心优势是**原生语义理解 + 情感演绎**——把整段文本交给模型，它会按内容自动表达情感，
无需外挂规则或 LLM 判断语气。

> 风格与用法对标 [`ha-qwen3-speech`](https://github.com/nichwang88/ha-qwen3-speech)，可平滑迁移。

---

## 功能

- ✅ **TTS 平台实体**（`Doubao TTS`）：`tts.speak`、Assist 语音助手、自动化均可调用
- ✅ **STT 平台实体**（`Doubao STT`）：Assist 语音助手语音转文字，流式 ASR 大模型、自带标点 + ITN（如"二十五度"→"25度"）
- ✅ **`doubao_speech.broadcast` 广播服务**：整段离线渲染 → `ffmpeg` 转 HomePod 安全 MP3（单声道 24k、去元数据）→ `play_media`
- ✅ 每次调用可覆盖**音色 / 语速 / 语气**
- ✅ 长文本按句**自动切分**（单请求 UTF-8 ≤ 1000 字节）后拼接
- ✅ 支持官方 **2.0 / 1.0** 音色与**声音复刻**（`seed-icl-2.0`，`S_xxx`）
- ✅ UI 配置流程（`config_flow`），保存时做真实合成测试；中文 / 英文界面

## 接入原理（V3 HTTP 单向流式）

| 项目 | 值 |
|---|---|
| Endpoint | `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| 鉴权头 | `X-Api-Key: <API Key>`（新版控制台）|
| 资源头 | `X-Api-Resource-Id: seed-tts-2.0`（2.0 官方音色）|
| 请求头 | `X-Api-Request-Id: <uuid>`、`Content-Type: application/json` |
| 响应 | NDJSON，逐行 `{"code":0,"data":"<base64 音频>"}`，解码拼接即音频 |
| 情感 | `req_params.additions`（JSON 字符串）内 `context_texts[0]` 传一句语气描述 |

## STT 接入原理（V3 流式 WebSocket）

| 项目 | 值 |
|---|---|
| Endpoint | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel` |
| 鉴权头 | `X-Api-Key` + `X-Api-Resource-Id: volc.bigasr.sauc.duration` + `X-Api-Connect-Id` / `X-Api-Request-Id` / `X-Api-Sequence: -1` |
| 协议 | 二进制分帧：`头(4B) + payload 长度(4B,大端) + gzip(payload)`；先发 full client request(JSON 配置)，再发 PCM 音频帧，末帧置 LAST flag |
| 音频 | PCM 16-bit 单声道 16kHz（Assist 默认输出；集成自动剥 WAV 头）|
| 返回 | `result.text`；开启 `enable_itn` + `enable_punc`（数字归一化 + 标点）|

## 安装

### 通过 HACS（推荐）
1. HACS → 右上角 → **Custom repositories**
2. 仓库填 `https://github.com/nichwang88/ha-doubao-speech`，类别选 **Integration**
3. 搜索 **Doubao Speech** 安装，重启 Home Assistant

### 手动
把 `custom_components/doubao_speech/` 复制到 HA 的 `config/custom_components/` 下，重启。

## 获取 API Key（火山引擎）

1. 进入[火山引擎语音技术控制台](https://console.volcengine.com/speech/app)
2. 确认应用已开通 **语音合成大模型**，并在音色管理里勾选 **2.0 音色**（资源 `seed-tts-2.0`）
3. 在控制台创建 / 复制 **API Key**（形如 `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`）

> 若用旧版控制台的 `App ID + Access Token` 三件套鉴权，本集成默认走新版 `X-Api-Key`；
> 如需旧版鉴权请提 issue。

## 配置

**设置 → 设备与服务 → 添加集成 → 搜索 “Doubao Speech”**，填写：

| 字段 | 说明 | 默认 |
|---|---|---|
| API Key | 控制台的 API Key | — |
| Resource ID | `seed-tts-2.0` / `seed-tts-1.0` / `seed-icl-2.0` | `seed-tts-2.0` |
| 音色 Voice | speaker，如 `zh_female_vv_uranus_bigtts` | `zh_female_vv_uranus_bigtts` |
| 语速 Speech rate | `-50`（最慢）~ `100`（最快），`0` 正常 | `0` |
| 默认语气 Emotion | 一句语气描述（可选） | 空 |

音色完整列表见官方文档：<https://www.volcengine.com/docs/6561/1257544>
（2.0 音色 ID 含 `_uranus_`；本集成音色为自由文本，任何已授权 speaker 均可填。）

## 使用

### TTS（语音助手 / 自动化）
```yaml
service: tts.speak
data:
  entity_id: tts.doubao_tts
  media_player_entity_id: media_player.master_bedroom_homepod
  message: "你好，这是豆包语音合成大模型二点零。"
  options:
    voice: zh_female_vv_uranus_bigtts
    speech_rate: 0
    emotion: "用温柔自然的语气说话"
```

### 广播服务（推荐用于 HomePod / AirPlay）
```yaml
service: doubao_speech.broadcast
data:
  message: "早上好，今天天气晴朗，气温 20 度，适合出门散步。"
  media_player_entity_id: media_player.master_bedroom_homepod
  # 可选：
  # voice: zh_female_vv_uranus_bigtts
  # emotion: "用温暖明亮、有活力的语气说话"
  # speech_rate: 0
```
不传 `emotion` 时，由豆包 2.0 按文本语义自动演绎情感。

## 排错

| 现象 | 原因 / 处理 |
|---|---|
| `鉴权失败 / app key not found` | API Key 错误，或没用 `X-Api-Key` 头 |
| `resource ID is mismatched` | 音色与 Resource ID 不匹配（2.0 音色配 `seed-tts-2.0`，复刻配 `seed-icl-2.0`）|
| `access denied` | 该音色未授权，去控制台开通 |
| `quota exceeded` | 额度 / 并发用尽，开通正式版或稍后重试 |
| 广播无声 | 确认 HA 主机有 `ffmpeg`；查看日志 `doubao_speech.broadcast` |

## 依赖

- Home Assistant 2024.1.0+
- `ffmpeg`（广播服务转码用；HAOS / Supervised 默认自带）

## 许可

[MIT](LICENSE) · 与火山引擎 / 字节跳动无官方关联。
