# OpenAI-compatible chat API

Amplify exposes a subset of the OpenAI Chat Completions API so OpenAI SDKs and tools can talk to it
without a custom adapter. It is a thin translation layer in the `amplify-lambda` service
(`chat/openai_compat.py` for translation, `chat/openai_service.py` for the Lambda handlers). Requests are
translated into the payload the existing `/chat` endpoint builds, then run through the same preparation
(`chat/service.py: prepare_chat_payload`): assistant validation, data-source lookup, `SIMPLE`/`COMPLEX`
model aliases, system prompt composition and deployment feature policy.

| Route | Purpose |
|---|---|
| `POST /v1/chat/completions` | Chat completion (non-streaming) |
| `GET /v1/models` | Models the API key can use |

**Base URL:** `<API_BASE_URL>/v1`  **Auth:** `Authorization: Bearer amp-...` (an Amplify API key with `chat`
or `full_access`).

```python
from openai import OpenAI
client = OpenAI(base_url="https://<api-base>/v1", api_key="amp-v1-...")
reply = client.chat.completions.create(model="SIMPLE", messages=[{"role": "user", "content": "Hello"}])
print(reply.choices[0].message.content)
```

## Supported request fields

| Field | Behavior |
|---|---|
| `model` (required) | An Amplify model id, or the aliases `SIMPLE` (cheapest) / `COMPLEX` (most capable). |
| `messages` (required) | Roles `system`, `developer` (treated as `system`), `user`, `assistant`. `content` is a string or an array of `text` parts (joined with newlines). |
| `temperature` | 0-2. Default 1. |
| `max_completion_tokens` / `max_tokens` | Positive integer. `max_completion_tokens` wins if both are sent. Default 4096; clamped to the model's output limit downstream. |
| `stream` | Only `false`/omitted. |
| `user`, `metadata`, `store`, `prompt_cache_key`, `safety_identifier` | Accepted and ignored. |

**Amplify extensions** (OpenAI SDKs send these via `extra_body`): `dataSources` (array of file ids or
`{"id": ...}` objects) and `assistantId`.

## Rejected requests

Anything that cannot be honored returns HTTP 400 with an OpenAI error body naming the `param`, rather than
being silently dropped:

- `stream: true`, `stream_options`
- `tools`, `tool_choice`, `parallel_tool_calls`, `functions`, `function_call`, and messages with role `tool`/`function` or `tool_calls`
- `response_format` other than `{"type": "text"}`
- `n` other than 1, `stop`, `seed`, `logprobs`, `top_logprobs`, `logit_bias`, `top_p` other than 1, non-zero `frequency_penalty`/`presence_penalty`
- `modalities`, `audio`, `prediction`, `reasoning_effort`, `verbosity`, `web_search_options`, `service_tier` other than `auto`
- non-text content parts (images, audio, files); attach files via `dataSources` instead
- message `name`, and any unrecognized field (`code: "unknown_parameter"`)

Library defaults that mean "not requested" (`n=1`, `top_p=1`, zero penalties, `tools=[]`, `stop=null`, ...) are
accepted, because many clients send them unconditionally.

## Responses

A standard `chat.completion` object. Differences from OpenAI:

- **No `usage`.** The chat pipeline does not report token counts back to this layer (the field is optional in OpenAI's client types). Usage is still metered and billed server-side.
- **`finish_reason` is always `stop`**, even if the answer was cut off by `max_tokens`.
- `model` is the resolved model id (an alias is replaced by the real model).
- Requests are not streamed. A long generation holds the connection until it completes (up to the 180 s Lambda limit).

## Errors

Errors use OpenAI's envelope and HTTP status codes:

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": "stream", "code": "unsupported_parameter"}}
```

| Status | When |
|---|---|
| 400 | Invalid or unsupported request |
| 401 | Missing/invalid API key (`invalid_api_key`) |
| 403 | Key lacks chat access, inactive or expired |
| 404 | Model not available to this key (`model_not_found`, `param: model`) |
| 429 | Amplify rate limit reached |
| 502 | The chat pipeline failed or returned nothing |

## Operational notes

- API Gateway: `POST /v1/chat/completions` is added to the `ApiGatewayTimeoutConfig` custom resource in `amplify-lambda/serverless.yml` so it gets the same extended integration timeout as `/chat`. That resource now `DependsOn` the new method (`ApiGatewayMethodV1ChatCompletionsPost`) because the timeout lookup fails if the route does not exist yet.
- These handlers are deliberately not registered with `@api_tool`; that registry also exposes functions as agent ops, which call endpoints with Amplify's `{"data": ...}` envelope.
- Real streaming would require a route on the JS streaming lambda (`amplify-lambda-js`), since API Gateway REST + Python Lambda cannot stream. Token `usage` would additionally need the JS lambda to emit it to the client.
