# Analog-factory

A local analog IC workspace with real ngspice execution, traceable results, and an
optional embedded model/CLI agents. The browser UI and CLI share the same project,
task, and artifact services. No model, API key, GPU, or Python package installation
is required for the core workspace.

The workspace includes a real SKY130 five-transistor OTA reference, native editor
coordination, typed visual agent graphs, and optional CPU model-weight training.
The reference measures more than 43 dB gain before and after capacitive extraction.
Training/checkpoint execution is verified; the held-out comparison shows **no
success-rate improvement**. Final integrated acceptance is tracked in
[delivery status](docs/status.md) and the [completion ledger](docs/completion-plan.md).

Application actions now share a structured operation registry across the UI,
assistants, workflows, and external HTTP callers. See the [operation guide and
coverage inventory](docs/operations/README.md) for discovery, invocation, browser
sessions, permissions, examples, and remaining integration boundaries.

## Start

For a fresh **Ubuntu 24.04 LTS x86-64** checkout, use the
[Linux deployment guide](docs/deploy-linux.md). Run as your normal user; the
installer uses `sudo` only for Ubuntu packages. Preview the same options before
installing:

```bash
cd /path/to/Analog-Factory
python3 scripts/install_ubuntu.py --profile desktop --with-agents --dry-run
python3 scripts/install_ubuntu.py --profile desktop --with-agents
python3 scripts/run_deployment.py --config .tools/deployment/config.json
```

Open **http://127.0.0.1:8765**. The `desktop` profile prepares native EDA tools,
SKY130 and an owned browser desktop. `--with-agents` adds project-local Node,
Codex and Claude CLIs; sign in normally on this device before using each agent.
Installation does not log in or change native approval settings. Use
`--profile eda` (the default) for EDA without the managed desktop, or
`--profile core` for the workspace alone. Training is a separate optional
`--with-training` install.

Data defaults to `./.workspace`; tools, deployment configuration and the Python
environment are generated locally. Start from a fresh clone and an empty workspace.
Move existing projects through the application's export/import UI and configure
accounts on the new device. Repeating installation requires the same options
and preserves saved workspace settings and runtime configuration. See the guide
for service management, prepared PDK reuse and validation limits.

For direct source use with Python **3.12+** and your own configured tools:

```bash
python3 -m analog_factory serve --open
# Or choose an explicit workspace:
python3 -m analog_factory --workspace /path/to/data serve --port 8765
```

Simulation requires **ngspice**. Create an **Educational amplifier** project to
inspect waveforms, measurements and logs, or follow the
[five-transistor OTA workflow](docs/ota5-reference.md) with the prepared SKY130
toolchain. The generic SKY130 starter needs its model-library path configured.
Keep one server per workspace and stop foreground deployment with Ctrl+C.

For another computer on your trusted LAN, select the server's concrete local IPv4
address using `--host` during the initial installation, for example:

```bash
python3 scripts/install_ubuntu.py --profile desktop --with-agents --host 192.168.1.14
```

Replace the example address before running it, then start with the saved config
and open **http://192.168.1.14:8765**. `0.0.0.0` and IPv6 listeners are unsupported.
LAN access has no authentication: anyone able to reach the application can edit
projects, execute tools and use its desktop. Use a trusted network and trusted
design inputs; do not expose it as a public or multi-user service.

## Reproduce a real result

```bash
python3 -m analog_factory doctor
python3 -m analog_factory --workspace .validation demo
python3 -m unittest discover -s tests -v
node --check analog_factory/static/app.js
```

`demo` creates an educational common-source amplifier, simulates it, and runs
bounded resistor tuning toward a 1.5 V mean output with a 0.05 V tolerance. Each
iteration has its own source snapshot, log, raw waveform, hashes and measurement
definitions. It leaves source files unchanged. The level-1 model is educational;
these results do not predict SKY130 silicon behavior.

To collect a baseline and archive evidence for a project ID returned by `demo`:

```bash
python3 -m analog_factory --workspace .validation evaluate PROJECT_ID --episodes 3 --seed 42
python3 -m analog_factory --workspace .validation export PROJECT_ID amplifier-evidence.zip
```

`python3 scripts/validate.py` creates a fresh simulation/tuning/evaluation example,
an archive, and a machine-readable validation summary. Unit/integration tests use
only the standard library; tests requiring ngspice skip explicitly when it is absent.
HTTP integration tests require permission to open localhost sockets.

## Workspace features

