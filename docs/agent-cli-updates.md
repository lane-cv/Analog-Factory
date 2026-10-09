# CLI configuration health and official release observations

Codex and Claude executable paths remain project/workspace tool settings (`tools.codex` and `tools.claude`). A project override takes precedence; an empty effective setting uses PATH. Configuration health does not read credential files, run inference, install software, upgrade a CLI, or change either CLI's global configuration.

The unified operation registry exposes three read-permission operations:

| Operation | Behavior |
| --- | --- |
| `agent.cli.status` | Optional `project_id`; probes configured executable versions and returns paths, effective configuration source, normalized version, and cached update comparison. Authentication is explicitly `not_checked`. Raw CLI stdout/stderr and diagnostic text are not returned. |
| `agent.cli.releases` | Reads cached public release observations; performs no network request. |
| `agent.cli.check_updates` | Checks `codex`, `claude`, or both against fixed anonymous official GitHub release endpoints. `force` bypasses the 24-hour success cache but still respects a 60-second attempt cooldown. |

The fixed repositories are `openai/codex` and `anthropics/claude-code`. The result label is **latest published release**. It does not identify Claude's delayed stable channel or claim an installed CLI has updated itself. Canonical repository/tag links, non-draft/non-prerelease flags, three-component numeric versions and publication timestamps are validated. Redirects, malformed releases and responses exceeding 2 MiB are rejected. Notes are bounded plain text with a truncation flag; consumers must escape them when rendering HTML.

Requests honor the trusted server process's existing HTTP/HTTPS proxy environment through the standard library. HTTPS certificate verification remains enabled, endpoints stay fixed, and there is no user-configurable release source. Proxy addresses, credentials and underlying transport errors are not included in public error messages or cached failure records.

The confined workspace cache `.cli-updates.json` retains the latest successful observation separately from the latest attempt. An offline or failed attempt preserves the previous observation and marks it stale. A matching version is `current` only with a successful latest attempt and a success less than 24 hours old; otherwise comparison is `unknown`. Valid comparisons also distinguish `update_available` and `ahead`. Invalid cache contents become unknown observations; a symlink cache is rejected.

A newly persisted attempt emits an `agent.cli.releases` changed event, including failed attempts that retain an older success. Cache hits and shutdown without persistence emit no change. The event contains identifiers and status only, without release notes or request/CLI outputs, so an already open interface can refresh its cache view while preserving unsaved settings.

Automatic checks are off by default. Workspace-only `cli_updates.automatic` enables checks while the server is running; `interval_hours` accepts integers from 6 through 168, default 24. The owned scheduler invokes the same registered check operation with a read-only scheduler identity and no project identifier. Settings changes wake the scheduler without waiting for its prior interval. Stopping the server disables new checks and prevents pending checks from writing cache after manager shutdown.

Each check invocation has an eight-second deadline, including single-flight waiting, and concurrent callers share the cache/cooldown rather than duplicating successful checks. Response reading is interruptible. System DNS resolution or connection setup inside the HTTP library can outlive the invocation deadline: shutdown does not guarantee immediate termination of those OS/library calls, and an executor thread may remain until they return. Their late results cannot update the cache. This is a caller deadline, not a claim that all network activity is forcibly terminated within eight seconds.

Focused verification is `python3 -m unittest discover -s tests -p test_cli_updates.py`. It covers registry schemas and failure envelopes, secret-free health output, numeric version comparison, persistence, malformed caches, redirect/size rejection, single-flight/cooldown, scheduler opt-in and lost wakeups, deadline failure, and shutdown without late persistence. These use controlled HTTP/fetch fixtures and are not evidence of a live official-service request or complete browser integration; those acceptance results must be reported separately.

## 使用入口

在 **设置 → Agent CLIs（Agent 命令行工具）** 打开面板：

1. 查看 Codex CLI / Claude Code 的实际可执行文件、来源和安装版本。
2. 在已选项目中填写路径并保存；留空或恢复默认会继承工作区配置或 PATH。
   工作区默认路径仍在原来的工具配置中修改。保存不会删除 PDK、EDA 或模型供应商配置。
3. 点击“检查官方更新”读取正式发布版本、发布日期和发布说明。
4. 勾选自动检查并保存，设置 6–168 小时的间隔（默认 24 小时）。服务停止时不会继续检查。

这只配置 Analog Factory 调用现有 CLI 的方式。登录状态始终明确显示为未检查；
请使用各 CLI 原有的登录方式。模型/effort 选择沿用现有能力发现，发布说明不会
自动变成可用模型、兼容性保证或自动安装指令。当前版本与官方发布版本分别显示。

面板草稿由目标浏览器持有：`ui.agent_cli.set_paths` 和
`ui.agent_cli.set_tracking_draft` 只改草稿；`save_paths`、`reset_paths`、
`set_tracking` 使用打开时的版本完成持久化。发生冲突时保留草稿并拒绝覆盖；
结果不明时停止重复保存，需重新打开并检查实际状态。

## Structured examples

Use the existing HTTP `/api/operations/invoke`, CLI or MCP `invoke_operation`
adapter; no separate installation API or agent-specific implementation exists.

```json
{"operation_id":"agent.cli.status","parameters":{}}
```

```json
{"operation_id":"agent.cli.check_updates","parameters":{"backends":["codex","claude"],"force":false}}
```

For automatic checks, read `settings.get` first and pass its returned `version`:

```json
{"operation_id":"settings.update","parameters":{"settings":{"cli_updates":{"automatic":true,"interval_hours":24}}},"expected_version":"VERSION_FROM_SETTINGS_GET"}
```

This writer still requires configuration permission and the applicable human
confirmation. Read-only callers may inspect public release information. For an
actual browser, find `client_id` and `version` with `ui.sessions`, then invoke
`ui.agent_cli.open` with `client_id` and optional `ui_version`. Every browser command
returns the mounted controller's acknowledgment; disconnected targets fail.

Official references: [Codex changelog](https://developers.openai.com/codex/changelog/),
[Codex releases](https://github.com/openai/codex/releases),
[Claude Code setup and update channels](https://code.claude.com/docs/en/setup),
[Claude Code releases](https://github.com/anthropics/claude-code/releases).

## Recorded validation and current limitation

On 2026-10-09, the frozen implementation passed 646 Python tests (five skips),
all nine Node contracts, registry generation (268 operations / 40 compatibility
routes) and the isolated installed-package gate. The initial browser batch passed
23 of 24 suites; its unrelated archive-selection test race was corrected, then
operations → transfers passed against identical product bytes. The new CLI browser
suite itself passed manual/agent parity, conflict preservation, offline cache and
external-event synchronization. Full records are in [delivery status](status.md).

The local native versions were really probed: Codex 0.159.2 and Claude 2.1.280.
Live official requests, including the corrected trusted-environment proxy path,
returned HTTP 403 for both fixed sources. Therefore live latest-version retrieval
remains an environment-dependent acceptance gap, and the application reports
unknown rather than inventing a latest version. Network access to those sources
is required. No authentication/model-access or new-version compatibility claim is
implied by installed version detection or offline fixtures.

Restart the Analog Factory server to load the new operation registrations, then
reload the browser. The update interval and automatic preference are workspace
settings; they are deliberately absent from exported project configuration.
