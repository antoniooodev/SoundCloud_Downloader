# Release Checklist

This checklist is the minimum bar for cutting an MVP release of the
single-track download CLI. Keep it short and use it as a gate, not as a
project tracker.

## Pre-release

- [ ] Working tree is clean (`git status --short` is empty).
- [ ] `main` is up to date with `origin/main`.
- [ ] `README.md` reflects the shipped CLI commands and options.
- [ ] `.env.example` mirrors the variables the CLI actually reads.
- [ ] `AGENTS.md` and any internal docs are still accurate.

## Security checks

- [ ] No real OAuth tokens, refresh tokens, client secrets, or Fernet keys
      in the repository (including history of the release commit).
- [ ] `.env.example` contains placeholders only.
- [ ] Logs and CLI output redact tokens, manifest URLs, segment URLs, and
      transcoding endpoint URLs (covered by the security regression tests).
- [ ] No DRM bypass, decryption, license-server, or token-scraping logic
      was added.
- [ ] Encrypted / DRM manifests are still denied fail-closed.

## Test gates

- [ ] `PYTHON=.venv/bin/python make check` passes.
- [ ] `PYTHON=.venv/bin/python scripts/check.sh` passes.
- [ ] `tests/test_cli_download_track.py` passes.
- [ ] `tests/test_e2e_download_pipeline.py` passes.
- [ ] `tests/test_download_security_regressions.py` passes.
- [ ] No test makes real network calls or runs real ffmpeg.

## Manual smoke test

Run against an authorized SoundCloud account in a disposable working
directory.

- [ ] `soundcloud-downloader oauth create-session …` returns a session ID
      and an authorization URL.
- [ ] `soundcloud-downloader oauth exchange-code …` reports
      `access_token_received=true` and `token_persisted=true`.
- [ ] `soundcloud-downloader oauth token-status` reports the profile as
      present and not expired.
- [ ] `soundcloud-downloader download track <URL> --format m4a …` exits 0
      and writes the expected `audio/final.m4a` artifact.
- [ ] Optional spot check for `--format mp3` and `--format wav`.
- [ ] `soundcloud-downloader oauth logout` removes the profile and
      `token-status` reports it absent afterwards.

## Versioning

- [ ] `pyproject.toml` `version` reflects the release.
- [ ] If a changelog or release notes file exists, it lists the headline
      changes for this release.

## Tagging

- [ ] Create an annotated tag from the release commit (`git tag -a vX.Y.Z`).
- [ ] Push the tag (`git push origin vX.Y.Z`).

## Post-release

- [ ] Verify the tag is visible on the remote.
- [ ] File follow-up issues for items found during the smoke test.
- [ ] Confirm no real credentials were used in any release artifact, log,
      or screenshot shared externally.
