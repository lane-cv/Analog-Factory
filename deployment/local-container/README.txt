Local container deployment
==========================

This template records the container deployment approach used on this device.
It uses Docker Compose on Linux with host networking and the existing
hpretl/iic-osic-tools:2026.08 image, adding Fluxbox and x11vnc. The image supplies
Python and the EDA/PDK environment under /foss. Its tag and apt packages are not
frozen: this is not the reproducible acceptance image in deployment/ubuntu24.

Run the commands below from a fresh checkout as the normal account that owns
it. Docker and the Docker Compose plugin must already be available. manage.sh
derives AF_REPO, AF_UID and AF_GID from the checkout and calling user, so the
checkout is mounted at the same absolute path inside the container. Keep that
path after initialization; the generated runtime configuration uses it.

First installation
------------------

  deployment/local-container/manage.sh build
  deployment/local-container/manage.sh run --rm --no-deps app 'python3 -m venv --system-site-packages .venv && .venv/bin/python -m pip install --no-build-isolation --no-deps -e . && .venv/bin/python deployment/local-container/bootstrap.py'
  deployment/local-container/manage.sh up -d

bootstrap.py accepts only a new empty workspace with no existing deployment
configuration. It prepares .workspace and .tools/deployment/config.json, using
the image's EDA tools and prepared SKY130 PDK. It does not create test projects
or run acceptance. The Python environment belongs to the container; run it
through manage.sh rather than with the host's Python.

Open http://127.0.0.1:8765. The application and noVNC backend bind to host
loopback ports 8765 and 6080; open Drawing desktop / 图形桌面 from the application.
For access from another device, use an SSH tunnel to port 8765. This is a local
single-user deployment; the application does not provide network-user login.

  deployment/local-container/manage.sh ps
  deployment/local-container/manage.sh logs -f --tail 100 app
  deployment/local-container/manage.sh stop
  deployment/local-container/manage.sh start
  deployment/local-container/manage.sh restart

The restart policy is unless-stopped. Workspace files, generated configuration
and CLI state persist in the mounted checkout across container recreation.

Proxy configuration is optional. AF_HTTP_PROXY and AF_HTTPS_PROXY default to
empty; set them in the invoking shell before running manage.sh up -d when
needed. Host networking allows a container to reach a proxy on host loopback.
Localhost requests bypass the proxy. Do not commit proxy credentials.

CLI installation and registration
---------------------------------

The template does not install or authenticate Codex or Claude Code. To use the
existing project-local installer, supply an available container-compatible
Node.js 22+ installation with npm; this image is not assumed to include one:

  deployment/local-container/manage.sh exec -T app .venv/bin/python scripts/setup_agent_clis.py --node /absolute/container/path/to/node

That installer uses the versions pinned in scripts/setup_agent_clis.py and
creates .tools/agent-clis/bin/codex and .tools/agent-clis/bin/claude. Alternatively,
use native CLI binaries already installed for the container's Linux runtime.
For workspace defaults, open Settings -> Local tool paths & process kit, set
each executable's absolute container path, and click Save configuration.
For a project-specific override, first create/select a project and then use
Settings -> Agent CLIs. These forms preserve the other EDA and CLI settings.
Do not replace the whole tools mapping with a mapping containing only one CLI.

Compose sets private, persistent authentication/configuration directories:

  CODEX_HOME         <checkout>/.tools/deployment/codex-home
  CLAUDE_CONFIG_DIR  <checkout>/.tools/deployment/claude-home

Sign in to Codex from the container with the executable path registered above:

  deployment/local-container/manage.sh exec app /absolute/container/path/to/codex login --device-auth
  deployment/local-container/manage.sh exec app /absolute/container/path/to/codex login status

Follow the current device-code instructions printed by Codex. Create/select a
project, then select Codex CLI and CLI default in the application's assistant.
Version status in the settings panel does not check login status.

Claude Code with the saved model API
-----------------------------------

The optional sync script copies the selected workspace/project provider's
locally saved, endpoint-bound API key into private Claude settings. It requires
an HTTPS endpoint whose gateway also supports Anthropic Messages. A working
OpenAI-compatible endpoint alone does not establish that compatibility.

For the workspace's provider, run:

  deployment/local-container/manage.sh exec -T app .venv/bin/python deployment/local-container/configure_claude_api.py

For a project's effective provider, add its ID:

  deployment/local-container/manage.sh exec -T app .venv/bin/python deployment/local-container/configure_claude_api.py --project-id PROJECT_ID

The script removes a trailing /v1 from the base URL for Claude, sets
ANTHROPIC_AUTH_TOKEN to the matching saved key, and keeps the configured model.
It writes the private settings with mode 0600 in a directory with mode 0700;
the key is not printed. Select Claude Code and CLI default in the assistant.
The model remains the configured API model, which need not be a Claude model.

This is one shared container configuration snapshot: Claude requests from all
projects use it. After changing the API URL, saved key or model, rerun the sync
command for the intended provider. The next Claude request uses the new settings;
no container restart is needed.

Local evidence and private state
-------------------------------

To run the Python regression suite in this container, include its EDA tools in
the test process's PATH, as the application runner does for normal operation:

  deployment/local-container/manage.sh exec -T app /bin/bash -lc 'export PATH="/foss/tools/bin:$PATH"; .venv/bin/python -m unittest discover -s tests'

On 2026-10-10, the existing deployment on this device passed application HTTP,
noVNC, real Xschem/Magic launch and cancellation, and the OTA pre/post simulation,
DRC, LVS and extraction flow with negative DRC/LVS controls. Codex 0.162.1 and
Claude Code 2.1.281 each passed an actual conversation and a read-only
list_operations MCP smoke check in separate test workspaces. Claude used the
model selected by the existing API configuration through CLI default.

These results describe the existing local deployment, not a fresh installation
of this generalized template. They do not establish that a different machine,
image revision, gateway or account will give the same results. The existing
deployment used tool versions different from the frozen Ubuntu acceptance image.

Generated state and evidence remain ignored by Git:

  .workspace/                    Workspace, settings, projects and credentials
  .tools/deployment/             Runtime configuration and private CLI homes
  .tools/                        CLI binaries, tools and installation records
  .validation/                   Acceptance and CLI smoke reports and logs
  .venv/                         Container Python environment

Commit only the shareable template and source documentation. Login state,
API keys, project data, binaries, installation records and logs are not part of
the template and must not be committed or copied into a new deployment.
