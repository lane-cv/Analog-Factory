# Editor conflict review and draft recovery

Save file retains the version opened with the file. A stale save leaves the editor
draft and the other writer's disk source intact. Open **Conflict review and draft
recovery**, then **Inspect base / disk / draft** to compare all three complete
texts. Edit **Merged text to prepare as draft**, then choose **Prepare merged
draft**. This changes only the editor draft and pins it to the inspected disk
version. Save file remains necessary; a disk edit after inspection still rejects
the save. Typing, changing file or changing project during a pending inspection
rejects the result without replacing the draft.

Dirty text is retained in browser IndexedDB by project, file and stable
`draft_id`. Each page owns its own variant; concurrent tabs cannot replace or
delete another page's backup. Records keep the exact opened base and version.
After reload, open the same file and explicitly choose and restore a retained
variant. Restoration verifies that the source still exists,
does not save, and retains the old version even when disk is newer. Read and
discard controls affect browser recovery records; discarding leaves the current
editor unchanged. A confirmed clean save removes only that page's recovery record.
Restoring an older page's record prepares a new page-owned variant and retains the
original until explicitly discarded. Refresh retained drafts reads the current
shared store. The selector identifies page ownership and
the draft ID; path-only read, restore and discard require an unambiguous variant.
Legacy localStorage records migrate on the first successful edit without losing
their base, version or text; the original localStorage value is left intact.
Drafts are not
shared across separate browser profiles.

Recovery holds at most eight records and 8 MiB of UTF-8 JSON, with at most 2 MiB
each for base and draft. It does not silently evict another record when full.
Browser quota, unavailable storage and corrupt records are reported; the in-memory
editor remains intact. Browser storage may impose a smaller quota. A failed or
uncertain save is shown explicitly and keeps the draft for disk inspection before
retrying. IndexedDB read/write transactions serialize updates and shared bounds
across tabs; a browser without transactional storage reports recovery unavailable. Recovery
depends on this browser retaining its storage.

After a successful write, Save file reads the actual source through the consistent
reader and confirms that its complete text matches the submission before adopting
the checked source version. A read failure or different text leaves the draft and
opened version intact and reports an unknown outcome. There is no automatic retry.
Typing during verification survives as a dirty draft against the confirmed saved
text; a changed file/project selection rejects the late result.

`ui.editor_recovery.list`, `.read`, `.restore`, `.discard`, `.inspect` and `.resolve`
are registered frontend operations with browser acknowledgement. Normal state
contains counts, selection, revision and status only. Source text requires an
explicit read/inspect command. Small inspections use shared read-only
`file.change.preview`; files above its 64,000-byte bound or diffs exceeding the
256 KiB proposal limit retain complete three-way text through versioned file
reading, within the editor's 2 MiB limit. Inspection pins to the proposal's checked
`project_version` or the file read's checked `source_version`, and ordinary file
opening/clean refresh use that same read version. An outer response version may
reflect a later native edit and is not substituted for the checked text version.
Shared readers revalidate content/file identity and version to reject observed
read races; they do not provide an OS-atomic lock against external file writers.

`node tests/editor-recovery-contracts.cjs` exercises controller bounds, revision
pins, delayed reads, selection changes, quota and corrupt storage.
`node tests/editor-recovery-variants.cjs` checks shared ownership, legacy migration
and the eight-record bound under concurrent updates.
`tests/browser-editor-recovery.cjs` runs against a disposable server workspace and
uses actual two-browser writers, semantic acknowledgements and real controls. It
checks concurrent saved edits, stale review, typing during inspection, reload
recovery, an oversized diff, quota failure and absence of unintended writes.
`tests/browser-editor-version-race.cjs` schedules real native edits after registered
read/preview handlers return but before outer response version capture. It verifies
opening, clean refresh and small/large inspection cannot acquire a newer unreviewed
version or overwrite those edits, and a post-write native edit remains intact.
`tests/browser-editor-ownership.cjs` uses two actual tabs in one storage profile to
check distinct variants, saving and reloading without losing the other draft,
typing/selection during save readback and failed verification without a retry.
