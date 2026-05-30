# Contributing

## Project scope

SoundCloud Downloader is for authorized SoundCloud access only. The current MVP
focuses on one-track downloads with explicit policy checks, local encrypted
OAuth state, and local artifact handling.

Do not add playlist download, indexing, queueing, TUI, or new pipeline behavior
without an explicit approved task.

## Safety boundaries

- DRM bypass and protected-stream decryption are out of scope.
- Token scraping, browser cookie extraction, credential theft, and stolen or
  leaked credentials are out of scope.
- New network behavior must be gated by settings and tested with mocks.
- New filesystem writes must be gated by settings and tested inside `tmp_path`.
- `ffmpeg` behavior must be wrapped behind ports/adapters and tested with
  fakes or mocks.
- Secrets and raw stream, manifest, or segment URLs must not appear in logs,
  reprs, exceptions, CLI output, or tests.

## Development setup

```bash
python -m venv .venv
PYTHON=.venv/bin/python make install-dev
```

## Running checks

```bash
PYTHON=.venv/bin/python make check
PYTHON=.venv/bin/python scripts/check.sh
```

## Code style

- Use Python 3.11+.
- Prefer typed public interfaces and small functions.
- Use `pathlib.Path` for filesystem paths.
- Keep comments sparse and focused on non-obvious safety or protocol details.
- Preserve existing module boundaries and application/domain/infrastructure
  separation.

## Testing expectations

- Add tests for non-trivial behavior.
- Unit tests must not require real SoundCloud credentials.
- Unit tests must not make real network calls.
- Policy, redaction, filesystem, and media behavior should have focused tests.
- External-service integration tests must be opt-in and skipped by default.

## Commit style

Use concise English commit messages. Conventional prefixes are preferred where
they fit, for example:

- `feat: add track download workflow`
- `test: harden end-to-end download pipeline`
- `docs: add smoke test guide`

## Pull request checklist

- The change stays within the approved task scope.
- Safety boundaries are preserved.
- New network, filesystem, and `ffmpeg` paths are gated and tested.
- No secrets or sensitive URLs are committed.
- `PYTHON=.venv/bin/python make check` passes.
- `PYTHON=.venv/bin/python scripts/check.sh` passes.
