# Installation and environment validation

For the tool installation subsequently configured on this checkout, see
[configured local EDA tools](local-toolchain.md).

## Core and desktop

The core uses Python 3.12+ and its standard library. Running from this checkout
requires no dependency download. A modern browser with Canvas, Fetch and SVG support
is needed. Node is only used for a JavaScript syntax check, not to start the application.

Ubuntu 24.04 LTS is the initial support target, with GNOME on X11 or Wayland plus
XWayland. This checkout was actually tested on Ubuntu 26.04, Python 3.14.4 and ngspice 46.
The target-version and native desktop acceptance tests remain open.

For the small educational example, a 2-core CPU, 4 GB RAM and 1 GB free space are
reasonable starting estimates, not measured limits. PDKs and run archives require
additional disk space, often several GB. Large simulations need circuit-dependent RAM.
A GPU is not required for any implemented manual or evaluation feature.

Optional distribution packages on Ubuntu:

```bash
sudo apt-get update
sudo apt-get install python3 ngspice xschem magic netgen-lvs
```

These commands install distro versions; they are **not a validated version lock**.
Depending on packaging, the LVS executable may be `netgen`, `netgen-lvs`, or installed
under `/usr/lib/netgen/bin/`. Configure its explicit path in Settings. The similarly
named `netgen` mesh-generator package does not implement LVS. The doctor rejects a
recognized mesh-generator executable.

```bash
python3 -m analog_factory doctor
python3 -m analog_factory --workspace .validation demo
```

Record the detected executable paths and versions before adopting a combination.
`scripts/validate.py` writes observed host/tool information and executable hashes.
No all-tools-plus-PDK combination is marked validated until the complete manual
reference design passes with archived reports.

## Prepared SKY130

Raw `skywater-pdk` source is not a usable installation for this application by itself.
Build it with open_pdks or use a prepared distribution such as Ciel. Volare is archived;
consult the [current upstream matrix](integration-matrix.md) for sources and versions.

Choose an immutable available open_pdks build revision from Ciel, install it into
an explicit PDK root, and record the revision and download checksum. Do not rely on
a changing user-wide default. This project does not invent a validated build ID.
The detector checks these files under `PDK_ROOT/sky130A`:

```text
libs.tech/xschem/xschemrc
libs.tech/ngspice/sky130.lib.spice
libs.tech/magic/sky130A.magicrc
libs.tech/netgen/sky130A_setup.tcl
```

Configure `pdk_root` and `pdk_variant` in workspace Settings, or explicitly export
`PDK_ROOT` and `PDK` before starting. Update the SKY130 template's model library path
to the installed `sky130.lib.spice`. The current template intentionally fails until
that real path is configured. Model paths are not guessed or silently downloaded.

Configuration-file presence indicates readiness for further validation, not model,
rule or signoff correctness. Run the full reference flow and retain tool reports.

## AI and training isolation

The assistant needs an explicitly configured service. Local Ollama uses its native
`/api/chat` and `/api/tags` interfaces; OpenAI-compatible endpoints use Chat Completions
and model listing. Check the chosen provider's actual protocol and model capabilities.
Configure the endpoint, model and API key directly in **Settings → Model API**.
**Save and test connection** saves the form and retrieves the model list without
sending project contents. Keep **Remember API key on this server** checked to store
the key in the workspace's `.credentials/provider.json` (private directory `0700`,
file `0600`, not encrypted), or uncheck it for session-only storage. Session-only
keys disappear when the service stops. Saved keys are excluded from settings and
configuration exports and are bound to the endpoint and protocol. A selected
environment variable is an optional fallback; the app never searches for credentials.

Model-weight training is not implemented in this delivery. Do not install training
dependencies into the core environment. Current AReaL and AgentFly requirements exclude
Python 3.13+, so plan separate pinned Python 3.12 environments/containers. Agent Lightning
v1 has a different architecture from older examples. The PyPI package named `agentfly`
is unrelated to the requested Agent-One-Lab project. Framework installation, accelerator
availability and saved checkpoint validation are future gates, documented in the matrix.
