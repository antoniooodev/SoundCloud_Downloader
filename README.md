# SoundCloud Downloader

## Status

This project is an MVP CLI for authorized SoundCloud track downloads. It is
policy-driven and designed to fail closed for DRM-protected or encrypted
streams. It is not a piracy tool, a DRM bypass, or a credential-scraping
helper.

The current release covers the single-track download flow only. Playlist,
library, and resumable-job features are not implemented yet.

## What works in the MVP

- OAuth PKCE helpers (`authorize-url`, `create-session`)
- Encrypted OAuth authorization-session store
- Encrypted OAuth token store
- Authorization-code exchange and refresh-token rotation
- Authenticated official SoundCloud resolver
- Track metadata normalization
- HLS manifest retrieval
- DRM / encryption detection on HLS manifests
- HLS segment planning
- HLS segment staging through local artifact storage
- Media assembly from staged segments
- M4A remux (ffmpeg copy)
- MP3 export
- WAV export
- Single-track download CLI

## Safety boundaries

- This project does not bypass DRM.
- This project does not decrypt protected streams.
- This project does not attack license servers.
- This project does not use stolen tokens.
- This project is intended for authorized access only.
- Encrypted or unsupported streams are denied fail-closed.
- Logs and CLI output redact tokens, secrets, manifest URLs, segment URLs,
  and transcoding endpoint URLs.

## Requirements

- Python 3.11 or newer (see `pyproject.toml`).
- `ffmpeg` available on `PATH` (remux and export rely on `ffmpeg`).
- SoundCloud developer credentials (Client ID, optionally Client Secret).

## Installation

```bash
python -m venv .venv
PYTHON=.venv/bin/python make install-dev
```

This installs the package in editable mode together with the development
dependencies declared in `pyproject.toml`.

## Configuration

The CLI reads its configuration from environment variables prefixed with
`SCD_`. You can keep them in a `.env` file; copy `.env.example` and fill in
the values you need.

The variables most relevant to authorized downloads are:

| Variable | Purpose |
| --- | --- |
| `SCD_ALLOW_NETWORK` | Master switch for network access. Must be `true` for downloads. |
| `SCD_ALLOW_FILESYSTEM_WRITES` | Master switch for filesystem writes. Must be `true` for downloads. |
| `SCD_SOUNDCLOUD_CLIENT_ID` | OAuth client ID for the official resolver and refresh. |
| `SCD_SOUNDCLOUD_CLIENT_SECRET` | OAuth client secret (if your app requires one). |
| `SCD_OAUTH_SESSION_ENCRYPTION_KEY` | Fernet key for the encrypted OAuth session store. |
| `SCD_OAUTH_TOKEN_ENCRYPTION_KEY` | Fernet key for the encrypted OAuth token store. |
| `SCD_OAUTH_SESSION_STORE_PATH` | Path to the encrypted OAuth session store. |
| `SCD_OAUTH_TOKEN_STORE_PATH` | Path to the encrypted OAuth token store. |
| `SCD_ARTIFACT_STORAGE_ROOT` | Root directory for staged and final artifacts. |
| `SCD_ARTIFACT_TEMP_ROOT` | Root directory for temporary workspaces. |
| `SCD_FFMPEG_BINARY` | `ffmpeg` binary name or path. |
| `SCD_FFMPEG_TIMEOUT_SECONDS` | Subprocess timeout for ffmpeg. |

Generate a fresh Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Never commit real tokens, real client secrets, or real Fernet keys.

## Command reference

```bash
soundcloud-downloader --version
soundcloud-downloader doctor
soundcloud-downloader download track "https://soundcloud.com/example/track" --format m4a
```

## OAuth setup

The OAuth helper commands are the supported way to bootstrap and maintain
tokens. Replace the placeholders below with values from your own SoundCloud
app and your own authorization session.

