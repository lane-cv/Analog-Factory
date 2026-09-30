# Configured local EDA tools

Configured on 2026-09-30 for this Ubuntu 26.04 amd64 host. System installation
required interactive administrator authentication, so the Ubuntu packages and their
missing runtime libraries were installed under `.tools/eda/` in this repository.
ngspice 46 was already installed at `/usr/local/bin/ngspice`. SKY130 configuration
subsequently required newer Magic and Xschem builds, installed locally from pinned
official sources. The workspaces now use the following launchers:

| Tool | Verified version | Configured executable |
|---|---|---|
| ngspice | 46 (KLU available) | `.tools/eda/bin/ngspice-sky130` |
| Xschem | 3.4.7 | `.tools/eda/xschem-3.4.7/bin/xschem` |
| Magic | 8.3.684 | `.tools/eda/magic-8.3.684/bin/magic` |
| Netgen LVS | 1.5.133 | `.tools/eda/bin/netgen` |

Both `.workspace/settings.json` and `.validation/settings.json` contain absolute
paths to these executables. Other settings are retained. The Netgen launcher uses the
Ubuntu **netgen-lvs** package, not the unrelated `netgen` mesh generator.

The launchers locate their runtime libraries, Tcl startup scripts, and symbols.
Xschem uses project-local configuration. The ngspice launcher invokes the existing
native executable with the PDK's `ngbehavior=hsa`, `skywaterpdk`, and `ng_nomodcheck`
settings, including when the application uses `-n`. No user-wide `.spiceinit` is
needed. Source archives, package archives, SHA-256 manifests, and build logs remain
under `.tools/eda/`. The original older Magic/Xschem launchers are retained.

## SKY130A installation

The pinned open_pdks revision is `1689ac3f2dc763876eaf967227c7dfe831b031ae`.
The workspace PDK root is the absolute path to:

```text
.tools/pdks/sky130/1689ac3f2dc763876eaf967227c7dfe831b031ae
```

