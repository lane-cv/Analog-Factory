# Deploy on a new Ubuntu machine

This guide installs a separate deployment on **Ubuntu 24.04 LTS x86-64**. Run as
the normal user who will own the workspace and agent accounts. Only Ubuntu
package installation uses sudo. The installer also accepts Ubuntu 26.04 x86-64;
other distributions and ARM need separate setup.

The private repository is `lane-cv/Analog-Factory`. Your GitHub account needs
access. Clone the published source commit, then install on the target device;
see the [validation status and limits](#validation-status-and-checklist).

For the Linux Docker setup used on the current device, see the separate
[local container deployment template](../deployment/local-container/README.txt).
It uses the IIC OSIC tools image and private project-local CLI configuration;
its existing-device evidence is distinct from a fresh template installation.

## Clone, install and start

Choose a new destination. Authenticate through your normal Git workflow without
putting a token in the clone URL or copying another device's credentials:

```bash
sudo apt-get update
sudo apt-get install -y git python3
git clone https://github.com/lane-cv/Analog-Factory.git
cd Analog-Factory
python3 scripts/install_ubuntu.py --profile desktop --with-agents --dry-run
python3 scripts/install_ubuntu.py --profile desktop --with-agents
python3 scripts/run_deployment.py --config .tools/deployment/config.json
```

Open **http://127.0.0.1:8765** on that machine. The installer prepares Python,
EDA tools, SKY130, a browser desktop and the optional agent binaries. It does not
start the application or log in to agent accounts. The last command starts the
foreground deployment; keep its terminal open and use Ctrl+C to stop it and its
owned desktop processes.

The desktop uses Xvfb, Fluxbox, x11vnc and noVNC, so an existing graphical login
or container desktop is unnecessary. Open **Drawing desktop / 图形桌面** in the
application for native editor windows. Its desktop backend listens on loopback
and is reached through the application; do not separately publish that port.

Install and run as the same normal user, with one server per workspace. The runner
locks its workspace and refuses an occupied listener. It uses Linux subreaper
and pidfd support to stop only its own descendants, including workers that create
independent sessions. Stop a previous foreground
server or service before starting another; do not run the application with sudo.

## Profiles and options

| Profile | Installed components |
| --- | --- |
| `core` | Python environment, application and YAML support; no automatic simulator, PDK or desktop. |
| `eda` | Default. Core, system ngspice/LVS Netgen, local Magic 8.3.684/Xschem 3.4.7 and prepared SKY130. |
| `desktop` | EDA plus the managed browser desktop and its Ubuntu dependencies. |

`--with-agents` and `--with-training` are independent opt-ins. Training still needs
a working simulator; adding its runtime to `core` does not install ngspice.

| Option | Behavior |
| --- | --- |
| `--workspace PATH` | New workspace; defaults to this clone's `.workspace`. |
| `--host IPv4` | Concrete local IPv4; default `127.0.0.1`. Wildcard `0.0.0.0` and IPv6 are unsupported. |
| `--port PORT` | Application port; default `8765`. |
| `--desktop-port PORT` | Loopback noVNC backend; default `6080`, distinct from the application port. |
| `--jobs N` | Native build jobs, 1–64; default 2. |
| `--pdk-root PATH` | Reuse an existing prepared SKY130 root; EDA/desktop only. |
| `--skip-system-packages` | Skip apt when all dependencies for the chosen profile are already installed. |
| `--skip-download` | Require cached EDA/PDK/Node archives. pip, npm and training have separate downloads/caches; this is not an offline-install guarantee. |
| `--dry-run` | Print the plan without writes, downloads or sudo; does not verify installed dependencies. |

Choose the complete plan before the first installation. For example, in a fresh
clone with host dependencies and a prepared PDK already available:

```bash
python3 scripts/install_ubuntu.py --profile desktop --with-agents \
  --skip-system-packages --pdk-root /absolute/path/to/prepared/PDK_ROOT
```

The PDK root must contain the `sky130A` files in
[installation prerequisites](installation.md#prepared-sky130), including the
combined SPICE library. Default setup uses the checked analog subset at open_pdks
revision `1689ac3f2dc763876eaf967227c7dfe831b031ae`; it excludes digital standard-cell,
I/O and SRAM libraries.

## Dependencies, downloads and resources

The dry-run output lists all apt packages. Core needs Python, venv support,
CA certificates and Git. EDA adds C/C++ build tools, flex/bison, pkg-config,
Tcl/Tk and X11/Cairo development libraries, ngspice, `netgen-lvs` and zstd.
Desktop adds Xvfb, xauth, Fluxbox, x11vnc, websockify, noVNC and fonts. The package
named `netgen` may be an unrelated mesh generator; circuit LVS needs `netgen-lvs`.

Magic/Xschem commits and SHA-256 hashes are pinned in
[`deployment/ubuntu24/sources.json`](../deployment/ubuntu24/sources.json). They are
built against this machine's libraries. Apt versions come from its configured
Ubuntu repositories; this installer does not use the frozen package closure of
the separate [Ubuntu acceptance image](deployment-ubuntu24.md).

Initial setup needs Ubuntu mirrors, Python package indexes, GitHub source/release
downloads and SKY130 artifacts. Agents additionally need nodejs.org and npm;
training adds its own Python/runtime, CPU packages and model downloads. Native
compilation and model downloads can take time. Allow the installer to finish.

Reserve several GiB for tools, archives and the PDK, then additional space for
projects, waveforms and run history. The documented training runtime/model occupied
approximately 2.3 GiB before checkpoints. These are planning estimates, not measured
minimum requirements for the combined deployment. Two build jobs are the default;
increase `--jobs` only when CPU and memory permit. Large simulations and training
have separate memory demands. Manual EDA and the implemented CPU trainer need no GPU.

## Project-local agents and login

`--with-agents` installs Node **22.23.3**, Codex **0.159.2** and Claude Code
**2.1.280** under `.tools`. The launchers retain that Node runtime without changing
global npm packages or your shell profile:

```bash
.tools/agent-clis/bin/codex --version
.tools/agent-clis/bin/claude --version
```

Run each CLI normally and follow its own sign-in flow on this device:

```bash
.tools/agent-clis/bin/codex
.tools/agent-clis/bin/claude
```

Use the same OS account for login and the application service. Version verification
does not verify account access. The installer does not make model requests, copy
login state or change native approval rules. Native controls and the application's
per-action review remain in place. Available models and usage depend on your account.

The new workspace receives these executable paths through the shared settings
operation. Later changes belong in **Settings → Agent CLIs**. An API provider can
instead be configured in **Settings → Model API**. Manual design requires neither.

## Trusted LAN access

On the initial installation, choose an IPv4 address assigned to the server.
Replace this example before running:

```bash
python3 scripts/install_ubuntu.py --profile desktop --with-agents \
  --host 192.168.1.14 --dry-run
python3 scripts/install_ubuntu.py --profile desktop --with-agents \
  --host 192.168.1.14
python3 scripts/run_deployment.py --config .tools/deployment/config.json
```

Open **http://192.168.1.14:8765** from the other computer. The firewall must permit
the intended trusted clients. There is no application login or network-user
isolation: anyone reaching this port can edit projects, execute tools and access
the desktop. Use trusted inputs and a trusted LAN; do not expose it to the public
internet. Browser origin checks do not provide authentication.

For an already installed deployment, stop it and edit `host`/`port` in
`.tools/deployment/config.json`, then restart. Rerunning the installer with changed
options is not the runtime reconfiguration mechanism.

## Optional systemd user service

After checking foreground startup, stop it with Ctrl+C. From the clone, run as
its normal user:

```bash
python3 scripts/manage_service.py print --config .tools/deployment/config.json
python3 scripts/manage_service.py install --config .tools/deployment/config.json
python3 scripts/manage_service.py start --config .tools/deployment/config.json
python3 scripts/manage_service.py status --config .tools/deployment/config.json
```

The helper creates a workspace-specific unit under your systemd user configuration.
It preserves unrelated units and refuses foreign units at its expected path.
A functioning systemd user manager is required; this is not a root service.

| Command | Effect |
| --- | --- |
| `print` | Display the unit without installing it. |
| `install` | Write the managed unit and reload the user manager; do not start. |
| `start` / `stop` / `restart` | Explicitly control the deployment and its owned children. |
| `status` | Display service status. |
| `enable` | Enable startup with the user's default target; **does not start it now**. |
| `disable` | Disable future automatic startup; does not stop a running service. |
| `uninstall` | Disable and stop the managed unit, remove it and reload; retain application files and workspace data. |

For example:

```bash
python3 scripts/manage_service.py enable --config .tools/deployment/config.json
python3 scripts/manage_service.py restart --config .tools/deployment/config.json
python3 scripts/manage_service.py stop --config .tools/deployment/config.json
```

Availability outside a logged-in user session depends on systemd user-session
policy. The helper does not change that policy or enable lingering. Keep the
clone and its configured absolute paths in place while the unit is installed.

## Existing work and repeat installation

First installation needs an absent or empty workspace and configures it through
the shared operation registry. An existing nonempty workspace or unknown Python
environment is refused. A matching deployment record permits repeating **the same
installation options**, preserving workspace settings and runtime configuration.
Completed phases are recorded in `.tools/deployment/installation.json`.

Changing profile, host, flags or another install option changes the plan and is
rejected. Use a fresh clone for a different installation plan. Change runtime
host/port in the stopped deployment's config and application settings through the
UI. If a failed component leaves an incomplete directory, preserve it for inspection
and use a fresh clone when reuse is refused. Do not delete a working deployment
or its data to bypass a guard.

Move work using the existing project's export UI and the new application's import
UI. Review imported tool/PDK paths and rerun checks locally. Archives neither
install tools nor transfer accounts. Do not copy `.tools`, `.workspace`, `.venv`,
provider credentials or native login state between devices. Imported historical
results remain evidence of earlier runs, not new successful executions.

## Optional CPU training

Select `--with-training` before initial installation when needed. Its pinned
Python, PyTorch/TRL dependencies and model are isolated under `.tools/training`,
outside the application `.venv`. Installation does not launch training. Start jobs
explicitly through the application, with its worker/simulator, time and memory limits.

Real LoRA weight updates and checkpoint reloads have been executed. The documented
pilots did **not** improve held-out electrical success over the base model.
Successful setup, changing weights and plausible agent text do not establish
electrical improvement. See [training evidence and limits](training.md).

## Validation status and checklist

The 2026-10-09 isolated Ubuntu 24.04 x86-64 replay passed the desktop/EDA path,
including actual tools rather than simulated success responses. The local evidence
root is `.validation/portable-20261009/`; it is intentionally excluded from Git.
See [delivery status](status.md) for the final regression record.

- Python 3.12 application installation, fresh Magic/Xschem source compilation,
  actual Node 22.23.3/Codex 0.159.2/Claude 2.1.280 installs and version probes passed.
- Prepared SKY130 reuse and fresh extraction from SHA-256-verified cached archives
  passed. Python wheels and EDA archives were also cached inputs. This does not
  prove every upstream download is reachable from a different network.
- UI, operation discovery and real noVNC HTML returned HTTP 200. Xschem and Magic
  stayed active and acknowledged cancellation. Application and desktop listeners
  were released on shutdown.
- The HTTP OTA simulation and independent full reference passed: pre/post gain
  **43.36763 / 43.36810 dB**, 28 extracted capacitors, actual DRC/LVS plus negative
  controls. Browser clicking through the desktop is a separate UI acceptance.
- Repeating the installer twice preserved manually changed language/theme and
  runtime port, confirmed by unchanged file hashes.
- Two existing private project backups passed ZIP checks and were successfully
  restored into a separate workspace through the registered chunked import API.
  Imported old-machine tool/PDK overrides still need review on the target device.
- Service rendering passed the real systemd parser; command lifecycle uses an
  isolated systemctl fixture. No real systemd user manager was enabled in the
  container or on the current machine.
- Worker cleanup tests use real detached process trees and confirm unrelated
  processes survive. Native account login/model inference and a new training job
  on the target machine are not included. The existing pinned training runtime and
  model pass the new integrity/import checks; prior training showed no improvement.

For a disposable, newly installed desktop workspace with **no projects**, run:

```bash
.venv/bin/python scripts/deployment_acceptance.py \
  --config .tools/deployment/config.json \
  --output .validation/deployment-acceptance \
  --allow-test-workspace
```

This creates test projects, launches the configured app and owned desktop, runs
real OTA tools, retains logs and checks shutdown. It refuses a nonempty project
workspace and existing evidence output. It does not log into agents or run model
training. The automatic deployment CI job checks core installation/repeat/start;
the full EDA/desktop script remains an explicit integration gate.

A healthy process, install success, doctor report or model response does not
satisfy circuit acceptance. Capacitive extraction is not distributed-RC extraction
or foundry signoff; reference results do not predict manufactured silicon performance.
