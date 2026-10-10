# Manual design workflow

This guide describes the implemented CLI, browser controls, and local HTTP API. Manual design does not require an AI provider or GPU. The educational amplifier supports real ngspice simulation; **no completed amplifier schematic or layout is supplied**. This host's native editors and SKY130 device smoke checks are validated; see [local toolchain and remote desktop](local-toolchain.md) for the current installation. See [architecture](architecture.md) and the [integration matrix](integration-matrix.md) for phase boundaries.

Commands below run from the repository root with Python 3.12 or later. `REPLACE_WITH_…` values are placeholders: substitute IDs returned by your actual requests or paths from your installation. Do not type those values as real project/run IDs.

## 1. Start the workspace and create a project

```bash
export AF_WORKSPACE="$PWD/.workspace"
python3 -m analog_factory --workspace "$AF_WORKSPACE" doctor
python3 -m analog_factory --workspace "$AF_WORKSPACE" create "Educational amplifier"
python3 -m analog_factory --workspace "$AF_WORKSPACE" serve --port 8765
```

Open `http://127.0.0.1:8765`. Keep the server running and use another terminal for API examples. Select the created project in the browser. The educational template has an editable level-1 MOS netlist, a transient testbench, measurements, and specifications. It is an integration example, not a foundry device model.

The **New project** dialog checks workspace requirements separately from the
selected project's overrides. An educational project can be created before
ngspice is installed; running it still requires the simulator. The OTA template
checks the same prepared SKY130 assets used by project creation. The generic
SKY130 starter still requires replacing its model-library placeholder after
creation. Use **Refresh check**, **Configure requirements**, or **Use educational
example** to resolve a missing prerequisite.

**Overview → The design path** shows recorded progress and an actionable next step.
File presence means ready to inspect; tool completion and passing DRC/LVS evidence
have separate labels. Old inputs are marked stale. For the OTA reference, actual
netlisting and extraction outputs must be published before they become project
inputs. Publication checks their recorded artifact and current derived files;
it does not establish electrical compliance or physical signoff.

**Continue design** resolves the selected stage against current project files and
requirements. It opens or prepares the relevant source, runs the saved pre/post
testbench, inspects an existing execution, or selects the exact publication run.
Publication still requires its explicit button. Missing tools lead to settings;
missing files lead to the relevant editor. Pending editor drafts and changed
prerequisites must be reviewed before a design action proceeds. The registered
`ui.workspace.continue_design` command uses this same path.

In **Settings → User interface**, choose **English** or **简体中文** and the **Light** or **Dark** theme. Changes apply immediately and are saved for the workspace, including after a reload or opening another browser. Existing workspaces default to English and dark. Interface translations leave project names, source files, tool output, and model responses in their original language. Tool and provider settings still use their separate **Save configuration** button.

To connect a model, open **Settings → Model API**, choose the protocol, and enter
the base URL, model name and API key. Click **Save and test connection** to save
the form and retrieve available models. This check does not send project contents
or verify chat support; a model name can be entered manually. Keep **Remember API
key on this server** checked to retain the key across server restarts in a private,
unencrypted workspace file, or uncheck it to use the key until the server stops.
The key input becomes blank after saving and never displays the stored key. Leave
it blank to retain the key for the same endpoint, or use **Clear saved key** to
remove it. Changing the endpoint or protocol requires a new key. The optional
environment variable in the advanced settings remains a fallback after clearing.
Configuration exports do not include keys.

Copy the returned project's 32-character `id` into the second terminal:

```bash
export AF_API=http://127.0.0.1:8765
export AF_PROJECT_ID=REPLACE_WITH_PROJECT_ID
export AF_WORKSPACE="$PWD/.workspace"
export AF_PROJECT_DIR="$AF_WORKSPACE/projects/$AF_PROJECT_ID"
```

In **Design sources**, edit `specifications.json`: name the metric, target, tolerance, units, and operating conditions. This file records design intent; it does **not** automatically rewrite supply sources, temperatures, models, or analyses in SPICE. Keep the testbench and specifications consistent. Save before executing. Browser saves retain earlier file revisions; native-tool saves are outside that editor history.

## 2. Run and inspect the netlist baseline

Use **Run pre-layout simulation**, or the CLI with the same workspace and project ID:

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" run "$AF_PROJECT_ID" \
  --kind simulate --options '{"netlist":"testbenches/pre.cir"}' --timeout 60
