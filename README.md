# Analog-factory

A local analog IC workspace with real ngspice execution, traceable results, and an
optional read-only model assistant. The browser UI and CLI share the same project,
task, and artifact services. No model, API key, GPU, or Python package installation
is required for the core workspace.

**This is an incremental foundation, not completion of every phase in the project
prompt.** The educational amplifier and scalar tuning loop run with actual ngspice.
SKY130 layout verification, native GUI coordination, external agent CLI execution,
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
The local API rejects foreign browser origins and is bound to loopback. It is not
a remotely hosted or multi-user service.

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
- Optional OpenAI-compatible Chat Completions or native Ollama read-only assistance,
  manual model entry, model discovery/connection check, selected file context and
  recorded responses. Live hosted/local-model inference was not validated here.

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
are read or modified. Model replies are suggestions, not verified design results.

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
