# 设计：ha-doubao-speech（火山引擎豆包语音合成大模型 2.0 · HA 集成）

日期：2026-06-24 · 状态：已实现并在 HA 实测通过

## 目标
为 Home Assistant 提供基于**豆包语音合成大模型 2.0（Seed-TTS 2.0）**的 TTS 集成，
风格与用法对标作者已有的 [`ha-qwen3-speech`](https://github.com/nichwang88/ha-qwen3-speech)，
并发布为公开 GitHub 仓库（HACS 可装）。

## 决策记录
- **功能范围**：TTS 平台实体 + `doubao_speech.broadcast` 广播服务（不含 STT）。
- **广播情感**：**去掉** qwen3 那套「逐句关键词规则 + LLM 判断语气」。豆包 2.0 原生语义理解，
  整段交给模型自动演绎；仅保留一个可选的全局 `emotion` 语气提示（走 `additions.context_texts[0]`）。
- **接入协议**：V3 **HTTP 单向流式**（`/api/v3/tts/unidirectional`），不用 WebSocket——
  HA TTS 只需整段音频字节，HTTP 更稳更简单。
- **鉴权**：新版控制台 **API Key**，请求头 `X-Api-Key`（实测确定；旧版三件套 `X-Api-App-Id`+
  `X-Api-Access-Key` 走不通，返回 `app key not found`）。
- **音色**：自由文本输入，默认 `zh_female_vv_uranus_bigtts`（实测可用）。不硬编码大列表，
  README 指向官方音色页，避免「音色未授权」翻车。

## 架构
```
custom_components/doubao_speech/
├── api.py          # 纯客户端：headers/body 构造、文本切分、NDJSON 解析、synthesize()、异常
├── tts.py          # DoubaoTTSEntity（async_get_tts_audio + 共享 async_synthesize）
├── __init__.py     # setup + broadcast 服务（整段合成 → ffmpeg HomePod MP3 → play_media）
├── config_flow.py  # UI 配置 + OptionsFlow，保存时真实合成校验
├── const.py        # 端点/默认/范围/资源 ID
├── manifest.json / services.yaml / strings.json / translations/{zh-Hans,en}.json
hacs.json / README.md / LICENSE(MIT) / .gitignore
```

## 协议要点（实测）
- Endpoint：`POST https://openspeech.bytedance.com/api/v3/tts/unidirectional`
- Headers：`X-Api-Key` / `X-Api-Resource-Id: seed-tts-2.0` / `X-Api-Request-Id: <uuid>` / `Content-Type`
- Body：`{user:{uid}, req_params:{text, speaker, audio_params:{format,sample_rate,speech_rate}, additions?}}`
  - `additions` 是 **JSON 字符串**：`{"context_texts":["语气描述"]}`（仅首元素生效）
- 响应：**NDJSON**，逐行 `{"code":0,"data":"<base64>"}`，结束 `{"code":20000000}`；
  错误两种结构：扁平 `{"code":...,"message":...}` 或 `{"header":{"code":...,"message":...}}`，均兼容。
- 单请求 UTF-8 ≤ ~1000B，长文本按句自动切分后拼接。

## 验证
- 本地（真实 api.py，隔离 venv）：split_text 67 段无损 / parse_ndjson 双错误结构 /
  synthesize 普通·情感·长文切分·错误路径，ffprobe 全部合法 MP3。
- HA 实机：部署 → 重启 → REST config flow 建项（触发真实合成校验通过）→
  `tts.doubao_tts` 实体生成 → `/api/tts_get_url` 经 HA 真实管线产出 6.67s 合法 MP3 →
  `doubao_speech.broadcast` 服务已注册。

## 安全
API Key 仅存于 HA 配置项与本地测试，**绝不入库**；仓库 grep 校验无密钥；建议测后轮换。

## 后续可选
- 旧版 `X-Api-App-Id`+`X-Api-Access-Key` 鉴权兼容（按需）
- 把广播迁到早间播报（替换 `qwen3_speech.broadcast`）
- 声音复刻（`seed-icl-2.0` + `S_xxx`）的便捷选择
