# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `mcp.example.json`: a ready-to-copy MCP client config, with a test that keeps its variable names and defaults in
  sync with `config.py`.
- A one-click installer for macOS, Linux and Windows (`scripts/install.sh`, `scripts/install.ps1`, `scripts/install.cmd`).
  It checks the prerequisites, installs the package, registers the MCP server with the Claude Code and DSH profiles
  on the machine, reuses an agent Chrome that is already running, and prints the sign-in steps.
- A documentation set: `docs/architecture.md` (how the pieces fit), `docs/mcp-tools.md` (every tool, its signature and
  its error strings), `docs/looker-export.md` (the export, its verification recipe and the reference numbers),
  `docs/troubleshooting.md` (symptom to fix) and `docs/development.md` (the dev loop and the release flow).
- `AGENTS.md`, for agents working in this repository.
- A second skill, `.claude/skills/looker-report-export`, for the export-and-verify workflow, and a fuller
  `.claude/skills/agent-browser` with the tool reference, the decision rules and the failure modes.

### Fixed
- Google Chrome is now found on macOS. The runtime only looked for Windows and Linux, so an install on macOS
  succeeded and then failed at `ahb start`.
- The Chrome on macOS no longer sends a Linux user agent.
- Chrome is now found in the per-user AppData directory, where the consumer Windows installer puts it, and in the
  fixed Linux paths, which are not always on `PATH`.
- The MCP server is registered with the port and profile the installer chose. Without them it started on the
  default profile, which is a different Chrome than the one that was set up.

## [0.2.0] - 2026-10-21

### Changed
- Restructured as an installable package `agent_headless_browser` (src layout, `pyproject.toml`).
- One CLI, `ahb`, replaces the loose scripts: `start`, `stop`, `status`, `health`, `login-view`,
  `looker-export`, `mcp`. The MCP server's entry point is `ahb-mcp`.
- Settings use `AHB_*` environment variables. The default profile dir moved to `~/.agent-headless-browser/profile`.
- `looker-export` writes CSVs off the event loop, so the MCP server stays responsive during large exports.
- `ahb health` works with any URL and exits 1 when the session is broken.

### Added
- `ahb start --foreground` and systemd user units for running on a server.
- Unit tests, a real-Chrome integration test, ruff, mypy (strict), pre-commit, and GitHub Actions CI on Linux and
  Windows.
- Docs: VPS guide, benchmark, contributing, security policy.

### Removed
- Cookie seeding into fresh profiles. It does not work for Google since DBSC and can revoke the session.

## [0.1.0] - 2026-10-20

### Added
- First public version: long-lived agent Chrome, MCP server with 14 tools, Looker Studio export, headless
  `login-view`, research log and benchmark.

[Unreleased]: https://github.com/EgiStr/agent-headless-browser/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/EgiStr/agent-headless-browser/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/EgiStr/agent-headless-browser/releases/tag/v0.1.0
