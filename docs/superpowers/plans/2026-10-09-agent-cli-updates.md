# Codex and Claude project configuration and release tracking

**Goal:** Make the existing Codex/Claude integrations configurable and diagnosable
from Analog Factory, with official release tracking through the same registry.

**Architecture:** Keep Python standard-library services, existing native CLI
adapters, versioned settings/project configuration, and native JavaScript UI.
Public release metadata is workspace-owned; installed status respects project
overrides. Checking releases never installs software or starts model inference.

**Sources:** Official Codex changelog and openai/codex releases; Claude Code setup
documentation and anthropics/claude-code releases. GitHub's latest published
release is not Claude's separately delayed stable installation channel.

**Constraints:** Preserve user workspaces and global CLI authentication. Do not
copy secrets, upgrade executable files, publish, commit or change native approval
rules. Keep existing workspace tool paths and project inheritance. Default
automatic checks off until the user enables the persistent setting; opt-in checks
run only while this application's server runs. Requests use fixed public HTTPS
sources and bounded response/time/cache limits. Failed/stale checks cannot claim
an installed CLI is current or compatible. Login remains explicitly not checked.

## Tasks and owners

- [x] Backend worker: strict cached public release/status schemas and handlers,
  fixed-source HTTPS transport, single-flight/cooldown, persisted errors and opt-in
  scheduler using the same registered checker; focused adversarial tests.
- [x] Root: workspace-only cli_updates settings validation/merge/defaults,
  project-scope status correction, registry and shutdown hooks, UI command schemas.
- [x] Frontend worker: Settings entry, project Codex/Claude override drafts and
  save/reset through project.configure, status/release display, checking and
  tracking controls through registered operations; preserve concurrent drafts.
- [x] Root: generated metadata, CI boundary declarations, portable and real browser
  tests, official-endpoint actual read attempt in a disposable workspace.
- [x] Independent review, focused/full regression and delivery documentation.

## Review focus

1. A configured executable or release listing never proves authentication,
   model entitlement, compatibility or a successful design turn.
2. Unknown/malformed/prerelease/draft/redirect/oversized/network-error responses
   fail explicitly; last successful metadata retains its original timestamp.
3. Project path updates preserve provider/EDA configuration and reject stale
   revisions, including changes during an awaited browser command.
4. Partial settings edits preserve unrelated preferences; automatic checks
   deduplicate and cannot revive after an owned manager shutdown.
5. Official release notes are untrusted escaped text, never executable commands
   or agent instructions; display links are fixed to the corresponding repository.

The requested scope is interpreted as in-app configuration and update notification.
The user can steer it to repository-development configuration or installation
management through the pending preference question without discarding reusable work.

## Acceptance outcome

Implementation is delivered with the host/Node/package/browser evidence in
`docs/status.md`. The initial browser transfer race was a test wait issue and its
corrected replay passed with unchanged runtime bytes. Official live API retrieval
remains unaccepted: both fixed endpoints returned HTTP 403 even with existing
trusted environment proxy support. This limitation is reported, not replaced with
fixture data or guessed release versions. No automatic software upgrade, login or
model invocation was performed.
