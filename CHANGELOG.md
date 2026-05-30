# Changelog

## Unreleased

### Added

- OAuth PKCE helper flow for authorization URLs and persisted sessions.
- Encrypted OAuth authorization-session and token stores.
- Auto-refreshing token provider for authenticated workflows.
- Authenticated SoundCloud resolver.
- Track metadata normalization.
- HLS manifest retrieval and analysis.
- DRM and encrypted-stream fail-closed policy.
- HLS segment planning, staging, and media assembly.
- M4A remux pipeline.
- MP3 and WAV export pipelines.
- One-track download CLI.
- Doctor CLI for local configuration checks.
- End-to-end mocked download pipeline tests.

### Changed

- MVP command documentation now covers version, doctor, and single-track
  download examples.

### Security

- Protected, DRM, encrypted, preview-only, and rights-restricted sources are
  denied safely instead of being decrypted or bypassed.
- CLI and logging paths redact tokens, secrets, manifest URLs, segment URLs,
  and transcoding endpoint URLs.

### Documentation

- Added README MVP usage guidance.
- Added release checklist.
- Added security note.
- Added smoke test guide.
