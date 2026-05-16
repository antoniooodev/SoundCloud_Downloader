# AGENTS.md

## Project Purpose

SoundCloud Downloader is intended to be a policy-driven Python project for authorized local handling of SoundCloud media metadata and media artifacts. The repository is currently in its governance phase: keep it minimal, clean, and free of application implementation until later tasks explicitly request code.

This project is not a piracy tool, DRM circumvention system, credential extraction utility, scraper shortcut, redistribution platform, or exploit framework.

## Non-Negotiable Safety Constraints

Agents must not implement, scaffold, suggest, or optimize:

- DRM bypass or decryption of protected streams.
- Widevine, FairPlay, PlayReady, or license-server circumvention.
- Token extraction, cookie theft, credential theft, or session hijacking.
- Use of stolen, leaked, hardcoded, or user-browser-extracted credentials.
- Rate-limit evasion, abusive scraping, or undocumented shortcut workflows.
- Downloader logic before an explicit approved implementation task exists.
- Public redistribution features for copyrighted media.

If authorization, entitlement, source legality, encryption status, or rights status is ambiguous, the system must fail closed with a classified error instead of attempting download, remux, transcode, reconstruction, or persistence.

## Policy-Driven Downloader Principle

Any future downloader behavior must be driven by an explicit policy layer. Media operations may only happen after a policy decision allows them.

Required future flow:

```text
resolve input
  -> fetch authorized metadata
  -> inspect available sources
  -> classify entitlement and protection state
  -> apply policy
  -> choose an allowed media strategy
  -> write only approved local artifacts
```

No component may download, remux, transcode, reconstruct, or persist media before policy approval.

## Coding Style

- Use Python 3.11+ unless a later task updates the runtime target.
- Prefer clear module boundaries, typed public interfaces, and small functions.
- Use `pathlib.Path` for filesystem paths.
- Use structured logging in application code instead of `print`.
- Use bounded timeouts and retries for external I/O when such I/O is introduced.
- Avoid hidden global mutable state, broad unclassified exceptions, hardcoded local paths, and hardcoded secrets.
- Keep the repository minimal; do not add framework, service, or application scaffolding before it is requested.

## Minimal-Comment Policy

Comments should be sparse and explain only what clear code cannot.

Allowed comments include security reasoning, non-obvious protocol behavior, external tool quirks, and invariants that are easy to break. Avoid decorative banners, restating code, commented-out experiments, and tutorial-style source comments.

## Error Model

Future errors must be explicit and classified. Security-relevant failures must not be collapsed into generic download failures.

Expected categories include:

```text
AUTH_REQUIRED
ENTITLEMENT_DENIED
PREVIEW_ONLY
DRM_UNSUPPORTED
ENCRYPTED_STREAM_UNSUPPORTED
RIGHTS_RESTRICTED
SOURCE_NOT_DOWNLOADABLE
NETWORK_RETRYABLE
NETWORK_PERMANENT
FFMPEG_FAILED
STORAGE_FAILED
UNKNOWN_UNSAFE
```

Ambiguous states must map to a safe denial category.

## Logging Redaction Rules

Logs must never expose:

- OAuth access or refresh tokens.
- Authorization headers.
- Cookies.
- Credentials.
- Signed media or manifest URLs containing sensitive query parameters.
- Decryption-related material.

Prefer structured fields such as job ID, track ID, decision state, error category, retry count, duration, and artifact checksum when those concepts exist.

## ffmpeg Subprocess Rules

When ffmpeg support is introduced:

- Invoke subprocesses with argument lists, never shell-interpolated strings.
- Validate and control input and output paths.
- Use temporary working directories under controlled storage.
- Set subprocess timeouts.
- Capture stderr for diagnostics.
- Classify failures as `FFMPEG_FAILED` or a more specific safe category.
- Do not pass arbitrary user input directly to ffmpeg arguments.
- Prefer remuxing over transcoding when policy and media characteristics allow it.

## Test Expectations

- Add or update tests for non-trivial behavior.
- Unit tests must not require real SoundCloud credentials.
- Unit tests must not make real network calls.
- Security and policy logic should be covered with focused tests when introduced.
- Integration tests requiring external services must be opt-in and skipped by default.

For this governance-only phase, checking repository state is sufficient unless later tasks add executable code.

## Git and Commit Rules

- Keep commits focused and in English.
- Use conventional prefixes where appropriate.
- Do not mix unrelated changes.
- Do not overwrite user changes without explicit permission.
- Before finishing a task, report changed files, checks run, commit hash, and push result.

Required commit for this task:

```text
chore: add agent guidelines and repo hygiene
```

## README Deferred

Do not create `README.md` yet. The README is deferred until the final phase, after the architecture, CLI, configuration, and supported workflows are stable.

## Definition of Done

This task is complete when:

1. `AGENTS.md` exists at the repository root.
2. `.gitignore` exists and covers Python caches, virtual environments, local data, secrets, logs, and build artifacts.
3. `.editorconfig` exists with conservative cross-editor formatting.
4. No README, application code, network logic, DRM bypass, token extraction, scraping shortcut, or downloader code was added.
5. `git status` was run.
6. The changes were committed with the required message.
7. The commit was pushed to GitHub.
