# Managed native canvas

The unified registry now exposes seven bounded operations for a **new, supervised** Xschem or Magic window. These operations use an installed fixed Tcl adapter and an acknowledgment from that actual process. They do not attach to existing windows or expose arbitrary Tcl, shell commands, menu traversal, screen coordinates, or a general third-party GUI API.

| Operation | Implemented behavior |
| --- | --- |
| `native.canvas.start` | Launch a new managed editor on an existing visible `.sch` or `.mag`; return its run and session IDs. |
| `native.canvas.get` | Obtain actual native revision, dirty state, named instances/pins or layers/cursor box and source hash. |
| `native.canvas.select` | Select one named Xschem instance. |
| `native.canvas.move` | Move a named Xschem instance by native grid steps, stretching attached wires. |
| `native.canvas.connect_pins` | Route two actual named Xschem pins with a bounded orthogonal route. |
| `native.canvas.paint_box` | Paint an actual discovered Magic layer inside the previously acknowledged native cursor box. |
| `native.canvas.save` | Export the current native document and invoke registered `file.write`, retaining ProjectStore history and its real receipt. |

Use `run.cancel` to close a managed editor. Closing the browser dialog alone leaves it running. The browser panel is available through **Managed native canvas**; its controller and `ui.native_canvas.*` commands share the same registry operations. The native process acknowledgment is separate from a browser-client acknowledgment. Native selection/edit/save operations declare the native frontend adapter rather than pretending that a browser performed them.

## Ownership, revision and save behavior

Each command names the project, run and random session ID. Only an active process owned by the current server can accept commands. Imported evidence and historical run artifacts provide no session authority. Mutations also require the last observed native revision. The revision compares the complete native serialization and relevant native state, so another edit while the native modified flag is already true still causes a conflict.

The adapters preserve current source, qualified selection and native clipboard while exporting. Xschem receives a private HOME, isolating its configuration and clipboard from existing editors. The private nonce, requests, responses and exported documents live outside project directories in a server-owned directory; they are not run artifacts or project exports. Nonces are read from a private file by the fixed startup script, never embedded in public generated script metadata.

Saving holds the native event callback for at most two seconds while the source write runs through the existing registered `file.write` operation, under the ProjectStore lock. Project version and original source hash must match. Saving never reloads the native document and deliberately **keeps the native modified flag**: project history contains the saved snapshot, while the window's native save/timestamp semantics remain intact. A later human edit must not be marked clean by an earlier source commit.

If the write completes after the native hold expires, the save reports an interrupted outcome and includes the real successful write receipt. If the registered write reports a failed or uncertain receipt after committing, that receipt is also retained. There is no automatic retry or rollback. Inspect source/history and native state before another save. The browser controller blocks further native mutations after an uncertain command until an explicit inspection, and blocks native start/save while a browser source draft is unsaved.

Browser acknowledgments publish the actual box geometry and omit `box_token` through the shared credential filter. The private controller keeps the directly inspected native token for paint commands; public browser schemas do not require the redacted field.

## Qualified scope and remaining gaps

The retained actual runtime qualification uses Xschem **3.4.7** and Magic **8.3.684**, with the prepared SKY130A PDK. Other native versions have not been qualified. This is partial native semantic coverage, not complete third-party UI coverage.

Xschem currently requires unique bounded names, available nonembedded symbols, and either no selection or full instance-only selection. Partial instance selections, selected wires/graphics, missing/embedded symbols and a different current schematic are rejected rather than exported with loss. Selection/edit commands cover named instances and pin routes; labels, component insertion/removal, symbol-property dialogs and arbitrary graphics do not have managed semantic operations yet.

Magic currently supports a flat single editable root cell. Hierarchical or multiple editable cells are rejected. Its implemented edit is painting a discovered layer into the acknowledged box. Hierarchical instance listing/select/move, labels, geometry deletion and a semantic box-placement operation remain unsupported. Human box changes invalidate the previous box token. Magic background DRC is completed before serialization, so its transient checkpaint does not cause a false native revision conflict.

Bounds are eight active managed sessions, 255 explicit commands per session, 2 MiB per exported document, 128 KiB per packet, 512 Xschem instances, 4096 wires and 128 pins per instance. Grid movement accepts 1–999 steps with a maximum 100000 native-unit displacement; pin routes have the same Manhattan length bound. Magic paint boxes require positive dimensions no larger than 10000 native units. These are implementation bounds, not process safety or design-rule guarantees. Geometry acknowledgment establishes an editor action; a separate actual netlist or DRC run establishes electrical/physical observations.

