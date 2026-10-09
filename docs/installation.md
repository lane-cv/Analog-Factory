# Installation and environment validation

Use the [Linux deployment guide](deploy-linux.md) for a fresh Ubuntu 24.04 LTS
x86-64 machine. It covers cloning, installation, startup and user-service management.
The installer also accepts Ubuntu 26.04 x86-64; other distributions require manual
dependency setup. Its new validation checklist is separate from the earlier
[Ubuntu replay](deployment-ubuntu24.md) and [host toolchain evidence](local-toolchain.md).

## Core and desktop

The core uses Python 3.12+ and its standard library. Direct source use requires no
Python dependency download. A modern browser with Canvas, Fetch and SVG support
is needed. The deployment installer creates `.venv`, installs the application and
YAML support there, and configures a new workspace through the shared operation
registry. Node is optional for the core; it is required by the optional native agents.

| Installer option | Result |
| --- | --- |
| `--profile core` | Application and workspace; no EDA, PDK or managed desktop installation. |
| `--profile eda` | Default: core plus ngspice, LVS Netgen, local Magic/Xschem and prepared SKY130. |
| `--profile desktop` | EDA plus an owned Xvfb/Fluxbox/noVNC desktop for native editors. |
| `--with-agents` | Optional local Node 22.23.3, Codex 0.159.2 and Claude Code 2.1.280; no login. |
| `--with-training` | Optional separate CPU training runtime and pinned model under `.tools/training`. |

The desktop profile does not require an existing GNOME session. Manually launched
native editors need an X11 or XWayland display. The historical Ubuntu 24.04 replay
includes native GUI lifecycle checks; acceptance of this new installation workflow
must be recorded separately.

For the small educational example, a 2-core CPU, 4 GB RAM and 1 GB free space are
starting estimates for core use, not measured limits or a complete EDA installation
budget. Tools, PDKs and run archives need several additional GiB. Large simulations
need circuit-dependent RAM. Manual EDA and the implemented CPU trainer need no GPU.

Preview a complete plan without downloads, writes or sudo:

```bash
python3 scripts/install_ubuntu.py --profile desktop --with-agents --dry-run
```

The installer uses apt for host dependencies, ngspice and `netgen-lvs`. Magic 8.3.684
and Xschem 3.4.7 are built locally from verified source pins against the host's
development libraries. Apt versions depend on the configured Ubuntu repositories.
The similarly named `netgen` mesh-generator package does not implement LVS. The
installer selects the LVS executable and records explicit tool paths in the new
workspace. These builds must be repeated on each target machine.

After installation:

```bash
.venv/bin/python -m analog_factory --workspace .workspace doctor
```

Use the chosen workspace path if you changed `--workspace`. Record executable
paths, versions and actual circuit reports before adopting a combination. A doctor
report or successful install does not replace the complete reference-design flow.

## Prepared SKY130

Raw `skywater-pdk` source is not a prepared installation. EDA/desktop installation
downloads the checked Ciel analog subset at open_pdks revision
`1689ac3f2dc763876eaf967227c7dfe831b031ae` by default, under
`.tools/pdks/sky130/1689ac3f2dc763876eaf967227c7dfe831b031ae`. Its root contains `sky130A`.

To reuse an installation prepared separately on this machine, pass
`--pdk-root /absolute/path/to/PDK_ROOT` with EDA or desktop during the first install.
The installer requires these files under `PDK_ROOT/sky130A`:

```text
libs.tech/xschem/xschemrc
libs.tech/ngspice/sky130.lib.spice
libs.tech/combined/sky130.lib.spice
libs.tech/magic/sky130A.magicrc
libs.tech/netgen/sky130A_setup.tcl
```

The installer records the PDK root, variant, revision and tool paths in the new
workspace. The five-transistor OTA template uses that prepared PDK. For a generic
SKY130 starter or imported source project, verify the model-library path and corner
in its testbench. The default subset excludes digital standard-cell, I/O and SRAM
libraries. File presence establishes readiness for further validation, not model
accuracy or foundry signoff. Follow the [OTA reference workflow](ota5-reference.md)
and retain actual reports.

## AI and training isolation

An API assistant needs an explicitly configured endpoint, protocol, model and key
in **Settings → Model API**. **Save and test connection** checks model listing
without sending project contents. **Remember API key on this server** stores it
in the workspace's private, unencrypted `.credentials/provider.json` file; uncheck
it for session-only storage that disappears when the service stops. Saved keys
are excluded from configuration exports and bound to the endpoint and protocol.
A selected environment variable is an optional fallback. See
[provider capabilities](provider-capabilities.md) for protocol and model limits.

With `--with-agents`, the local CLI launchers are `.tools/agent-clis/bin/codex` and
`.tools/agent-clis/bin/claude`; they retain the selected local Node runtime. Sign in
normally on each device using the same OS account as the running service. The
installer verifies versions, not login. It does not copy native login files or
change approval settings.

CPU model-weight training is implemented as an optional TRL GRPO/LoRA worker.
Install it with `--with-training` during deployment, or separately with
`python3 scripts/setup_training.py`. It uses its own pinned Python 3.12 environment
and dependencies under `.tools/training`; the core never imports torch or TRL.
The documented runtime/model installation occupied approximately 2.3 GiB before
checkpoints and run artifacts. Actual pilots changed weights and reloaded
checkpoints, but did **not** improve held-out electrical success over the base
model. Setup alone establishes neither successful training nor improved design
quality. See [training evidence and resource limits](training.md).

## Existing projects

The installer refuses an existing nonempty workspace without its matching
deployment record. Use a fresh clone and empty workspace, export projects through
the old application's UI, then import them into the new deployment. Reinstall
tools and configure accounts locally; do not copy `.tools`, `.workspace`, virtual
environments or native login state between machines.

Repeating the same installation options preserves saved workspace settings and
runtime configuration. Use the [deployment validation checklist](deploy-linux.md#validation-status-and-checklist)
to qualify the new machine and retain the source revision, installation manifests
and actual circuit reports.
