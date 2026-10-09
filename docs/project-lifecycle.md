# Project lifecycle and restoration

Use **Manage** beside the project selector to rename projects, move or rename the
selected file, send a project or file to trash, restore trash, or import a ZIP.
**Download full backup** saves sources, file revision history, file trash, supported
run/job/evaluation evidence, training tasksets/checkpoints/optimizer receipts, and
conversations in one checksummed ZIP. Large archives use disk-backed transfers.

Project trash is reversible. It hides the project from the active selector and
keeps its original directory, identifier, files and evidence. Restoring it makes
that same project selectable again. There is no permanent deletion control.
File trash keeps the file's contents and revision history together. Restore uses
the original path unless you supply a different destination; it never overwrites
an existing file. Moving a file also moves its revision history, so an older
revision can still be restored at the new path.

Save editor drafts before moving or trashing files or switching to an imported or
restored project. Project trash, file move/trash/restore, and full backup reject
active runs, orchestration jobs, native terminals and agent turns, including workers that are still
finishing cancellation. A rename only changes the project display name.

## Portable exports and full backups

The existing **Export** action produces a portable source ZIP with evidence under
`evidence/`. Import accepts this ZIP or a ZIP of visible source files. A ZIP
without `project.json` receives the name “Imported project,” unless you choose a
name in the import form.

A full backup contains `af-backup.json` with format
`analog-factory-project-backup`, schema version 1, and a SHA-256 digest for every
other file. Supported hidden records are revision history, file trash, run/job
and evaluation evidence, training evidence and immutable tasksets, agent
conversations and the older chat records. Training source snapshots, trajectories,
safe-tensor adapters, tokenizer files and opaque optimizer states remain evidence;
the archive never includes globally installed base models or training runtimes.
Credential stores, arbitrary hidden files, native agent state, symbolic links,
and cancellation markers are excluded. Recognized credential fields in project
metadata or supported internal JSON make backup/import fail. Conversation text
and user-authored sources are included; do not put credentials in those files or
messages.

Importing either format creates a **new project identifier**. It never replaces
an existing project. Declared `project.design` source/netlist paths, cell, family
and ports are restored under the published project schema; paths must stay in
visible project files. Specification files are preserved as source content.
These defaults preserve the OTA reference's native source selection, but neither
its family label nor imported evidence grants reference qualification. Missing
derived netlists still require fresh local tool runs and explicit publication.
Full-backup restoration preserves source revisions and
trash identifiers, but it clears native agent session identifiers, pending
approvals and process ownership. Interrupted execution is recorded as interrupted
or failed; importing a backup does not launch tools or resume an agent. Imported
run records are marked stale and unverified. Original commands, environment,
external dependency paths and verification claims remain available as inert
`imported_provenance`; viewing those records never checks paths on the original
machine. Archived commands cannot be retried. Start a fresh run through the
normal simulation or verification controls to obtain locally verified evidence.
Imported training jobs are marked `imported_evidence` and lose process ownership.
Worker request paths are removed. Imported checkpoints cannot be evaluated or
resumed; archived Python sources, optimizer pickles and worker requests are never
executed or deserialized by restoration.

Project configuration and tool overrides are restored for an authorized caller.
An archive with nonempty configuration also requires `configure` permission,
and configuration must pass the same schema and value validation as
`project.configure`. Direct source-only imports need `write` permission. Forwarded
frontend imports additionally require `configure`, since the browser snapshot
deliberately omits archive contents and cannot certify a source-only ZIP. Credential
values and credential-control fields are never restored.

All archive validation completes before publication. Files are staged in a hidden
temporary directory and become an active project through a single directory
rename. Rejected imports leave no visible project. ZIP traversal, absolute paths,
backslash or drive aliases, device names, hidden credential paths, symbolic links,
special files, encryption, duplicate/case/Unicode-normalized names, file/directory
conflicts, checksum failures and malformed supported records are rejected.
Evaluation episode indices must contain ASCII digits; every supported nested
run manifest receives the same authority removal as a top-level run.

