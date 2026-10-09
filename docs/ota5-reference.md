# Five-transistor SKY130 OTA reference

This is a complete native schematic and routed layout for a differential-input,
single-output OTA. Its five devices are a matched NMOS differential pair, a PMOS
current-mirror load, and an NMOS tail current source. The tail gate uses an external
0.65 V ideal bias source in the testbench. There is no sixth bias transistor.

The circuit uses a 1.8 V supply, 0.9 V input common mode, and 1 pF output load.
The two input sources have AC amplitudes of 0.5 V and opposite phases, producing
exactly 1 V differential AC input. The acceptance gain is
`20 log10(abs(V(out)/(V(inp)-V(inn))))` at 1 Hz, and must exceed 30 dB before and
after layout. Both inputs use W/L = 4/2 µm, both PMOS loads use 8/2 µm, and the tail
uses 4/2 µm. These deliberately long channels provide useful gain at low current.

## Create the reference in the application

Configure the prepared SKY130A PDK and tools in Settings, then choose **New project →
Five-transistor OTA**. Creation checks the required model library, Xschem, Magic,
and Netgen PDK assets before publishing the project. An unavailable PDK produces a
clear prerequisite error and no partial project. The installed wheel bundles the
same qualified native schematic, layout, testbenches and independent netlist as
this repository.

Project metadata records the six ports, `ota5` cell, and default source paths.
The browser, registered operations, workflow nodes and scoped agents use those
same defaults. A new project contains sources and a workflow README, with no
saved tool results or claimed checks.

1. Run the pre-layout simulation and open the native schematic/layout as needed.
2. In Verification, choose **Netlist schematic**, select the successful Xschem
   execution, and **Publish selected tool output**.
3. Run DRC and extraction, then publish the successful extraction. This creates
   both `netlists/extracted.spice` for post-layout simulation and
   `netlists/extracted-lvs.spice` for connectivity comparison.
4. Run LVS and **Run post-layout simulation**, then compare the actual runs.

The initial `netlists/schematic.spice` is explicitly labelled as the supplied
hand-authored electrical source. Publishing replaces it with the actual Xschem
output. The registered `reference.derive_netlists` operation requires a current,
successful, locally owned run, verifies the artifact hash, preserves exactly five
SKY130 devices, canonicalizes the six named ports, and records a derivation report.
Imported, stale or modified run artifacts are rejected. The LVS view removes only
capacitor records; the full extracted netlist remains available. Publication is
a version-checked source edit, not a DRC/LVS verdict.

## Recorded acceptance

A replay through packaged template creation, implicit source defaults and registered
publication on 2026-10-08 passed every required gate. Its actual reports, derived
netlists and hashes are retained in
[`examples/ota5/evidence/template`](../examples/ota5/evidence/template/summary.json). The adapter explicitly preserves the top subcircuit while flattening
hierarchy, including capacitances to ground emitted by newer Magic versions.

| Measurement | Before layout | After layout |
|---|---:|---:|
| Differential gain at 1 Hz | 43.3676 dB | 43.3681 dB |
| Unity-gain frequency | 3.73116 MHz | 3.69110 MHz |
| DC output | 0.706702 V | 0.706686 V |
| Supply current magnitude | 2.44021 µA | 2.44238 µA |

The accepted layout has zero reported local DRC errors; both Xschem and independent
reference LVS match. The width-error LVS and minimum-width DRC negative controls
both fail as required. The current extraction contains **28 capacitance records**
and five MOS devices. These results are real tool evidence, not results supplied
when creating a template.

The earlier 25-capacitance hierarchy-based extraction and its 3.68956 MHz post-layout
unity-gain frequency remain preserved in
[`examples/ota5/evidence`](../examples/ota5/evidence/summary.json), project
`d3b164087cec4e85a840806bc6e9e8a1`. They are historical evidence for the earlier
adapter. A native layout rebuild also passed `drc(full)` with zero errors and
exactly reproduced the committed geometry.

## Run the actual tools

After configuring the toolchain and SKY130A as described in
[local-toolchain.md](local-toolchain.md), run from the repository root:

```bash
python3 scripts/validate_ota5.py --settings .validation/settings.json \
  --workspace .validation/ota5-template-cli --template
```

