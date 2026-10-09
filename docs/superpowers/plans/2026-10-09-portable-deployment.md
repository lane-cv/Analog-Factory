# Portable Ubuntu deployment and GitHub publication

The user requested moving this project through GitHub to another Ubuntu/Linux
device and asked us to carry out the preparation. Work in the existing checkout:
its uncommitted implementation is the version to preserve and publish.

## Design

Keep the Python application, operation registry and JavaScript UI. Add native
Ubuntu installation profiles: core, EDA, and EDA with an owned virtual desktop.
Pin Magic/Xschem sources and SKY130 to the already validated revisions. Optional
project-local Node and agent CLIs and the existing isolated training installer
do not modify global login state. Python packages live in a project venv.

Bootstrap only a new workspace, using settings.get/settings.update through the
registry. A repeat install preserves user settings. Reject adoption of an unknown
nonempty workspace. Runtime configuration is separate, secret-free, and points
to this device's paths. Supervise only owned processes; never take over listeners.
An optional systemd user unit uses the same foreground launcher.

Project data is migrated with existing export/import operations, outside Git.
Do not copy host binaries, PDK downloads, credentials or login sessions into Git.
Publication checks working files, staged content and bounded local history before
an ordinary push to the existing remote; do not force push or change visibility.

## Implementation and acceptance

- [x] Standard installer, validated options, resumable phases and dry-run plan.
- [x] Verified portable EDA builder and optional Node/agent installers.
- [x] Foreground app/desktop supervision and explicit systemd management.
- [x] Git publication preflight with redacted evidence and bounded scan limits.
- [x] New-device guide, dependency pins and migration instructions.
- [x] Focused negative/idempotence tests and complete regression suite.
- [x] Fresh Ubuntu installation, actual EDA execution and real desktop smoke.
- [x] Review candidates/history and prepare an ordinary push to the existing private remote.

Report unavailable network, authentication, desktop or training checks explicitly.
Fixtures establish error contracts, not successful tool installation or execution.
This work must not restart or reconfigure the current live workspace.

## Verified outcome

Final Ubuntu replay: 755 tests, 4 skips, no failures. Real desktop/OTA acceptance
passed with 43.36763/43.36810 dB pre/post gain and 28 extracted capacitors. Two
repeat installations preserved user settings/runtime hashes. Detached-worker
cleanup and foreign-process isolation passed real subprocess tests. The two
private project archives also restored through the registered import API.

Publication is checked independently by comparing the remote main ref with the
local commit. Account login/inference, a real systemd user manager, and training
on a new target device remain separate environment-dependent steps.
