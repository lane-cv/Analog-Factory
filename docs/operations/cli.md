# Operation CLI

The normal `doctor`, `create`, `projects`, `run`, `tune`, `evaluate`, `demo`, and
`export` commands use the shared operation registry. The `operations` command
exposes discovery and the same structured result envelopes as the HTTP API.

```bash
python3 -m analog_factory --workspace .workspace operations list --module file
python3 -m analog_factory --workspace .workspace operations describe run.start
python3 -m analog_factory --workspace .workspace operations invoke project.create \
  --parameters '{"name":"Example amplifier"}' --confirm
```

`list` accepts `--module`, `--keyword`, `--project`, and `--parameters` for contextual
availability. `describe` includes schemas, permissions, defaults, examples,
preconditions, and side effects. JSON parameters must be an object. `--project`
binds the command to a project and must agree with any `project_id` parameter.
`--read-only` restricts invocations to read operations.

`invoke` requires `--confirm` for mutations whose descriptor requires agent
confirmation. Versioned writes also require `--expected-version` using the
`version` returned by a previous read. An optional `--idempotency-key` avoids
repeating the same logical request within the durable journal retention window.
The same workspace and caller identity are required for replay across commands.
An interrupted ambiguous call reports OUTCOME_UNKNOWN; inspect state before using
a fresh request key. Use running server mode for live asynchronous jobs and browser
commands, whose active execution/session ownership belongs to that process.

```bash
python3 -m analog_factory --workspace .workspace operations invoke project.detail \
  --project PROJECT_ID
python3 -m analog_factory --workspace .workspace operations invoke file.write \
  --project PROJECT_ID \
  --parameters '{"path":"reports/design-notes.txt","content":"Reviewed bias measurements.\n"}' \
  --expected-version VERSION_FROM_READ --idempotency-key review-notes-1 --confirm
```

## Running server mode

Use `--url` to invoke an already-running workspace server. The option can appear
before the operation subcommand or after its arguments. This mode does not create
or modify a local workspace directory.

```bash
python3 -m analog_factory operations --url http://127.0.0.1:8765 list --module run
python3 -m analog_factory operations invoke run.start --url http://127.0.0.1:8765 \
  --project PROJECT_ID --parameters '{"kind":"simulate","options":{"netlist":"testbenches/pre.cir"}}' \
  --confirm
python3 -m analog_factory operations status run:PROJECT_ID:RUN_ID \
  --url http://127.0.0.1:8765
python3 -m analog_factory operations cancel run:PROJECT_ID:RUN_ID \
  --url http://127.0.0.1:8765 --confirm
```

Set the client environment variable `AF_OPERATION_TOKEN` to the server's
configured agent token. `AF_AGENT_TOKEN` is also accepted as a client fallback.
Tokens are sent as a bearer header and are never printed or placed in command-line
arguments. Redirects are not followed, so credentials are not forwarded to another
endpoint. The server's token grants and project binding remain authoritative;
`--confirm` and `--read-only` cannot add server permissions. Without a token, the
server's existing trusted local-owner connection policy applies.

Remote invocation returns an accepted asynchronous task immediately. The running
server continues to own it after the CLI exits; inspect or cancel it using the
returned `task_id`. For frontend operations, the server also needs the selected
browser client to be connected and to acknowledge the command.

Local invocation waits for asynchronous work before exiting, because its workers
belong to that process. This includes GUI EDA launches, which can run until the
editor closes or the configured timeout expires. Use `--url` for background tasks
and interactive sessions that need another client to inspect or approve progress.

An accepted task is not a successful simulation. Inspect final `status`, metrics,
verification, staleness, and logs. CLI exit code is nonzero for rejected, failed,
cancelled, timed-out, interrupted, or unavailable outcomes. Binary export and
artifact operations return `content_base64`; the ordinary `export` command writes
the decoded ZIP directly to the requested output file.
