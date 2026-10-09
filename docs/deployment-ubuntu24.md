# Ubuntu 24.04 deployment and reproducibility

Ubuntu 24.04 LTS is the primary target. Core use requires Python 3.12+; simulation
requires ngspice. Xschem/Magic windows require X11 or XWayland. The browser and
native editor processes remain coordinated through project sources and run records.
The application is a local single-user service. No GPU or provider credential is
needed for manual design.

For direct use, run `python3 -m analog_factory serve --open` from the checkout.
Install the toolchain and a prepared PDK as described in [local toolchain setup](local-toolchain.md).
YAML interchange is optional: `python3 -m pip install '.[yaml]'` in a virtual
environment. Training has its own [pinned setup](training.md); do not add its ML
packages to the core interpreter.

## Isolated acceptance image

The image in `deployment/ubuntu24/` is a reproducible acceptance environment:

- Official Ubuntu 24.04 base pinned by digest in the Dockerfile.
- Entire apt package closure selected from `20261007T000000Z`, including Python,
  ngspice, Netgen, Tcl/Tk/X dependencies, build tools, Xvfb and optional PyYAML.
  [Ubuntu snapshot service](https://snapshot.ubuntu.com/) describes snapshot use.
- Magic 8.3.684 and Xschem 3.4.7 source archives pinned by commit and SHA-256 in
  `sources.json`. The build verifies both digests before compiling.
- A public CA trust bundle bootstraps verified TLS. The runner records its hash;
  it copies no private keys, authentication files or user workspace.
- The prepared SKY130 installation is an explicit read-only input. The replay
  records its installation manifest and tools record dependency hashes. It does
  not pretend that a raw foundry source checkout is a complete prepared PDK.

Build and replay from the checkout, with Docker available:

```bash
python3 scripts/validate_ubuntu24.py \
  --pdk-root /absolute/path/to/prepared/sky130/INSTALLATION
# Reuse that exact locally built image:
python3 scripts/validate_ubuntu24.py --skip-build \
  --pdk-root /absolute/path/to/prepared/sky130/INSTALLATION
```

The PDK directory must contain `sky130A/libs.tech/combined/sky130.lib.spice`.
The driver copies only allowlisted source trees to a fresh directory, excludes
hidden user state/symlinks, and records an input source manifest. Docker receives
read-only source and PDK mounts plus an isolated writable evidence directory.
Runtime has no network, no Linux capabilities, no privilege escalation, a bounded
PID count, 6 GiB memory and two CPUs. `--init` reaps children and allows Xvfb's
readiness signal to work correctly.

The runner executes the full Python suite, mathematical/registry checks, the real
OTA schematic/DRC/LVS/PEX/pre/post flow with negative controls, and actual native
Xschem/Magic start/cancel under Xvfb. It records exit codes and machine-readable
results under `.validation/ubuntu24/replay-*/`, including logs, package versions,
image ID, source manifest, OTA records and `ubuntu24-acceptance.json`.

The final replay on 2026-10-08 passed: 482 Python tests (4 skips), waveform/provider/
frontend-state Node checks, 223-operation registry validation, actual OTA with
negative DRC/LVS controls, and native Xschem/Magic start/cancellation. Evidence is
in `.validation/ubuntu24/replay-e51e6l14/ubuntu24-acceptance.json`; the exact image is
`sha256:3bf6eb0359242b1ef02b8a00c9825f0e272c0168435e32abfd64ab29927c0207`.
Its manifest was checked against the final implementation and tests. See
[current status](status.md) for host/browser results and the separate opt-in checks.

Earlier replays exposed protocol fixture compatibility, font coverage, legacy
Magic capacitance and catalog-result size regressions. All were fixed before this
successful frozen replay. A running container or successful image build alone is
not acceptance.

Xvfb tests actual GUI processes, not a human desktop/compositor. Browser/noVNC
interaction has separate tests. The offline image does not include the optional
2.3 GiB ML runtime/model or native provider logins; those availability-dependent
checks are reported separately. Capacitive PEX does not establish distributed-RC,
antennas/density coverage, silicon performance or foundry signoff.