The `sky130A` child contains prepared Xschem, ngspice, Magic, and Netgen assets.
The official [Ciel release](https://github.com/fossi-foundation/ciel-releases/releases/tag/sky130-1689ac3f2dc763876eaf967227c7dfe831b031ae)
archives `common.tar.zst` and `sky130_fd_pr.tar.zst` were checked against their
published SHA-256 digests before extraction. This is the analog primitive subset;
digital standard-cell, I/O-cell, and SRAM libraries are not installed.

The `test` project pins this PDK in its project configuration. Its
`testbenches/pre.cir` placeholder was replaced through the editor API, preserving
file history, with `sky130A/libs.tech/combined/sky130.lib.spice` at corner `tt`.
This is the library selected by the installed PDK's Xschem configuration. It uses
scalable MOS models: it is not numerically identical to the alternative discrete
models in `libs.tech/ngspice`. The same amplifier produced 1.63332 V using the
selected combined models and 1.63102 V using the discrete models.

Magic was upgraded because the technology requires at least 8.3.411. Xschem was
upgraded because the current device symbols use the `expr(...)` feature introduced
in 3.4.7; the older 3.4.4 executable left unresolved geometry expressions.

## Use

Restart an already-running workspace after changing its settings file, or apply the
same paths through Settings. Check the configuration with:

```bash
python3 -m analog_factory --workspace .workspace doctor
```

To use the launchers directly from the current terminal:

```bash
source .tools/pdks/sky130-env.sh
.tools/eda/bin/ngspice-sky130 --version
xschem --version
magic --version
netgen -batch
```

This sets the current shell's PDK variables and selects the upgraded Magic/Xschem
launchers on PATH. No shell profile or system installation is modified. Keep
`.tools/eda` and `.tools/pdks` with this checkout. If the repository moves, rerun the
build/install scripts and update absolute workspace and testbench paths.

## Execution checks

- ngspice ran the educational amplifier through the configured application adapter:
  2,508 waveform points, 2.43467 V mean output, and gain 3.40448. OP, DC, AC,
  transient, noise, and tuning checks also passed in the nine service tests.
  `.tools/eda/validation/ngspice/validation.json` identifies the retained simulation
  run and measurements in `.validation`.
- The actual `test` SKY130 project simulation succeeded with 2,508 waveform points
  and `vout_mean=1.63332 V`. Its run ID is
  `bf4398a9ac7346f2b4d63454d46cf805`.
- Xschem 3.4.7 resolved a SKY130 transistor symbol, expanded its geometry, and
  generated a deck that ngspice simulated successfully.
- Magic 8.3.684 generated a native SKY130 transistor with contacts and a guard ring;
  its `drc(fast)` check reported zero errors, and PEX produced a transistor plus
  five parasitic capacitors. A separate `ext2spice lvs` netlist matched an independent
  schematic in Netgen. A deliberately wrong transistor width correctly failed LVS.
- Xschem generated a real resistor netlist, including through the application's
  headless netlisting adapter and run records.
- Netgen compared matching and deliberately rewired resistor circuits. Actual reports
  distinguish a unique match from a mismatch; both processes exit zero.
- Magic loaded its bundled generic `scmos` technology, checked a small test layout,
  and extracted a transistor and capacitance into SPICE.

The application extraction adapter was adjusted for Magic 8.3.105: `extract do local`
and extraction from the run's output directory replace unsupported `extract path`.
Using `ext2spice -p .` also handles project paths containing spaces. The real regression
test checks that extraction preserves source/input snapshots. The complete suite passed
all **63 tests** after this compatibility fix.

Logs, inputs and reports are retained under `.tools/eda/validation/`, with current
PDK checks under its `sky130/` directory. The application's Netgen parser was updated
to recognize completed 1.5.133 reports while rejecting incomplete reports, property
errors, and top-level pin failures after child matches. These checks validate tool
setup; the user's amplifier still has no drawn schematic or layout. Native GUI
windows use the remote desktop connection described below.
The final regression suite passed **72 tests**. The idle local workspace server was
reloaded to activate the LVS parser update; its saved settings and project data
were retained.

## Reproduce this host installation

ngspice is supplied by the existing host installation. The package-relocation
script below prepares the baseline Tcl/Tk runtime and Netgen; it also resets tool
paths to the older launchers, so apply the upgraded paths from the table afterward.

The following package versions were available from this host's Ubuntu 26.04 repositories:

```bash
mkdir -p .tools/eda/packages
cd .tools/eda/packages
apt-get download xschem=3.4.4-1build1 magic=8.3.105+ds.1-1.1 netgen-lvs=1.5.133-1.2 \
  libglu1-mesa=9.0.2-1.1build2 libopengl0=1.7.0-3 libtk8.6=8.6.17-1build1 \
  libxss1=1:1.2.3-1build4 tk8.6=8.6.17-1build1
cd ../../..
python3 scripts/configure_local_eda.py
python3 scripts/configure_local_eda.py --workspace .validation
python3 scripts/build_local_magic.py
python3 scripts/build_local_xschem.py
python3 scripts/install_sky130.py --ngspice /usr/local/bin/ngspice
```

Use `--skip-download` with each of the three build/install scripts to reuse its
verified local archives. Configure the absolute upgraded executable paths, PDK root,
`sky130A` variant, and revision in workspace Settings, then set the model-library
path in a SKY130 testbench. Updating a running workspace through its settings API
applies configuration immediately; changes to Python application code require a
server restart.

## Remote editor desktop

This host's existing `iic-osic-tools_xvnc_uid_1000` container provides a password-free
browser desktop at **port 8081**, as requested by the workspace owner. The workspace's **Drawing desktop / 图形桌面** tab now
displays it inside the original page. Clicking the schematic or layout action
opens that tab automatically; an already-running editor for the same file is
reused. noVNC connects directly without requesting a desktop password.

The workspace proxies the desktop page and WebSocket through its existing port
8765. SSH access therefore needs only the workspace's existing tunnel. The desktop
connection stays alive while switching workspace tabs or refreshing run status.
Use **Reconnect desktop** to reconnect explicitly. Native editor saves still write
to the original project files.

You may alternatively open the same server address with port 8081 to view the full
desktop separately. Both browser entry points use the same password-free desktop.
The container's startup command persists `-localhost yes -SecurityTypes None`:
raw VNC listens only on container loopback, and noVNC proxies that connection.
X11 authorization still uses its private cookie. After changing this mode, the
desktop was restarted, its X11 connection refreshed, and the unchanged Magic
layout reopened. Fresh browser sessions verified both entry points without a password.

For that optional standalone page over SSH, forward the desktop port from your client computer:

```bash
ssh -N -L 8081:127.0.0.1:8081 lien@SERVER
```

Then open `http://127.0.0.1:8081` in that computer's browser. Replace `SERVER` with
your SSH host. The workspace can keep its existing port 8765 connection.

For future workspace launches from SSH or a terminal without a desktop environment:

```bash
./scripts/serve_with_desktop.sh --port 8765
```

The launcher reconnects to the running desktop, verifies the real X11 connection
with `xdpyinfo`, and starts the workspace with `DISPLAY=:101`, a private
`XAUTHORITY` file, and `AF_DESKTOP_URL=http://127.0.0.1:8081/`. This URL is a
server-side loopback target; it is not a browser URL or a password. The proxy is
disabled when the variable is unset, and accepts only a fixed loopback HTTP origin.
It discovers the container's current process and socket, so run
it again after restarting the desktop container. No system profile, container
configuration, VNC credentials, or X11 access-control policy is changed.

For a shell that also needs native editor access:

```bash
python3 scripts/connect_remote_desktop.py
source .tools/desktop/env.sh
source .tools/pdks/sky130-env.sh
```

Connection metadata is under `.tools/desktop/connection.json`. The authorization
file is private (mode 0600), stays outside version control, and must not be shared.
The socket alias only connects to the existing authenticated display; it does not
start another display server or listener. In the workspace overview, the schematic
and layout buttons create an empty source file on first use, then launch the editor.
Existing sources open without being overwritten. Save your drawings in the native
editor before netlisting, simulation, or verification.

The host already supplies Tcl 8.6, X11/Cairo libraries and libc. This is a configuration
for this host, not a cross-distribution binary bundle. For another Ubuntu version,
use its packages and validate compatibility. The original installation/validation
documents describe the earlier baseline before these tools were installed.
