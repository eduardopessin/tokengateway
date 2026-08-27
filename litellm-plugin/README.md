# LiteLLM Wire Bridge Plugin (OMP 1:1 Compatible)

This plugin enables transparent OAuth proxying and first-party CLI wire protocol translation inside LiteLLM for **Anthropic Claude Max**, **OpenAI ChatGPT Plus (Codex Responses API)**, and **Google Antigravity (Cloud Code API)**.

## What it does

- **OpenAI Codex Responses API (`gpt-5.*`, `codex/*`)**: Maps chat completion messages and tool schemas 1:1 with `@oh-my-pi/pi-ai` wire transformers (`input_text`, `output_text`, `function_call`, `function_call_output`), supporting async SSE streaming via `httpx` without token duplication or dropped chunks.
- **Google Antigravity (`gemini-*`, `antigravity/*`)**: Emits `parametersJsonSchema` for OpenAPI 3.0 tool declarations, handles `functionCall` / `functionResponse`, preserves `thoughtSignature` across turns (with `skip_thought_signature_validator` fallback), and enforces `maxOutputTokens: 64000`.
- **Anthropic Claude Max (`claude-*`)**: Keeps the required Claude Agent SDK identity as the sole system message. Client system instructions move to a `cache_control: ephemeral` block in the first user turn, avoiding OAuth `429` rejections while preserving prompt-cache hits. Also normalizes temperature for extended thinking and ensures `max_tokens > budget_tokens`.
- **Atomic Token Manager**: Automatically refreshes expired access tokens in memory and atomically persists them to Kubernetes Secrets (`litellm-secrets` and `quota-dashboard-credentials`).

## Installation

### In Docker / Docker Compose

Mount `sitecustomize.py` into `/app/patch` and set `PYTHONPATH=/app/patch`:

```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm-database:main-latest
    environment:
      - PYTHONPATH=/app/patch
      - ANTHROPIC_OAUTH_TOKEN=${ANTHROPIC_OAUTH_TOKEN}
      - ANTHROPIC_REFRESH_TOKEN=${ANTHROPIC_REFRESH_TOKEN}
      - OPENAI_CODEX_OAUTH_TOKEN=${OPENAI_CODEX_OAUTH_TOKEN}
      - OPENAI_CODEX_REFRESH_TOKEN=${OPENAI_CODEX_REFRESH_TOKEN}
      - GOOGLE_ANTIGRAVITY_OAUTH_TOKEN=${GOOGLE_ANTIGRAVITY_OAUTH_TOKEN}
      - GOOGLE_ANTIGRAVITY_PROJECT_ID=${GOOGLE_ANTIGRAVITY_PROJECT_ID}
    volumes:
      - ./sitecustomize.py:/app/patch/sitecustomize.py:ro
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
