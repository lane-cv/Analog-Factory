# Provider capabilities and automatic agent scopes

The API adapter supports Chat Completions and native Ollama as distinct protocols.
Streaming follows the provider's framing: [Chat Completions SSE deltas](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)
or [Ollama NDJSON messages](https://docs.ollama.com/api/streaming).
The browser displays partial text, but tool calls execute only after complete,
validated responses. Truncated streams, missing completion markers, refusals and
length termination fail explicitly. A partial response is never silently retried.

The configured default model may declare `capabilities`; other session models use
their exact entry in `model_capabilities`. These declarations describe the user's
selected endpoint/model and are not vendor entitlement or benchmark claims.
Streaming and image support default to false. Existing text/tool integration is
preserved; setting `tools: false` omits tool definitions and rejects unexpected
tool calls. Switching project model removes inherited default-model declarations;
switching endpoint also removes inherited per-model declarations and credentials.

```json
{
  "protocol": "openai-compatible",
  "base_url": "http://127.0.0.1:8000/v1",
  "model": "my-model",
  "timeout": 60,
  "retries": 2,
  "concurrency": 2,
  "stream": true,
  "capabilities": {
    "streaming": true,
    "tools": true,
    "images": true,
    "generation_parameters": ["temperature", "max_tokens"]
  },
  "generation": {"temperature": 0.2, "max_tokens": 2048}
}
```

Only declared generation parameters are sent. Supported controls are temperature,
top-p, maximum output tokens, seed, stop strings, and low/medium/high reasoning
effort. Reasoning effort currently applies only to explicitly compatible Chat
Completions models; it is not translated into arbitrary Ollama thinking modes.
Ollama receives native `options`, with `max_tokens` mapped to `num_predict`.
Defaults omit generation overrides. Unsupported settings fail before inference.
Global settings and project overrides use the same validation and registry schema.

Retries range from zero to three and apply only to explicit HTTP 429/500/502/503/504
responses, within the original timeout. Retry attempts appear in turn events.
Ambiguous connection failures are not retried. Concurrency limits of one to four
are shared by endpoint across provider instances; agent sessions additionally keep
one active turn per project and four per workspace. Stop closes an established
response socket, including a stalled stream. Connection establishment before a
response exists may drain to the configured timeout. Redirects never forward
credentials. Usage remains provider-reported and cost remains unknown.

For a model with declared image support, the composer accepts PNG, JPEG and WebP
attachments: up to four images, 256 KiB each, 512 KiB combined. The server checks
base64, type/signature and size. Images are explicit context, never remote URL
fetches or project-file writes. Chat Completions uses a base64 data URL content
part; Ollama uses its native image array. Text and image attachments share the
20-item context limit. Images remain attached to the saved conversation and can
be restored for another turn. Native CLI attachment support remains text-only.
See [OpenAI image input](https://developers.openai.com/api/docs/guides/images-vision)
and [Ollama chat input](https://docs.ollama.com/api/chat).

Choose **Automatic within scope** to set permitted operations, explicit file paths
or directory prefixes ending in `/`, an action limit, and a run limit. Sending the
first message creates a fixed grant for that conversation. The current allowlist
is `file.write` and `run.start`; automated runs may simulate, DRC, LVS or extract.
Native editors, terminal input, nested jobs, metadata injection, configuration and
approval operations are outside the automatic allowlist. Other actions and spent
budgets use the existing human approval dialog. File writes retain revisions;
normal source-version conflict checks still apply.

Only a local human context with approval authority can create a grant. Agent
confirmation flags do not grant that authority, and forwarded browser commands
cannot create or renew automatic grants. Grants are private workspace records
bound to project, session and exact scope; copied or edited session JSON has no
authority. Each automatic attempt spends its budget durably before execution,
including failed attempts. Counters survive restart and are not reset by a new
turn. Imported project conversations do not import automatic authority.

Validation uses local deterministic HTTP fixtures for SSE/NDJSON, fragmented tool
arguments, cancellation, retries, image wire shapes and scope attacks. The browser
acceptance exercises the actual sidebar, partial text, image attachment, and an
actual scoped file write. A separate regression runs real ngspice under an
automatic run grant. No paid provider calls or provider performance claims are
part of this evidence.