## Verification evidence

`python3 -m unittest tests.test_native_canvas` covers literal transport parsing, private-file bounds/symlinks, archive identity, session ownership, permission/schema contracts, controller unknown-outcome recovery and settled busy state, and preservation of actual write receipts after expiry or a post-commit error. Mocked transport checks are protocol evidence, not native geometry evidence.

Run `scripts/validate_native_canvas.py --output <isolated-directory> --pdk-root <prepared-PDK>` under a private Xvfb display with the prepared tools. It creates its own project and never takes over existing windows. The acceptance-only startup fixture simulates a second edit using an actual native interpreter callback; it is not a product endpoint.

Retained final native runtime evidence: `.validation/native-canvas-safety2/native-canvas-validation.json`.

- A second native edit while already modified rejects the stale mutation and preserves both unsaved changes.
- Explicit named-pin reconnection produces a real Xschem netlist with both resistor pins on the same net.
- A registered source write delayed beyond the actual native hold reports interruption, retaining its successful write/history and the native dirty flag.
- Actual Magic painting preserves the original geometry. A separate actual DRC run reports its result; this intentionally small fixture has violations and does not assert DRC cleanliness.
- `run.get` and exported project contents contain no nonce or private request/response transport files.

The standalone native runtime acceptance does **not** establish full browser integration parity. Initial actual browser evidence is retained at `.validation/native-browser-xevoqz1h/native-browser-report.json`: manual, remote browser and direct registry movement agree; named pin connection and explicit saves preserve project history; actual Magic painting preserves original geometry; stopped targets reject inspection. This initial run predates the final controller race fixes and does not certify the final source snapshot.

### Repeat actual browser acceptance

Use the already prepared `analog-factory-ubuntu24:validation` image and a host Node installation with Playwright and Chromium. Configure `NODE_PATH`, `PLAYWRIGHT_BROWSERS_PATH` and any browser library `LD_LIBRARY_PATH` in the invoking environment as needed; the driver inherits them unchanged. It does not install packages, pull an image, publish evidence or invoke a model.

```sh
python3 scripts/validate_native_canvas_browser.py \
  --output .validation/native-browser-final \
  --pdk-root /path/to/prepared-pdk \
  --image analog-factory-ubuntu24:validation \
  --port 8922
```

`--output` must be a new directory outside the source trees, user workspace and PDK. The PDK root must contain the prepared `sky130A` tree. The selected port must be unused. The driver freezes allowlisted source using `scripts.validate_ubuntu24.copy_source`; both the container and host browser test use that copy. User state is excluded. The source and PDK mounts are read-only, including the frozen source's alternate path under the evidence directory.

The driver starts a uniquely owned container under private Xvfb, publishes only `127.0.0.1:<port>`, and binds the server to the container's specific address. Readiness checks the owned container ID and localhost port mapping before running the actual browser fixture. Every exit closes only that container; interruption or browser timeout retains the logs and a failed evidence record.

The new directory contains `source/`, the full `source-manifest.json`, `server.log`, `browser.log`, `native-browser-report.json` and `evidence.json`. Evidence records command/exit status, key implementation hashes, the manifest digest, generated server digest and whether the frozen bytes remained unchanged. A successful exit requires both the real browser report to pass and owned cleanup to finish. Run again with a fresh output directory after changing source; historical browser reports are not evidence for different bytes.

`node tests/native-canvas-races.cjs` separately checks delayed target/stop/close receipts and invalid manual drafts. The Python native acknowledgment tests exercise real session poll/ack, redacted result schemas and permission denials before browser delivery. These portable checks complement the actual GUI fixture; they do not establish unsupported native editing coverage or electrical/DRC correctness.

Final browser acceptance is retained in
`.validation/integration-def-drafts-wzq5e7vj/native-browser/`, using the same
source manifest as the 614-host/613-Ubuntu/23-browser integrated gate.
`ui.native_canvas.set_options` covers tool/source plus instance, direction, grid
steps, named pins, route and layer. Manual controls call this same handler. These
are atomic validated drafts; editing them alone does not invoke a native command
or advance its revision. While connected, tool/source remain fixed, and instance,
pin and layer choices must exist in the acknowledged native state. The real browser
fixture verifies a remote two-step draft leaves geometry unchanged, then an explicit
move consumes that draft. This does not extend the third-party editing scope above.
