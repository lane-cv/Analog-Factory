# Navigate recorded DRC errors in Magic

Select a completed local DRC execution in **Verification**, choose an **Error
index**, and choose **Find error in Magic**. Magic opens the original layout,
rechecks it, executes `drc find N` and `findbox zoom`, and writes an acknowledgment
containing the actual box coordinates. The browser reports focus only after the
server validates that acknowledgment. Use the Drawing desktop to inspect/edit
the native layout, and **Stop navigation window** or the ordinary run stop control
to close the managed GUI process group.

The semantic `ui.drc_navigation.set_index` command selects an index in the visible
report and updates the input and `drc_navigation.error_index` snapshot without
launching Magic. Manual edits use the same validation. Changing the draft index
does not claim that an existing navigation window has focused the new tile.

An index refers to Magic's error tiles, rather than a report-text line or a browser
pixel coordinate. The count and enumeration follow the installed Magic/PDK.
Magic's [DRC command reference](https://opencircuitdesign.com/magic/commandref/drc.html)
describes indexed error lookup, and
[findbox](https://opencircuitdesign.com/magic/commandref/findbox.html) centers and
zooms the layout view to Magic's cursor box. No screen coordinates are invented.

The shared `verification.drc.focus` operation requires `project_id`, the source
DRC `run_id`, `error_index` (1–100000), and the current project revision. An optional
timeout ranges from one second to one hour, defaulting to 15 minutes. It requires
execution permission and the ordinary explicit agent approval. It rejects stale,
imported, incomplete, altered or clean reports, out-of-range indices, changed
Magic/PDK identity and unavailable graphical capability. The actual Tcl script
rechecks the error count before finding the requested tile. Starting a GUI never
by itself claims successful navigation.

The resulting Magic run has ordinary immutable script/input evidence, tool/PDK
identity, task status, runtime limits and cancellation. Poll
`verification.drc.focus_status` with the resulting GUI run ID. Its `focused`
boolean means that this server verified the native acknowledgment; `status` says
whether the GUI is still running. After cancellation, the reason explicitly says
the acknowledgment is historical and the navigation window is no longer running.
The box values come directly from Magic. If project sources change after the
request, the status refuses to retain a current focus claim. Imported or
previous-server acknowledgments cannot recreate the private request authority.

Validation used Magic **8.3.684**, SKY130 revision
`1689ac3f2dc763876eaf967227c7dfe831b031ae`, and a private Xvfb in the Ubuntu 24.04
acceptance image. Two isolated undersized metal1 rectangles produced four actual
DRC tiles. Indices 1 and 2 returned distinct boxes
`[2000, 2002, 2002, 2028]` and `[2000, 2000, 2028, 2002]`; both managed GUI processes
then reached `cancelled`. These are tool-returned layout coordinates in this
fixture, not reusable screen positions or a signoff claim. The container had no
network, dropped capabilities, read-only source/PDK mounts and a temporary writable
filesystem. It did not connect to a preexisting host display.

The browser fixture separately checks the selected-error control, delayed native
acknowledgment display, stop and stale-source rejection through the real registry
and run manager. Its executable and small PDK-path fixtures do not perform DRC;
actual tool behavior is covered by the explicit Magic/Xvfb acceptance above.

```sh
python3 -m unittest tests.test_drc_navigation -v
AF_DRC_FOCUS_REAL=1 xvfb-run -a python3 -m unittest \
  tests.test_drc_navigation.DrcNavigationTests.test_actual_magic_gui_finds_real_sky130_error_and_acknowledges_box -v
```

The opt-in test defaults to `/opt/eda/bin/magic` and `/pdk`; set `AF_DRC_CONFIG` to
a JSON project configuration to use another prepared local installation. The
test creates only temporary projects. `tests/browser-drc-navigation.cjs` needs a
disposable test server with a nonempty fixture-only `DISPLAY`; its configured
Python executable never connects to an X server.
