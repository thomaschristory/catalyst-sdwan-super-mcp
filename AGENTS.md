# AGENTS.md

This repo follows the same conventions as `CLAUDE.md`. The two files are kept in
sync; agents that don't read `CLAUDE.md` should read this one.

See [CLAUDE.md](CLAUDE.md) for the full project documentation: stack, architecture,
auth, project layout, data flow, CLI, Docker, and key decisions.

## Quick reference for agents

- Source lives in `sdwan_mcp/`. Tests in `tests/`. Docs in `docs/`.
- `uv sync --group dev --group docs` to install everything.
- `uv run pytest -v` runs the suite. `uv run ruff check sdwan_mcp tests` lints.
- CI enforces lint + tests + docker build + mkdocs strict build.
- Default behavior is read-only. The `--read-write` flag is the only way to register
  mutating operations.
- Specs go in `specs/{version}/` and may be `.yaml`, `.yml`, or `.json`. Filenames
  inside a version folder are arbitrary; they're merged in name order.
- New vManage version = drop a new folder + change `sdwan.active_version` in
  `config.yaml`. No code changes.