- Project templates, source editing/history, rename/move, reversible trash, restore,
  and bounded disk-backed ZIP backups/imports including inert training artifacts.
- Operating point, DC, AC, transient/noise; graphical parameter, temperature, supply
  and corner sweeps with measurement definitions and actual run provenance.
  Statistical modes require installed-model qualification; unavailable modes fail.
- Complex/multiple waveforms, phase/dB/log axes, trace ratios, cursors, comparisons
  and CSV. Large rawfiles use bounded display samples; original artifacts and
  exact original-sample statistics remain available separately.
- Immutable run inputs, SHA-256 manifests, tool/PDK identity, stale-result checks,
  process cancellation, deadlines and independent retry records.
- Native Xschem/Magic and headless netlisting, DRC, LVS and capacitance extraction.
  Prepared tools/models are required; templates carry sources, never saved passes.
- v1 scalar tuning and v2 typed visual workflows with branches, bounded loops,
  retries, debug/review controls and JSON/YAML interchange (optional PyYAML).
- Independent educational amplifier/current-mirror tasksets, real EDA rewards,
  replay/baselines and optional TRL GRPO model training with saved checkpoints.
- API and Codex/Claude conversations with model/effort choices, context/images,
  capability-driven streaming/tool support, reviewed actions and bounded human
  authorization. A human-only native PTY retains the CLI's own approval interface.
- Global and per-project tool/PDK/provider configuration, operation discovery,
  acknowledged browser commands and durable deduplication/events.

See [operations](docs/operations/README.md), [workflow graphs](docs/workflow-graphs.md),
[simulation batches](docs/simulation-batches.md), [training](docs/training.md),
[provider capabilities](docs/provider-capabilities.md), and
[artifact transfers](docs/artifact-transfers.md) for contracts and limits.

## Embedded agents

Choose **Model API**, **Codex CLI**, or **Claude Code** in the assistant sidebar,
select a permission mode, and start a conversation. Existing sessions can be
selected again after a page reload or project switch. Attach files with explorer
checkboxes and optionally attach the selected execution's record and log.

Open **Settings → Agent CLIs** to configure project-specific Codex/Claude executable
paths, inspect installed versions, read official release notes, or enable periodic
release checks. These use the shared operations and retain version-conflict guards.
Checks never install software or verify account login; automatic checks run only
while the server is running. See [CLI configuration and updates](docs/agent-cli-updates.md).

The chat follows Netcatty's compact header, continuous transcript, and bottom
composer. **Expand agent workspace** opens the same live conversation in the center
workspace; collapse returns it to the sidebar without losing the draft. Drag the
composer's upper edge, or focus it and use arrows/Home/End, to resize the input.
**Enter** sends; **Shift+Enter** inserts a line break and IME composition stays local.

The history button opens searchable conversations with rename and delete controls.
Export the current conversation as Markdown, JSON, or plain text. Replies support
Markdown, code copying, and collapsible tool arguments/results. **Edit and resend**
restores a turn's message and attached context for review before sending. Model
selection supports search, custom IDs, recent choices, and favorites. Type `/` for
commands and reusable quick messages. The `+` menu selects project context and
imports UTF-8 text attachments (64 KiB each, up to 20 files including project
context). Attachments enter the model context without creating project files.
Drafts and quick messages are cached in this browser; sent conversations remain
in the local workspace. Image attachments and token-by-token API streaming are
not supported by the current transport.

For **Codex CLI**, the model picker reads the installed CLI's model catalog and
can refresh it without sending a prompt. **Claude Code** lists the documented
Opus, Sonnet, and Haiku aliases; account access is checked by the CLI when used.
Keep **CLI default** to inherit its configured model, or choose a specific model.
The **Fast** toggle becomes available for a selected compatible model. Claude
requires version 2.1.205 or later and a supported Opus model. Fast may consume
more usage or incur higher charges, and the CLI can fall back to standard speed.
Changing model or Fast starts a new conversation while preserving the draft;
running conversations lock both controls. Older conversations without a speed
setting display an inherited state and can explicitly switch to standard speed.
These controls affect this application's sessions, without changing CLI defaults.

For **Model API**, configure the existing Model API settings. **Read-only** exposes
project queries. **Confirm each action** permits proposed mutations through the
shared operation registry; approve or reject each action in the sidebar. File
writes use the editor's revision history, and the legacy write tool also presents
a diff. Approvals reject changed project sources. Simulations, workflows and
evaluation use the same execution records as manual runs. The selected model must
support its protocol's function/tool calling to use these tools.

