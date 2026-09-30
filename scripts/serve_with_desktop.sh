#!/bin/bash
# Reconnect to the existing OSIC desktop before starting Analog Factory.
set -euo pipefail
AF_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$AF_REPO_ROOT/scripts/connect_remote_desktop.py"
source "$AF_REPO_ROOT/.tools/desktop/env.sh"
cd "$AF_REPO_ROOT"
exec python3 -m analog_factory --workspace "$AF_REPO_ROOT/.workspace" serve "$@"
