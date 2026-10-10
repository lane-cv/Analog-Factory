#!/bin/sh
set -eu
af_repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
AF_REPO=$af_repo_dir
AF_UID=${AF_UID:-$(id -u)}
AF_GID=${AF_GID:-$(id -g)}
export AF_REPO AF_UID AF_GID
exec docker compose -f "$af_repo_dir/deployment/local-container/compose.yaml" "$@"
