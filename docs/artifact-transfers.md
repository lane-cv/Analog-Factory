# Large artifacts and archives

The editor retains its 2 MiB text limit. Binary artifacts and project archives have
separate bounded transfers. The browser uses `File.slice` for archive upload and
a normal browser-managed download for ZIP/artifact output; neither path constructs
a whole-archive JavaScript blob. The server copies in 256 KiB chunks and stores
temporary ZIPs under the private workspace `.transfers` directory.

`artifact.stat` accepts `project_id`, `kind` (`file`, `run`, or `training`), `path`,
and `run_id`/`job_id` when appropriate. Paths are confined to visible project files,
that run's output directory, or that training job's evidence directory. Symlinks,
FIFOs and devices are rejected. The result gives `filename`, `size`, `content_type`,
`chunk_bytes` and `artifact_version`. Files may be at most 2 GiB. This version is a
file identity/change token derived from device, inode, size and nanosecond
modification/change timestamps; it is not a cryptographic content provenance claim.

`artifact.read` takes the same owner/path, `artifact_version`, optional byte
`offset` (default 0), and `limit` (1–262144). It returns a bounded `content_base64`
chunk, offsets, total size and `eof`. File identity is checked before and after
each read. A changed file requires fresh metadata. Queries return `version: null`
to avoid rehashing the entire project for every chunk; the operation descriptor's
`source_revision`/`revision_policy` declares this. Explicit `expected_version`
still checks project state. Normal editing and final import revisions are unchanged.

For source exports or full backups:

1. Invoke `project.archive.prepare` with `project_id` and `mode: "export"` or
   `"backup"`. Preparation requires the project to have no active execution.
2. Use `transfer.read` with `project_id`, `transfer_id`, `offset` and `limit`, or
   download `/api/transfers/download?project_id=…&transfer_id=…` with the same caller.
3. Release a chunked download with `transfer.release`. A completed HTTP download
   releases its temporary ZIP automatically; interrupted downloads remain bounded
   by the transfer's idle lifetime.

For imports, invoke `project.import.begin` with the ZIP byte `size`, append
sequential `project.import.append` chunks with `transfer_id`, `offset` and
`content_base64`, then invoke `project.import.finish` with that handle, an optional
name, and the workspace `expected_version` captured when the import was prepared.
Changed workspace configuration or membership causes a conflict before publishing.
Incomplete uploads, offset replays and writes beyond the declared size are refused.
Release an abandoned upload using `transfer.release`.

Every handle is bound to its principal, caller source, project scope and project
identifier. Knowing a handle grants no access. Project-scoped callers cannot
import another project. Uploads require `write` and the ordinary agent confirmation;
restoring nonempty configuration additionally requires `configure`. Both upload
and inline import use the same traversal, symlink, case collision, ZIP expansion,
record and checksum validator. Validation and authority removal finish in a hidden
directory before a single atomic publish. Imported execution/training records
remain unverified and cannot replay commands, resume workers or load checkpoints.
The [lifecycle guide](project-lifecycle.md) lists preserved evidence and exclusions.

Transfer limits are 1 GiB per ZIP, 2 GiB expanded, 512 MiB per member, 16,384 entries,
16 MiB per metadata JSON/ZIP central directory and a 200:1 expansion ratio. ZIP
central-directory bounds are checked before Python loads that directory. Split
ZIPs and ZIP64 central directories are not accepted. Generated archives use stored
members, including highly compressible tensors, so they obey their own import
ratio policy. The service admits eight handles and reserves at most 2 GiB of ZIP
disk space, with a 30-minute idle timeout. Temporary directories of dead servers
are removed on the next server startup; live-server identities are preserved.
Shutdown removes this server's transfer files. Extracting an import also needs
room for its bounded expanded staging directory.

`/api/artifacts/download` accepts the `artifact.stat` owner/path parameters and an
optional `artifact_version`. Both HTTP download routes retain the ordinary local
origin and credential checks and invoke registered metadata/chunk operations.
Every chunk remains authorized. If an artifact changes during a download, the
connection ends with an incomplete declared content length; no JSON is appended
to binary data. Raw chunk query responses reject idempotency keys, preventing
large binary chunks from being copied into durable operation receipts. Legacy
inline artifact/export APIs reject oversize responses with transfer guidance.

Validation includes a real 68 MiB checkpoint backup/upload/restore with equal
hashes, retained tokenizer/optimizer/source evidence, imported checkpoint denial,
path/FIFO/scope/offset/conflict negatives, bounded file-read assertions, real
localhost HTTP download hashes and a 5 MiB graphical browser upload/download.
All fixtures use temporary directories.

```sh
python3 -m unittest tests.test_transfers tests.test_transfer_http -v
AF_URL=http://127.0.0.1:8765 node tests/browser-transfers.cjs
```
