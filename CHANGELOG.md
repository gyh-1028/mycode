# Changelog

All notable changes to MyCode are documented in this file. The project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Persistent model profiles with `mycode model presets/list/add/use/key/edit/remove`.
- Selectable GPT, DeepSeek, Claude, GLM, Gemini, Qwen, MiniMax, and separate Kimi Coding Plan/Open Platform catalogs.
- Provider-aware inference controls for reasoning effort, adaptive thinking, thinking toggles, and Qwen token budgets.
- Added execute, plan-only, and read-only review modes to the Web workbench.
- Added standard confirmation, read-only, and full-trust permission profiles with an explicit full-trust warning.
- Moved command and diff approvals from the activity inspector into a blocking approval dialog.
- Corrected Kimi Coding Plan to low/high effort with Thinking fixed on, and added Kimi K2.7 Code.
- API keys stored in the operating-system credential vault through `keyring`; environment variables remain supported and take precedence.
- Added a versioned, validated model catalog with package metadata and pricing lookup.
- Added a repeatable code-intelligence benchmark for cold and incremental indexing.
- Added structured Web activity, per-file diff, context inspection, file/selection attachments, and checkpoint undo.
- Added Web session filtering, quick model switching, plan execution, and run-scoped exact-operation approvals.

### Changed

- `doctor`, `config show`, and REPL `/model` report the active model profile and credential source without exposing secret values.
- The `web` extra now installs a WebSocket protocol implementation required by the browser workbench.
- Session, model-profile, and checkpoint files now use locked, durable atomic writes with stale-writer conflict detection.
- Runtime instances reuse and explicitly close MCP and code-intelligence services across prompts.
- Code intelligence uses Git-aware incremental updates, batched SQLite writes, and a large-repository object reader.
- CI and release workflows now enforce Python quality gates, offline evals, Web tests, package checks, and static-asset consistency.
- Web authentication exchanges the URL fragment for a tab-scoped token and automatically reconnects after refresh.

### Fixed

- Revalidate checkpoint write and undo paths to reject project escapes and symlink retargeting.
- Do not persist or restore full-trust Web permissions across browser sessions, sessions, or model switches.
- Close SQLite and runtime resources deterministically, including startup-failure paths.
- Detect modified untracked files during incremental indexing.
- Repair corrupted provider and model display text and clean Windows Web-preview shutdown.
- Prevent session switching during active runs, retain terminal errors, and refresh changed workspace files after a run.
- Print the authenticated launch URL when `mycode web --no-open` is used.

## [0.2.2] - 2026-07-02

### Added

- Trace subsystem wired into the product: `MYCODE_TRACE=1`, `--trace`, and
  `mycode trace list/show/replay` for CLI/TUI/VS Code observability.
- OpenTelemetry metadata-only span export (OTLP) with `MYCODE_TRACE_OTLP_*`
  overrides.
- Session schema versioning: legacy v0 files are migrated to v1 in memory and
  persisted on next save; corrupt sessions are skipped in listings and surface
  readable errors on explicit load.
- `mycode doctor` reports the count of corrupt session files.
- Quality gates: ruff (`E/F/I/B/UP`), Pyright standard mode, and pytest-cov
  80% threshold, enforced by a new CI quality job.

### Changed

- `MyCodeRuntime.run_prompt` is the single execution path shared by CLI, TUI,
  and VS Code stdio server; it creates `TraceWriter` and `OtelEventSink` when
  trace/OTLP are enabled.
- CLI one-shot and REPL flows now go through `MyCodeRuntime` instead of calling
  `run_agent` directly.

## [0.2.1] - 2026-07-01

### Added

- TUI config wizard: first-time `mycode tui` runs can configure provider/model
  and set the API key in-process without leaving the terminal.
- Real streaming Markdown conversation in the TUI, with auto-scroll and manual
  scroll pause.
- Selectable activity list in the TUI showing model calls, plans, tool status,
  duration, and errors; tool details are redacted and capped at 20,000 chars.
- Reasoning content (e.g. DeepSeek R1) is shown in a collapsible fold under the
  matching model call.

### Changed

- `MyCodeRuntime.new_session()` and `get_session()` accept `persist=False` so
  the TUI avoids creating empty session files before the first user message.
- Tool events now include optional `tool_call_id` and `duration_ms` fields.
- Protocol v1 schema documents the new optional tool-event payload fields.

### Fixed

- TUI no longer duplicates the assistant answer at the end of a stream.
- Pending approvals are automatically rejected and waiting workers released on
  cancel, modal close, or app quit.
- Status strings are displayed in Chinese in the TUI status bar.

## [0.2.0] - 2026-06-30

### Added

- Textual full-screen TUI with sessions, streaming output, tool activity, diffs,
  usage metrics, cancellation, and permission dialogs.
- Versioned JSON-RPC 2.0 stdio service for local editor integrations.
- VS Code workspace extension with session tree, agent webview, editor selection
  context, tool activity, cancellation, and permission responses.
- PyPI/pipx and VSIX build pipelines with offline tests and Trusted Publishing.

### Changed

- Distribution name is now `mycode-ai-cli`; the `mycode` import and command stay
  unchanged.
- Supported Python versions are 3.11 through 3.14.
- Project license changed from provisional metadata to Apache-2.0.

## [0.1.1] - 2026-06-01

- Multi-provider agent loop, file and shell tools, permissions, sessions,
  context compaction, checkpoints, trace, evals, MCP client, skills, and plugins.