```

The CLI waits and prints the run record. The browser's simulation view shows measured values, raw logs, waveform traces, zoom/cursor controls, and CSV export. Open each measurement's provenance to inspect its `.meas` definition and source log. A missing measurement is not zero. A completed process alone does not establish that the circuit meets its specification.

**Compare recorded results** selects successful simulations with finite recorded
measurements. Differences are calculated only when explicit units, definitions,
analysis settings, PVT conditions and model/simulator identities agree. Missing
metadata remains unknown, including on older manual runs. Values and recorded
conditions remain inspectable when a difference cannot be calculated. dB values
show an absolute difference, never a relative percentage.

New ordinary ngspice runs collect measurement evidence from the executed input
snapshot after generated overrides, then confirm temperature from the actual
simulator log. The educational amplifier's six measurements have explicit units;
repeated runs and resistor/device-size revisions can be compared when their
measurement definitions and conditions agree. Supply, temperature, analysis or
model changes prevent a same-condition delta. Automatic qualification currently
covers bounded static inline level-1/passive decks. External libraries, scoped
models, control programs and unresolved expressions remain unqualified; their
numeric results are still available for inspection. A recorded design target or
caller-supplied condition does not establish the simulator's actual settings.

The analysis form generates `testbenches/configured.cir` by default for OP, DC, AC, transient, or noise analysis. It replaces existing analysis/measurement directives, so enter suitable `.meas` directives in the additional-directives field if measurements are needed. Review the generated file. Relative `.include` paths resolve from the project snapshot root, such as `netlists/amplifier.cir`, rather than from the `testbenches/` directory.

For API execution:

```bash
curl --fail --silent --show-error "$AF_API/api/projects/$AF_PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  --data '{"kind":"simulate","options":{"netlist":"testbenches/pre.cir","conditions":{"phase":"pre","temperature_c":27}},"timeout":60}'
```

The response is asynchronous. Copy its `id` into `AF_RUN_ID`; condition metadata documents the run but does not apply temperature or other electrical settings:

```bash
export AF_RUN_ID=REPLACE_WITH_RUN_ID
curl --fail --silent --show-error "$AF_API/api/projects/$AF_PROJECT_ID/runs/$AF_RUN_ID"
curl --fail --silent --show-error "$AF_API/api/projects/$AF_PROJECT_ID/runs/$AF_RUN_ID/log"
```

Wait until `status` is terminal. To stop an active task, use its browser **Stop** control or POST `{}` to `/api/projects/$AF_PROJECT_ID/runs/$AF_RUN_ID/cancel` with JSON content type. POST `{}` to the corresponding `/retry` endpoint creates a new run and ID. Retain both records. Save IDs by purpose, for example `AF_PRE_RUN_ID`, rather than assuming the newest run is the baseline.

## 3. Prepare SKY130 before physical design

Create a separate physical-design project, then update `AF_PROJECT_ID` and `AF_PROJECT_DIR` to its returned ID:

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" create "SKY130 amplifier" --template sky130
```