For **CLI agents**, install and log in to the CLI separately, then use its PATH
entry or configure its executable in Settings. Analog Factory passes prompts over
stdin and uses the CLI's own authentication without reading or copying login files.
Because Claude's `--restricted` skips user settings, the adapter reads only the
connection/authentication and model environment allowlist from
`$CLAUDE_CONFIG_DIR/settings.json` (default `~/.claude/settings.json`) into the
Claude child process, and preserves the user default model. Secrets never enter
command arguments or workspace settings. Project settings, hooks and permission
overrides remain excluded; native login storage is handled by Claude itself.
**Read-only** is the default; **Allow project writes** enables mutation proposals,
each approved in the workspace sidebar. Native sessions require the running
workspace server and receive a temporary, project-scoped MCP connection. Both
native backends discover and invoke the same operations as the API agent, including
file revisions and recorded EDA runs. Codex's native sandbox stays read-only with
shell tools disabled; Claude uses `--restricted` with built-in tools disabled and
only the workspace MCP tools allowed. Native credentials stay with the installed
CLI. The MCP turn credential is passed through the child environment and revoked
when the turn ends. CLI session IDs are retained for continuation, and unsupported
CLI options fail visibly.

All backends expose `list_operations`, `describe_operation`, `invoke_operation`,
`get_operation_status`, `cancel_operation`, and `subscribe_events`. Discovery
returns compact availability summaries; descriptions provide complete schemas.
Browser operations require a connected client from `ui.sessions` and return its
acknowledged state. Agents cannot approve their own requests or configure secrets.

The **Stop** button cancels the current turn and terminates CLI process groups or
agent-owned simulations. An in-flight API request may finish after Stop, but its
late response cannot execute tools or enter the transcript; another turn in that
project waits until the request returns. The sidebar polls activity; API text is
shown after each model response rather than token by token. A turn is bounded to
eight API requests or five minutes of CLI execution. Start a new conversation at
the 40-turn/size limit. Agent statements remain suggestions until supported by an
actual run record. See [agent architecture and capability limits](docs/embedded-agents.md).

## Configuration

**Settings → User interface** switches between English and 简体中文, and between
Light and Dark themes. Changes apply immediately and persist in workspace settings.
Chinese interface fonts are bundled for offline use; project sources and tool output
keep their original text. Existing workspaces default to English and dark.

Settings configures executable paths, PDK root/variant and a model provider. Data is
stored in the workspace's `settings.json`; project `config` overrides can be set via
`PUT /api/projects/PROJECT_ID/config`. Tool entries are paths keyed by `ngspice`,
`xschem`, `magic`, or `netgen`. Use the **LVS Netgen**, not the unrelated meshing tool.

In **Settings → Model API**, enter the protocol, base URL, model and API key, then
choose **Save and test connection**. The test lists available models; it does not
send project contents or verify chat support. The model can also be entered manually.
Leave the key field blank to keep the saved key for the same endpoint.

**Remember API key on this server** stores the key in the workspace's private
`.credentials/provider.json` file (directory mode `0700`, file mode `0600`). This file
is not encrypted. Uncheck it for a session-only key that disappears when the server
stops. Keys are bound to their endpoint and protocol; changing either requires a
new key. **Clear saved key** removes the stored key. An explicitly named environment
variable remains available as an optional fallback. Settings and configuration
exports exclude keys. CLI login files are not read or modified by the application;
a launched CLI manages its existing authentication and session storage. Claude's
user connection settings are read as described above, without modifying global
configuration. Model replies are suggestions, not verified design results.

```json
{
  "pdk_root": "/path/to/prepared/pdks",
  "pdk_variant": "sky130A",
  "tools": {"netgen": "/path/to/netgen"},
  "provider": {
    "protocol": "ollama",
    "base_url": "http://127.0.0.1:11434",
    "model": "your-installed-model",
    "timeout": 60
  }
}
```

For an OpenAI-compatible service, select `openai-compatible` and its documented
Chat Completions base URL (commonly ending in `/v1`). Responses and Anthropic Messages
are separate protocols and are not implemented by changing the URL.

See [installation](docs/installation.md), [manual workflow](docs/manual-workflow.md),
[architecture](docs/architecture.md), and the researched [integration matrix](docs/integration-matrix.md).

Licensed under Apache-2.0. EDA tools, PDKs and optional frameworks retain their own licenses.
