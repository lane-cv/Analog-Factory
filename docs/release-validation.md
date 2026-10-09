# Release candidate validation

`scripts/validate_release.py` builds the current implementation and verifies an
installed copy without publishing anything. This supplements source-tree tests:
they cannot establish that a wheel contains its JavaScript, fonts, schemas and OTA
templates, or that the installed entry point works outside the checkout.

Use a developer environment with Python 3.12+, `venv`/`ensurepip`, and these build
tools (they are not application dependencies):

```bash
python3 -m venv .tools/release-runtime
.tools/release-runtime/bin/python -m pip install \
  build==1.3.0 setuptools==80.9.0 wheel==0.45.1
.tools/release-runtime/bin/python scripts/validate_release.py
```

CI runs the same command on Ubuntu 24.04/Python 3.12. The validator:

1. Copies only `analog_factory/`, `pyproject.toml`, `README.md` and `LICENSE` to a
   fresh source directory, preserving current uncommitted runtime files. Private
   workspace/tool/cache directories are never staged. Package symlinks and known
   private member names fail validation.
2. Builds an sdist and a wheel from that sdist with the selected installed build
   toolchain, without build isolation or package-index access. The report records
   build-tool versions; the install smoke uses a separate fresh virtual environment.
3. Checks both archives without extraction: no traversal, duplicate names, links,
   known private paths or missing/changed runtime bytes. Inspection is bounded to
   10,000 members and 1 GiB uncompressed data. This is not a general secret scanner;
   new package source contents still require review.
4. Installs the wheel with `--no-deps --no-index`, then runs the entry point and
   imports the product with Python isolated mode outside the checkout. It verifies
   every runtime file against the source manifest, creates an educational project,
   discovers operations and reads a real template through the registry.
5. Starts the installed HTTP service on a disposable loopback port and checks its
   index, JavaScript bytes and operation discovery. The server, workspace and
   virtual environment are cleaned up; no EDA or model call is required.

Each run leaves packages, command logs, SHA-256 hashes, runtime manifests and
`release-validation.json` in `.validation/releases/release-*/`. Failed attempts
remain recorded. Temporary installation paths in successful reports are historical
evidence and no longer exist after cleanup.

This gate does not commit the worktree, tag a release, push a branch, upload packages
or claim a remote CI pass. Local development changes remain uncommitted until a
separate integration decision. EDA/PDK, browser and live-model acceptance have their
own recorded scopes.
