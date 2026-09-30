# Analog-factory

A local analog IC workspace with real ngspice execution, traceable results, and an
optional embedded model/CLI agents. The browser UI and CLI share the same project,
task, and artifact services. No model, API key, GPU, or Python package installation
is required for the core workspace.

**This is an incremental foundation, not completion of every phase in the project
prompt.** The educational amplifier and scalar tuning loop run with actual ngspice.
SKY130 layout verification, native GUI coordination, full external agent validation,
general agent graphs, and model-weight training have not passed their acceptance gates.
See [delivery status](docs/status.md) and [validation evidence](docs/validation.md).

## Start

Requires Python **3.12+**. To simulate, install **ngspice** and place it on `PATH`.
The initial deployment target is Ubuntu 24.04 LTS with GNOME/X11 or GNOME/Wayland
plus XWayland for native EDA editors. Actual development validation used Ubuntu 26.04,
Python 3.14.4 and ngspice 46; Ubuntu 24.04 acceptance is still pending.

```bash
cd /path/to/Analog-Factory
python3 -m analog_factory serve --open
```

Open **http://127.0.0.1:8765**. Create an **Educational amplifier** project, run the
pre-layout simulation, and inspect Simulation for waveforms, measurements, and logs.
The SKY130 template requires a prepared PDK and an edited model library path.

Data defaults to `./.workspace`. An explicit directory is supported:

```bash
python3 -m analog_factory --workspace /path/to/data serve --port 8765
```

Keep one workspace server per data directory. Stop with Ctrl+C after cancelling
active jobs. EDA programs run as the current user; run trusted design inputs only.
The API rejects foreign browser origins and binds to loopback by default.

To access the workspace from another computer on your trusted LAN, explicitly bind
to the server's LAN IPv4 address (replace this example with your server's address):

```bash
python3 -m analog_factory serve --host 192.168.1.14 --port 8765
# With the existing graphical EDA desktop:
./scripts/serve_with_desktop.sh --host 192.168.1.14 --port 8765
```

Open **http://192.168.1.14:8765** from the other computer. Choose one launch command
and stop the previous workspace server first. Use a concrete address; `0.0.0.0`
and IPv6 listeners are not supported. LAN access has no login: anyone able to reach
the port can edit projects, run tools and access the configured desktop. Use it only
on a trusted network; this is not a public or multi-user service.

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

- Project templates, source tree, text editing/import, saved revisions and restore.
- Actual operating-point, DC, AC, transient and noise analysis through editable
  SPICE decks. PVT/statistical analysis requires manual directives and validated models.
- Waveform traces, cursor values, zoom/pan, CSV download, measurement provenance,
  run comparisons, tool logs and artifact downloads.
- Input snapshots, SHA-256 hashes, tool/PDK metadata, stale-result checks, process-group
  cancellation, timeouts and fresh execution records for retries.
- Native xschem/magic launch adapters and headless netlisting, DRC, LVS and extraction
  adapters. These require actual installed tools and prepared PDK assets; no passes
  are supplied with the project.
- A draggable workflow graph, JSON save/load/export, and an executable bounded scalar
  tuning chain. Unsupported graphs fail explicitly.
- Isolated evaluation episodes with real ngspice measurements and a seeded random
  search baseline. This is circuit evaluation, **not model training**.
- Embedded project conversations with OpenAI-compatible Chat Completions, native
  Ollama, Codex CLI and Claude Code. Durable sessions, selected file/run context,
  visible tool activity, cancellation, and permission controls share one sidebar.
  API agents can read files, propose reviewed edits and run recorded simulations.
  A minimal authenticated Codex conversation has been verified. Live model API,
  Claude and CLI project-editing acceptance remain open.

## Embedded agents

Choose **Model API**, **Codex CLI**, or **Claude Code** in the assistant sidebar,
select a permission mode, and start a conversation. Existing sessions can be
selected again after a page reload or project switch. Attach files with explorer
checkboxes and optionally attach the selected execution's record and log.

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

For **Model API**, configure the existing Model API settings. **Read-only** exposes
project reading tools. **Confirm each action** additionally proposes file writes and
simulations; approve or reject each action in the sidebar. Write proposals include
a diff, reject stale source changes, and use the editor's revision history for
restore. Simulations use the same execution records as manual runs. The selected
model must support its protocol's function/tool calling to use these tools.

For **CLI agents**, install and log in to the CLI separately, then use its PATH
entry or configure its executable in Settings. Analog Factory passes prompts over
stdin and uses the CLI's own authentication without reading or copying login files.
**Read-only** is the default; **Allow project writes** authorizes the CLI's available
write tools for that session. CLI session IDs are retained for native continuation.
Codex uses its `read-only` or `workspace-write` sandbox with interactive approvals
disabled. Claude requires `--restricted` support and uses only confined Read/Glob/
Grep tools, plus Edit/Write in edit mode; it cannot run shell commands through this
adapter. Unsupported CLI options fail visibly. There is no per-tool approval UI
for CLI sessions. Review CLI edits in the source editor; API writes use application
revisions, while CLI writes are managed by the CLI.

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
exports exclude keys. No CLI authentication files or global agent configuration
are read or modified by the application; a launched CLI manages its own existing
authentication and session storage. Model replies are suggestions, not verified design results.

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
