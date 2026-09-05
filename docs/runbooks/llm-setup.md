# LLM setup for 霜月

霜月通过服务端持有的 OpenAI 兼容接口调用模型。密钥只放在 `api` / `worker` 容器环境变量里，前端永不接触。

## Environment

Add these to the private `.env` used by Compose. Leave them empty to run SuperBoss without 霜月（对话页会提示离线，其它页面仍可用）。

```
SUPERBOSS_LLM_BASE_URL=https://api.example.invalid/v1
SUPERBOSS_LLM_API_KEY=
SUPERBOSS_LLM_MODEL=your-model-id
SUPERBOSS_LLM_TIMEOUT_SECONDS=60
```

`BASE_URL` should already include the `/v1` prefix if the vendor requires it. The client posts to `{BASE_URL}/chat/completions`.

Compatible vendors include DeepSeek, Moonshot/Kimi, Tongyi, and Hunyuan OpenAI-compatible endpoints.

## Cost expectations

P0 traffic is one OWNER chatting about internal operations. Budget roughly:

- Input cap per turn: about 16k tokens
- Tool loop: at most 6 rounds
- Memory extract: one extra small call after each turn

A few dozen short Chinese conversations per day typically stay in the low tens of yuan per month on current domestic prices. Watch `agent_messages.token_usage` and the audit log if spend grows.

## Privacy

Finance numbers, project names, and memory text are sent to the vendor as prompt context. Use a vendor that does not train on API data. Do not paste employee passwords or TLS material into chat.

## Restart

After changing `.env`:

```powershell
docker compose --env-file .env -f docker-compose.dev.yml up -d api worker
```