Provision xschem, ngspice, Magic, the RTimothyEdwards Netgen LVS comparator, and a prepared SKY130 PDK. A raw `skywater-pdk` checkout is insufficient. [Ciel](https://github.com/fossi-foundation/ciel) is the maintained successor to Volare; [open_pdks](https://github.com/RTimothyEdwards/open_pdks) builds the tool-specific assets. This repository does not yet publish a validated tool/PDK version combination.

After installing Ciel separately, select an actual available open_pdks build hash and a dedicated PDK root. For example, with real values substituted:

```bash
export PDK_ROOT=/REPLACE_WITH_DEDICATED_PDK_ROOT
export AF_PDK_BUILD=REPLACE_WITH_AVAILABLE_OPEN_PDKS_COMMIT
ciel enable --pdk-family sky130 "$AF_PDK_BUILD"
readlink -f "$PDK_ROOT/sky130A"
```

Record the build ID and archive/source identity. In workspace **Settings**, set the PDK root to the parent directory of the **resolved, fixed** `sky130A` installation and set the variant to `sky130A`. Avoid a mutable activation path that can silently switch builds. In a terminal using that installation, set `PDK_ROOT` to that same fixed parent and `PDK=sky130A`.

The detector expects these actual files beneath `$PDK_ROOT/sky130A/`:

```text
libs.tech/xschem/xschemrc
libs.tech/magic/sky130A.magicrc
libs.tech/netgen/sky130A_setup.tcl
libs.tech/ngspice/sky130.lib.spice
```

Recheck tools in Settings or run `doctor`. Detection establishes asset presence, not correct DRC/LVS or model behavior. Replace the placeholder `.lib` path in `testbenches/pre.cir` with your installed model library and select the intended corner. Use the same PDK installation for all stages. Check model-specific configuration against that PDK; the simulator adapter disables automatic user `.spiceinit` loading, so a hidden global configuration cannot be relied upon to make a model work.

## 4. Create or import the schematic and layout

Choose the DUT boundary first. The schematic and layout compared by LVS must describe the same devices, hierarchy, ports, and top-cell name. External voltage sources, loads, and stimuli belong in the testbench when they are outside that boundary. The starter SKY130 netlist includes ideal sources/load and is not already a layout-equivalent DUT subcircuit.

From the browser **Overview**, choose **Schematic → Open in xschem** or **Layout → Open in magic**. The first click creates a valid empty `schematics/amplifier.sch` or `layout/amplifier.mag` if it is missing, then opens the native editor in the **Drawing desktop** tab. Existing drawings are preserved; repeated clicks reuse the active editor for that file. Enter the existing desktop password if requested. This host relays its remote desktop through the workspace page, so no additional SSH port forwarding is needed. The file tree refreshes after launch. The **Verification → Open magic** action also supports first-time creation at the selected layout path. Save your edits in the native editor.

For API/CLI GUI launches, opt into the same behavior with `"create_missing": true` in run options. Batch netlisting, DRC, and extraction continue to require saved source files. No file is created when tool, PDK, or display validation prevents the GUI launch.

You may also start the editors from a terminal with the fixed PDK and desktop variables configured. For example:

```bash
(
  cd "$AF_PROJECT_DIR"
  xschem --rcfile "$PDK_ROOT/sky130A/libs.tech/xschem/xschemrc"
)
```

Save the schematic as `schematics/amplifier.sch`, with its required local symbols and subcircuits. Create the matching layout in Magic:

```bash
(
  cd "$AF_PROJECT_DIR"
  magic -rcfile "$PDK_ROOT/sky130A/libs.tech/magic/sky130A.magicrc"
)
```

Save `layout/amplifier.mag` with the intended cell ports, well/substrate connections, and hierarchy. Alternatively, import existing valid design files and their dependencies into those project folders. The browser's **Add file** supports text files; a filesystem copy is suitable for a larger existing hierarchy. Symlink-based project inputs are rejected. Refresh/reselect the project after external changes.

Once saved, **Open in xschem** and **Open magic** can reopen these files. They are coordinated external windows and edit the original sources. Save in the editor before starting verification. X11 or Xwayland and an actual graphical session are required; setting a fake `DISPLAY` does not supply one. API GUI jobs default to a long editor lifetime; the CLI defaults to a 60-second task timeout, so use the browser/API for editing sessions or set an appropriate explicit CLI timeout.

In the separate **Managed native canvas**, a confirmed version/confirmation
rejection before launch offers **Read latest project state**, followed by an
explicit new launch. An unknown launch outcome stays locked for inspection;
closing and reopening the dialog does not authorize another launch. A launch
receipt that arrives after closing is retained so its actual session can be
inspected.

To netlist the schematic without a GUI, use the implemented API even if no dedicated browser control is present:

```bash
curl --fail --silent --show-error "$AF_API/api/projects/$AF_PROJECT_ID/runs" \
  -H 'Content-Type: application/json' \
  --data '{"kind":"xschem","options":{"schematic":"schematics/amplifier.sch","netlist_only":true},"timeout":120}'
```

Inspect that run's `artifacts` list; output filenames come from xschem. Download the generated netlist through its artifact link and, after review, save its contents as `netlists/schematic.spice` using **Add file**/the editor. Configure the schematic to netlist the intended DUT subcircuit, not a complete simulation deck containing unrelated testbench sources. Run the pre-layout testbench against this schematic-derived netlist before comparing it with layout results.

## 5. DRC, extraction, and LVS

In **Physical verification**, supply the layout path, extracted netlist path, schematic netlist path, and top cell. The corresponding CLI operations are:

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" run "$AF_PROJECT_ID" \
  --kind drc --options '{"layout":"layout/amplifier.mag"}' --timeout 120
python3 -m analog_factory --workspace "$AF_WORKSPACE" run "$AF_PROJECT_ID" \
  --kind extract --options '{"layout":"layout/amplifier.mag"}' --timeout 120
```

Inspect `drc.txt` and `verification.status`, including error counts and rule details. Fix layout errors, save, and rerun. `unknown` is not a pass. Extraction produces `extracted.spice` and `extraction.txt` in that run's output; it does not overwrite a design source. This adapter requests capacitance-inclusive `ext2spice` output. Distributed resistance extraction, complete RC coverage, and foundry signoff have **not** been validated. Inspect the actual output devices, nodes, capacitances, and report.

LVS needs an extracted connectivity netlist, so perform this extraction before invoking LVS. Promote the selected artifact explicitly: download `extracted.spice` from the extraction run, review it, and save it as `netlists/extracted.spice`. For an API-based promotion that preserves editor revisions, set `AF_RUN_ID` to the completed extraction run's public ID and run:

```bash
python3 - "$AF_API" "$AF_PROJECT_ID" "$AF_RUN_ID" <<'PY'
import json, sys, urllib.request
base, project_id, run_id = sys.argv[1:]
project_url = f'{base}/api/projects/{project_id}'
with urllib.request.urlopen(f'{project_url}/runs/{run_id}') as response:
    record = json.load(response)
if record['status'] != 'succeeded' or record['kind'] != 'extract':
    raise SystemExit('Choose a completed extraction run and inspect its report first')
with urllib.request.urlopen(f'{project_url}/runs/{run_id}/artifact?path=extracted.spice') as response:
    content = response.read().decode('utf-8')
request = urllib.request.Request(
    f'{project_url}/files?path=netlists/extracted.spice',
    data=json.dumps({'content': content}).encode(), method='PUT',
    headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request) as response:
    print(response.read().decode())
print('Promoted extraction artifact from run', run_id)
PY
```

Record the extraction run ID and artifact hash in a project report; keep the original output unchanged. Repeat promotion after a new extraction, not after merely editing the layout. Source additions/promotions can mark earlier runs stale because the current implementation compares project-wide source hashes. Those immutable runs remain usable as identified historical evidence.

Now compare the extracted and schematic netlists:

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" run "$AF_PROJECT_ID" \
  --kind lvs \
  --options '{"layout_netlist":"netlists/extracted.spice","schematic_netlist":"netlists/schematic.spice","cell":"amplifier"}' \
  --timeout 120
```

The current adapter uses the same `cell` name for both netlists. Check the actual top-cell definitions before launching. Read `lvs.txt` and the run's `verification` result; a zero process exit does not replace a matching report. Resolve device, connection, and parameter differences in the design. Do not alter extracted connectivity just to obtain a passing comparison. Preserve both failed and passing reports.

## 6. Construct and run the post-layout testbench

Inspect the extracted `.subckt` declaration. An include loads definitions; it does **not** instantiate the DUT. The `X` instance must use its exact subcircuit name and ordered pins, with the correct supplies, grounds, and body/well connections. Map any global nets consistently. Avoid instantiating the schematic and extracted DUT together.

For example, **only if both netlists declare `.subckt amplifier in out vdd vss`**, the following is a testbench skeleton for `testbenches/post.cir`. The model-library path is a placeholder and the operating values must match your actual pre-layout testbench/specification:

```spice
SKY130 post-layout comparison - user-supplied extracted DUT
.lib "/REPLACE_WITH_FIXED_PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.temp 27
.include "netlists/extracted.spice"
VDD vdd 0 1.8
VIN in 0 DC 0.8 SIN(0.8 0.005 1k) AC 1
XDUT in out vdd 0 amplifier
.tran 2u 5m
.meas tran vout_mean AVG v(out) FROM=1m TO=5m
.meas tran vout_pp PP v(out) FROM=1m TO=5m
.meas tran vin_pp PP v(in) FROM=1m TO=5m
.meas tran gain PARAM='vout_pp/vin_pp'
.meas tran supply_current AVG i(VDD) FROM=1m TO=5m
.meas tran power PARAM='-1.8*supply_current'
.end
```

For the corresponding pre-layout run, use the same bench with `netlists/schematic.spice` included instead. If the actual pin order differs, adapt both instances from their declarations. The `gain` measurement above is a transient amplitude ratio at 1 kHz; it does not establish AC bandwidth or phase margin. A purely top-level extracted netlist needs a reviewed restructuring or compatible bench before this subcircuit example applies.

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" run "$AF_PROJECT_ID" \
  --kind simulate --options '{"netlist":"testbenches/post.cir","conditions":{"phase":"post","corner":"tt","temperature_c":27}}' \
  --timeout 120
```

No parasitics or post-layout results are synthesized by the workspace. If extraction, model resolution, or simulation is unavailable/invalid, the post-layout gate remains open.

## 7. Compare and archive the evidence

In **Simulation lab → Compare recorded results**, select the specific successful pre- and post-layout run IDs. Confirm identical stimulus, corner, temperature, units, measurement definitions, and DUT boundary. The table reports common metric values and differences; it does not automatically establish equivalent conditions. Inspect raw waveforms/logs and stale/provenance flags. Local source hashes do not by themselves prove all external model dependencies are immutable; retain the fixed PDK identity and review the run's dependency limitations.

Use **Export project**, or:

```bash
python3 -m analog_factory --workspace "$AF_WORKSPACE" export "$AF_PROJECT_ID" ./analog-factory-design.zip
```

The archive contains project sources and recorded execution evidence; it does not bundle the external PDK/tool installations. Add a report describing the chosen PDK build, tool versions, actual DRC/LVS coverage, extraction limitations, and the relevant pre/post run IDs. File revision history and credentials are excluded. A complete saved design flow is a reproducible engineering milestone; tapeout signoff requires separately established process and verification coverage.
