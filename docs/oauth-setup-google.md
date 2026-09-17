# Google Antigravity (Cloud Code API) Setup Guide

This guide explains how Quota Dashboard and LiteLLM connect to Google's internal Cloud Code / Antigravity API for Gemini models.

## How it works

Google Antigravity utilizes the internal Cloud Code API (`https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse`) to provide high-tier access to Gemini models.
- **Client ID**: see `.env.example` / `GOOGLE_CLIENT_ID`
- **Loopback Callback Port**: `51121` (`/oauth-callback`)
- **Key Features**: OpenAPI 3.0 schema support via `parametersJsonSchema`, `thoughtSignature` preservation, and extended 64k token outputs.

## Authentication via Quota Desktop

1. Open **Quota Desktop**.
2. Click **Connect** under **Google Antigravity**.
3. Sign in to your Google Account.
4. Quota Desktop captures the authorization code and exchanges it for an offline refresh token.

## Supported Models in LiteLLM

- `gemini-3.8-flash`
- `gemini-3.8-flash-tiered`
- `gemini-3.7-flash`
- `gemini-3.7-flash-tiered`
- `gemini-3.1-pro`
- `gemini-2.5-pro`
- `gemini-2.5-flash`

A bare family name lets the requested effort pick the wire variant
(`gemini-3.8-flash` with `medium` resolves to `gemini-3.8-flash-medium`). A name
that *is* a served variant is honoured as sent: `-tiered` exists upstream and is
what `tieredModelIds.flash` points at, so it is never rewritten to `-low`.

The served set comes from `POST /v1internal:fetchAvailableModels`, minus the
entries the same response lists in `deprecatedModelIds` — that is how
`gemini-3.1-pro-high` appears available while answering
`400 INVALID_ARGUMENT`, which is why high effort on `gemini-3.1-pro` routes to
`gemini-pro-agent`.

`-thinking` variants exist only for `gemini-2.5-flash`. There is no
`gemini-3.8-flash-thinking` or `gemini-3.7-flash-thinking` upstream, so those
names are refused instead of being served by `-low` under the requested name.

## Multimodal Input

Gemini models served through this gateway accept media as well as text. The
accepted client part shapes are:

- `image_url` with a `data:` URI — inlined as-is.
- `image_url` with an http(s) URL — **fetched by the gateway and inlined**. The
  upstream `fileData` field does not accept web URLs (it answers
  `404 Requested entity was not found`), so the bytes have to travel with the
  request.
- `image_url` with a `gs://` or Gemini Files API URI — passed through as
  `fileData`.
- `file` with base64 `file_data` — inlined with the mime type you declare, so a
  PDF sent as `application/pdf` is read as a document.

A tool result (`role: "tool"`) may carry an image too: it is attached to the
`functionResponse` and is visible to the model on every generation this account
serves, with no extra user turn.

Any single piece of media must stay under **12 MB**; above that, and on a fetch
that fails, the request is rejected with the URL and the status rather than
being sent without the image.
