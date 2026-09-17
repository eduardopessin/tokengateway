# LiteLLM Wire Bridge Plugin (OMP 1:1 Compatible)

This plugin enables transparent OAuth proxying and first-party CLI wire protocol translation inside LiteLLM for **Anthropic Claude Max**, **OpenAI ChatGPT Plus (Codex Responses API)**, and **Google Antigravity (Cloud Code API)**.

## What it does

- **OpenAI Codex Responses API (`gpt-5.*`, `codex/*`)**: Maps chat completion messages and tool schemas 1:1 with `@oh-my-pi/pi-ai` wire transformers (`input_text`, `output_text`, `function_call`, `function_call_output`), supporting async SSE streaming via `httpx` without token duplication or dropped chunks.
- **Google Antigravity (`gemini-*`, `antigravity/*`)**: Emits `parametersJsonSchema` for OpenAPI 3.0 tool declarations, handles `functionCall` / `functionResponse`, preserves `thoughtSignature` across turns (with `skip_thought_signature_validator` fallback), and enforces `maxOutputTokens: 64000`.
- **Anthropic Claude Max (`claude-*`)**: Keeps the required Claude Agent SDK identity as the sole system message. Client system instructions move to a `cache_control: ephemeral` block in the first user turn, avoiding OAuth `429` rejections while preserving prompt-cache hits. Also normalizes temperature for extended thinking and ensures `max_tokens > budget_tokens`.
- **Reasoning passthrough**: Keeps the chain-of-thought the subscriptions bill for. Drops `redact-thinking-2026-02-12` (which makes Anthropic return signed but empty thinking blocks), sends `thinking: {type: "adaptive", display: "summarized"}` on Claude 4.7+/5.x (whose `display` default is `"omitted"`, i.e. silent reasoning), always sends Codex a `reasoning` object, and always sends Antigravity a `thinkingConfig`. Reasoning surfaces as `reasoning_content` on chat completions and as `reasoning` items on `/v1/responses`.
- **`/v1/responses` support**: Clients that discover models through LiteLLM call OpenAI-backed models over the Responses API. That route bypasses `acompletion`, so it is flagged at `route_request` and translated into chat completions, which is where the bridges live.
- **Credential consumer, never owner**: Reads the tokens the agent (quota-dashboard or the desktop app) syncs into the Kubernetes Secret, or from the `credentials.json` it writes on the shared volume; re-reads every 10s so a rotation becomes live without a restart. It never exchanges or writes credentials: two independent refreshers racing on single-use rotating refresh tokens produce an `invalid_grant` loop that forces a manual re-login.
- **Honest failures**: A model name the subscription does not serve returns the upstream error instead of being answered by a different model.

## Installation

### In Docker / Docker Compose

Mount `sitecustomize.py` into `/app/patch` and set `PYTHONPATH=/app/patch`:

```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm-database:main-latest
    environment:
      - PYTHONPATH=/app/patch
    volumes:
      - ./sitecustomize.py:/app/patch/sitecustomize.py:ro
      # The agent writes credentials.json here; the plugin reads it. Env vars
      # are optional and only useful for a one-off manual token.
      - quota_data:/app/quota-data:ro
```

### In Kubernetes

Apply as a ConfigMap and mount as volume:

```yaml
volumeMounts:
  - name: litellm-patch
    mountPath: /app/patch
volumes:
  - name: litellm-patch
    configMap:
      name: litellm-mcp-patch
```

## Credits & References

- **[LiteLLM Proxy](https://github.com/BerriAI/litellm)**: The core LLM gateway architecture.
- **[Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi)**: The reference coding harness whose `@oh-my-pi/pi-ai` wire transformers inspired our OpenAI Responses API and Google Antigravity bridge implementations.