```bash
# Open an authorization session (also prints the URL to visit).
soundcloud-downloader oauth create-session \
  --client-id "<your-client-id>" \
  --redirect-uri "https://your-app.example/callback" \
  --persist

# After completing the authorization in your browser, exchange the code.
soundcloud-downloader oauth exchange-code \
  --session-id "<session-id-from-create-session>" \
  --code "<code-from-callback>" \
  --state "<state-from-callback>"

# Inspect the stored token state.
soundcloud-downloader oauth token-status

# Revoke local tokens for a profile.
soundcloud-downloader oauth logout
```

These commands respect `SCD_ALLOW_NETWORK` and `SCD_ALLOW_FILESYSTEM_WRITES`
and write tokens only into the encrypted token store.

## Token lifecycle commands

| Command | Purpose |
| --- | --- |
| `oauth authorize-url` | Build a PKCE authorization URL (no session persisted). |
| `oauth create-session` | Create an authorization session (in-memory or encrypted store). |
| `oauth exchange-code` | Exchange a returned code for tokens and persist them. |
| `oauth token-status` | Report whether a token profile is present / expired. |
| `oauth logout` | Delete the encrypted token entry for a profile. |

The resolver and download commands transparently refresh expired tokens
when a valid refresh token is available.

## Downloading a track

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --format m4a \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes
```

Other formats:

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --format mp3 \
  --output-profile aac_m4a \
  --allow-network \
  --allow-filesystem-writes
```

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --format wav \
  --output-profile aac_m4a \
  --allow-network \
  --allow-filesystem-writes
```

Useful options:

- `--access-mode {public,go_plus}` — choose the policy context.
- `--output-profile` — explicit reconstruction profile.
- `--token-store-path`, `--artifact-storage-root`, `--artifact-temp-root` —
  override paths without editing the env file.
- `--json` (default) or `--plain` — choose JSON or key/value output.
- `--env-file` — load a specific `.env` for the run.

The command exits non-zero with a generic `Track download failed.` message
when the workflow cannot complete safely (DRM denial, policy denial, network
failure, ffmpeg failure, storage failure). It never prints tokens, raw
manifest text, manifest URLs, segment URLs, or transcoding endpoint URLs.

## Output formats

| Format | Notes |
| --- | --- |
| `m4a` | AAC remuxed into MP4 (no transcoding). Preferred for fidelity. |
| `mp3` | LAME-encoded MP3 at 128 kbps. Requires AAC remux first. |
| `wav` | PCM 16-bit WAV. Requires AAC remux first. |

Final artifacts land under `${SCD_ARTIFACT_STORAGE_ROOT}/audio/final.<ext>`
unless `--output-path` or a different storage root is used.

## Troubleshooting

- `Network access must be enabled for track download.` — set
  `SCD_ALLOW_NETWORK=true` or pass `--allow-network`.
- `Filesystem writes must be enabled for track download.` — set
  `SCD_ALLOW_FILESYSTEM_WRITES=true` or pass `--allow-filesystem-writes`.
- `Download command is not configured.` — make sure
  `SCD_OAUTH_TOKEN_ENCRYPTION_KEY`, `SCD_SOUNDCLOUD_CLIENT_ID`, and
  `SCD_SOUNDCLOUD_CLIENT_SECRET` are set.
- `Track download failed.` after a successful OAuth setup — the most common
  causes are DRM/encrypted manifests (denied fail-closed), a missing or
  expired token profile, or a missing `ffmpeg` binary.

## Development

```bash
PYTHON=.venv/bin/python make check
PYTHON=.venv/bin/python scripts/check.sh
```

Both run `compileall`, the full pytest suite, `ruff check`, and `mypy`.
Tests use mocked HTTP transports and a fake ffmpeg runner; they do not make
real network calls and do not execute real ffmpeg.

## Quality gates

The repository requires:

- `python -m compileall src` to succeed.
- `pytest` to pass.
- `ruff check .` to be clean.
- `mypy src` to be clean.

`make check` and `scripts/check.sh` run all four.

## Roadmap

- Playlist batch workflow
- Local library indexing
- Resumable / retryable jobs
- Terminal UI
- Packaging and release automation