The `--template` path uses registered template creation, implicit source defaults,
and actual-output publication. Omitting it replays the original asset-copy
benchmark. The script creates a new, isolated reference project on every invocation. It
preserves existing projects and refuses the user's `.workspace` as its validation
workspace. Tool paths and the PDK root come from the supplied settings; the
committed sources contain no host-specific model path. The `@MODEL_LIBRARY@`
placeholder is replaced with the configured PDK's
`sky130A/libs.tech/combined/sky130.lib.spice`, corner `tt`.

The script uses the shared workspace operation registry for settings, project
creation, configuration, file writes, Xschem netlisting, Magic DRC/extraction,
Netgen LVS, ngspice simulation, and waveform reads. Runs contain immutable source
snapshots, tool versions, PDK identity, input/artifact hashes, process logs,
measurements, and available external model dependency hashes. External PDK files
remain external dependencies, as explicitly recorded in run provenance.

The final report is `WORKSPACE/ota5-validation.json`. A failure returns exit status
1 and retains the incomplete report and all completed runs. A process exiting
zero is insufficient: positive DRC/LVS verdicts, negative-control failures,
actual capacitances, complex-waveform gain, ngspice measurements, and useful DC
bias are all checked explicitly.

## Native files and derivations

- `examples/ota5/schematics/ota5.sch` contains the five device symbols, wires, and
  six named ports. Open it in Xschem with the configured SKY130 symbols.
- `examples/ota5/layout/ota5.mag` contains flat SKY130 geometry with guard-ring
  contacts, physical metal routing, and six external ports. Open it in Magic with
  `sky130A.magicrc`.
- `examples/ota5/netlists/ota5.spice` is the independently written electrical
  reference. Its five device records are checked against extraction separately
  from the Xschem netlist.
- `testbenches/pre.cir` includes the generated Xschem netlist; `post.cir` includes
  the generated capacitance-inclusive layout netlist. The testbench conditions
  are otherwise identical.

The Xschem adapter emits its top `.subckt`/`.ends` lines as comments. The validation
script enables that wrapper and puts the six named ports into the documented
order `inp inn bias vdd vss out`. It also normalizes the extracted port order;
no transistor or capacitance record is rewritten. The original tool netlists
remain in their run artifacts. A separate `netlists/extracted-lvs.spice` removes
only the parasitic capacitor records from the PEX netlist for connectivity and
geometry LVS. The retained post-layout netlist keeps those capacitances.

The negative LVS reference changes the first NMOS width from 4 to 5 µm. The
negative DRC layout adds a disconnected 10 nm by 10 nm metal1 rectangle. Both are
separate source files and runs; the accepted layout and schematic are preserved.

## Rebuild the geometry

The layout generator asks the installed SKY130 PDK to draw two native MOS pcell
geometries, instantiates three NMOS and two PMOS shapes, and adds explicit metal1,
metal2, metal3 and contact routing. It writes to a new output directory and runs
Magic's `drc(full)` style. The normal application DRC adapter records its own
configured default style separately.

```bash
python3 examples/ota5/layout/generate.py \
  --magic /path/to/magic \
  --pdk-path /path/to/pdk-root/sky130A \
  --output /tmp/ota5-rebuilt
```

The pinned PDK uses a 5 nm Magic grid. The generator rejects different saved grid
scaling or a changed transistor port interface. With the pinned tools and PDK,
the rebuilt layout is byte-identical to the committed layout after normalizing
Magic's timestamp. Retained generator logs include the full local DRC verdict.

## Scope

This reference establishes the complete manual-design flow and nominal circuit
acceptance. The deliberately spacious single-row layout is intended for inspection
and reproducibility. It is not an optimized matching or area design. Extraction
includes interconnect capacitances and model junction geometry; distributed
resistance is not extracted. Local DRC and LVS do not establish foundry signoff,
full-chip density/antenna compliance, manufacturing yield, or measured silicon
performance. PVT, mismatch, transient settling, noise and offset are separate
characterization tasks, not implied by this nominal AC acceptance.

Quick checks run with:

```bash
python3 -m unittest discover -s tests -p test_ota5_reference.py
```

To run the same complete EDA acceptance inside the integration test's temporary
workspace, add `AF_OTA5_REAL_TOOLS=1` to that command. Missing configured resources
are a test failure; the real-tool test is opt-in so software-only test environments
can still run the asset and acceptance-gate checks.
