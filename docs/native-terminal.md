# Human native CLI terminal

Choose **Native terminal** in the execution log header to open an interactive
Codex or Claude CLI in the selected project directory. Select the backend,
read-only/plan or workspace-write mode, optional model, dimensions and runtime,
then start. Save an editor draft first. The panel renders terminal control
sequences with a locally bundled xterm.js renderer; it supports ordinary typing,
pasting, native approval prompts, resizing and Ctrl+C. Closing the panel detaches.
Reopening reconnects to the active session; **Stop terminal** ends its process
group. A terminal cannot survive a server restart.

This is a human-owned native session. The CLI uses its existing user login,
configuration and permissions. Analog Factory does not copy credentials or native
configuration, change `HOME`/`CODEX_HOME`, or grant a new login. The project directory
is the initial working directory; the project API's path confinement does not
sandbox native terminal commands. The selected native CLI applies its own sandbox
and interactive approval rules. This distinction is shown before launch.

Installed capabilities are checked from a bounded `--help` probe. Codex must
advertise `--sandbox`, `--ask-for-approval`, `--no-alt-screen` and `--no-daemon`;
it starts with `on-request` approval and `approvals_reviewer="user"`. Claude must
advertise `--permission-mode` with `manual` and `plan`; workspace-write selects
`manual`, and read-only selects `plan`. Unsupported versions fail explicitly.
No arbitrary executable, shell command or argument list is accepted by a terminal
operation. Executable overrides use the ordinary configure-capable project tool
settings. Installed flags are the compatibility authority; see the
[Codex CLI reference](https://developers.openai.com/codex/cli/reference) for native
CLI behavior.

Every terminal action uses the shared registry. `native.terminal.capabilities`,
`list` and `get` return metadata. `start`, `input`, `resize`, `read` and `stop`
require human approval authority; registry contexts identified as embedded or
external agents cannot use them even with a forged approval permission or a
confirmation flag. Their corresponding `ui.native_terminal.*` commands enforce
the same boundary. Native input cannot answer Analog Factory agent approvals or
create automatic scopes. The child environment removes inherited
`AF_OPERATION_*` and `AF_AGENT_*` bridge capabilities.

Start returns a generic `terminal:<project_id>:<terminal_id>` task handle, supporting
the normal task-status and cancellation transports. Process authority lives in
the server's owned process objects. Persisted terminal status contains no
authority to adopt or signal a PID. Abandoned records become interrupted.
Active terminals prevent project/file rename or trash operations. Server shutdown,
stop, timeout and output-budget failure terminate the owned process group,
including its children.

The limits are one active terminal per project, eight per workspace, 32 retained
session buffers, 128 KiB of output per buffer, 8 MiB of lifetime output and a
maximum runtime of four hours. An input request accepts at most 8,192 UTF-8 bytes.
Input acknowledgments state the byte count; a full native input buffer produces
an explicit partial acknowledgment and is not automatically replayed. Output
offsets are byte offsets, and a read indicates when earlier output was truncated.
Use `content_base64` to preserve split UTF-8 sequences. `text` is a convenience
decode of that same chunk.

Terminal input and output are not written to project files, browser snapshots or
operation receipts. Raw `native.terminal.read` responses are volatile: supplying
an idempotency key is rejected before reading. Completed terminal output remains
in bounded server memory until evicted or the server exits. The native CLI may
independently maintain its own ordinary history according to its configuration.
The renderer disables OSC 52 clipboard writes and loads no link or clipboard
addons. xterm.js 6.0.0 is bundled under its MIT license; the npm archive integrity
and extracted file SHA-256 values are recorded in
`analog_factory/static/xterm-source.json`, with the license alongside it. The
[xterm security guidance](https://xtermjs.org/docs/guides/security/) describes why
native terminal output and input need an explicit trust boundary.

Validation uses real POSIX PTYs and temporary native CLI fixtures. The Python
tests exercise controlling-terminal attachment, literal Unicode input, native
approval waiting, dimensions, output truncation, timeout, child-process cleanup,
imported-record rejection, scope/agent denial, task APIs and server shutdown. The
browser test uses the real renderer, keyboard input, detach/reconnect and
acknowledged UI commands. Installed native help was inspected, but these tests do
not perform paid model turns or claim that every installed native backend has
completed authenticated inference.

```sh
python3 -m unittest tests.test_native_terminals -v
AF_URL=http://127.0.0.1:8765 node tests/browser-native-terminal.cjs
```

Run the browser test against a disposable test-server workspace; it creates a
temporary executable fixture and a project with that tool override.