| Limit | Maximum |
|---|---:|
| ZIP via bounded transfer | 1 GiB |
| Total expanded data via transfer | 2 GiB |
| One expanded file via transfer | 512 MiB |
| Entries, including directory entries, via transfer | 16,384 |
| One metadata JSON / central directory | 16 MiB |
| Expansion ratio per entry | 200:1 |
| One transfer chunk | 256 KiB |

The browser selects a ZIP without reading it all into memory, then uploads
`File.slice` chunks. Backup/export prepares a temporary ZIP on disk and downloads
through a registry-authorized streaming HTTP adapter. Generated archives use
stored ZIP members so repeated tensors or sparse data cannot create expansion
ratios that restoration rejects. Oversize archives fail explicitly and are never
silently truncated. Legacy `project.import`/`project.backup` base64 envelopes retain
the smaller 2 MiB ZIP / 64 MiB expanded / 16 MiB member limits; larger clients use
the transfer operations. See [artifact transfers](artifact-transfers.md).

## Shared operations

The UI, CLI, MCP and agents use the same registered handlers and policy checks.
Project mutations require the revision returned by a recent query as
`expected_version`. Transfer reservation/chunks affect only private temporary files;
`project.import.finish` checks the workspace revision before publication. Agent
mutations retain the existing explicit approval policy.
Project-scoped agents cannot import a separate project or inspect another
project's trash.

| Operation | Parameters in addition to `project_id` |
|---|---|
| `project.rename` | `name` |
| `project.trash` | — |
| `project.restore` | — |
| `project.trash.list` | Optional `project_id`; otherwise workspace trash |
| `project.backup` | —; returns a base64 ZIP download envelope |
| `project.import` | No `project_id`; `content_base64`, optional `name` |
| `project.archive.prepare` | `mode`: `backup` or `export`; returns a private transfer handle |
| `project.import.begin` | No `project_id`; declared ZIP `size` |
| `project.import.append` | No `project_id`; `transfer_id`, byte `offset`, chunk `content_base64` |
| `project.import.finish` | No `project_id`; `transfer_id`, optional `name`; normal workspace revision |
| `file.move` | `path`, `destination` |
| `file.trash` | `path` |
| `file.trash.list` | — |
| `file.restore_deleted` | `trash_id`, optional `destination` |

`file.restore` continues to restore an earlier revision of a visible file;
`file.restore_deleted` restores a file from trash.

## Controller integration

`static/project-controls.js` exports `AFProjectControls.create`. Pass the shared
`state`, `esc`, `t`, `toast`, `refreshProject`, `selectProject`, `openFile`, and an
`onProjectsChanged` callback. Call `mount(host)` once and `render()` when project
selection changes. The controller creates its own accessible dialog and invokes
business operations exclusively through `AFOperations.invoke`.

Its `commands` and `snapshot` can be registered with
`AFOperations.register('lifecycle', controls.commands, controls.snapshot)`.
Commands are `open`, `close`, `set_draft`, `submit`, `backup`, and `refresh_trash`.
`open` accepts `action` (`manage`, `rename`, `trash`, `move`, `file_trash`, `import`,
`restore_project`, `restore_file`) and optional `project_id` or `trash_id` for
restoration. `set_draft` accepts `name`, `destination`, and/or `content_base64`.
The remaining commands take an empty object. Archive contents are omitted from
frontend snapshots. Snapshots expose a draft revision so concurrent edits still
invalidate pending frontend commands.

Register the backend with
`analog_factory.operation_lifecycle.register_lifecycle(registry)`. Ordinary
operations must reject trashed projects; project detail, backup, restore, and
trash-list queries remain available. Workspace revisions must include project
metadata so import and project-list changes participate in optimistic locking.

Run the focused filesystem, archive-security and policy tests with:

```sh
python3 -m unittest tests.test_project_lifecycle tests.test_projects -q
node --check analog_factory/static/project-controls.js
```

These tests create temporary workspaces and never use the user's `.workspace`.
