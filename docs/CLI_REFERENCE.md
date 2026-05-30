# CLI Reference

## Global commands

```bash
soundcloud-downloader --help
soundcloud-downloader --version
soundcloud-downloader version
```

Use `--help` on any command or command group to inspect the full current option
set.

## Doctor

`doctor` checks local configuration without creating artifact directories or
executing media conversion.

```bash
soundcloud-downloader doctor --env-file .env
soundcloud-downloader doctor --env-file .env --plain
soundcloud-downloader doctor --env-file .env --no-check-ffmpeg
soundcloud-downloader doctor --env-file .env --no-check-paths
```

Common options:

- `--env-file PATH`
- `--json` / `--plain`
- `--check-ffmpeg` / `--no-check-ffmpeg`
- `--check-paths` / `--no-check-paths`

## OAuth

OAuth commands manage PKCE authorization sessions and encrypted local token
state. Use `soundcloud-downloader oauth COMMAND --help` for the full option set.

```bash
soundcloud-downloader oauth authorize-url \
  --env-file .env \
  --client-id "<CLIENT_ID>" \
  --redirect-uri "https://your-app.example/callback"
```

```bash
soundcloud-downloader oauth create-session \
  --env-file .env \
  --client-id "<CLIENT_ID>" \
  --redirect-uri "https://your-app.example/callback" \
  --persist \
  --allow-filesystem-writes
```

```bash
soundcloud-downloader oauth exchange-code \
  --env-file .env \
  --session-id "<SESSION_ID>" \
  --code "<AUTHORIZATION_CODE>" \
  --state "<STATE>" \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes
```

```bash
soundcloud-downloader oauth token-status --env-file .env --profile-id default
soundcloud-downloader oauth token-status --env-file .env --profile-id default --plain
```

```bash
soundcloud-downloader oauth logout \
  --env-file .env \
  --profile-id default \
  --allow-filesystem-writes
```

Common options include `--env-file`, `--profile-id`, `--token-store-path`,
`--store-path`, `--session-store-path`, `--json`, and `--plain`, depending on
the subcommand.

## Resolver

`resolver inspect` normalizes inputs offline by default. It can also use the
configured external resolver or the authenticated official resolver when the
appropriate gates and credentials are configured.

```bash
soundcloud-downloader resolver inspect "https://soundcloud.com/example/track"
```

```bash
soundcloud-downloader resolver inspect "https://soundcloud.com/example/track" \
  --official \
  --env-file .env \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes
```

```bash
soundcloud-downloader resolver inspect "https://soundcloud.com/example/track" \
  --external \
  --env-file .env \
  --resolve-endpoint "https://example.invalid/resolve" \
  --allow-network
```

Common options:

- `--external` / `--offline`
- `--official`
- `--profile-id TEXT`
- `--token-store-path PATH`
- `--env-file PATH`
- `--allow-network` / `--no-allow-network`
- `--allow-filesystem-writes` / `--no-allow-filesystem-writes`
- `--resolve-endpoint TEXT`

## Reconstruction planning

`plan evaluate` builds a local reconstruction plan from explicit input facts.
`policy evaluate` evaluates the policy layer directly. These commands are local
planning and inspection helpers.

```bash
soundcloud-downloader plan evaluate \
  --access-mode public \
  --source-protocol hls \
  --requested-profile aac_m4a \
  --authenticated \
  --track-public \
  --source-downloadable
```

```bash
soundcloud-downloader policy evaluate \
  --access-mode public \
  --requested-profile aac_m4a \
  --source-present \
  --source-protocol hls \
  --source-downloadable \
  --track-public
```

Use `soundcloud-downloader plan evaluate --help` and
`soundcloud-downloader policy evaluate --help` for the full set of policy,
source, entitlement, and manifest options.

## Download track

`download track` downloads one authorized SoundCloud track when network access,
filesystem writes, OAuth credentials, token storage, policy, and media handling
all allow it.

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --env-file .env \
  --format m4a \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes
```

Common options:

- `--format m4a`
- `--format mp3`
- `--format wav`
- `--profile-id TEXT`
- `--output-path TEXT`
- `--token-store-path PATH`
- `--artifact-storage-root PATH`
- `--artifact-temp-root PATH`
- `--access-mode public`
- `--access-mode go_plus`
- `--output-profile original`
- `--output-profile mp3_128`
- `--output-profile aac_m4a`
- `--output-profile wav_export`
- `--env-file PATH`
- `--allow-network` / `--no-allow-network`
- `--allow-filesystem-writes` / `--no-allow-filesystem-writes`
- `--json` / `--plain`

## Output formats

Supported `--format` values for `download track` are:

- `m4a`
- `mp3`
- `wav`

`m4a` uses the AAC/M4A remux path. `mp3` and `wav` use export paths. Real media
processing requires `ffmpeg` to be installed locally.

## Environment files

Most commands that need settings accept:

```bash
--env-file .env
```

The `.env` file should use `SCD_` settings such as:

- `SCD_ALLOW_NETWORK`
- `SCD_ALLOW_FILESYSTEM_WRITES`
- `SCD_SOUNDCLOUD_CLIENT_ID`
- `SCD_SOUNDCLOUD_CLIENT_SECRET`
- `SCD_OAUTH_SESSION_ENCRYPTION_KEY`
- `SCD_OAUTH_TOKEN_ENCRYPTION_KEY`
- `SCD_OAUTH_SESSION_STORE_PATH`
- `SCD_OAUTH_TOKEN_STORE_PATH`
- `SCD_ARTIFACT_STORAGE_ROOT`
- `SCD_ARTIFACT_TEMP_ROOT`
- `SCD_FFMPEG_BINARY`
- `SCD_FFMPEG_TIMEOUT_SECONDS`

See `.env.example` for placeholders.

## Safety notes

- Do not pass raw tokens on the command line.
- Use encrypted OAuth session and token stores.
- Use only authorized accounts and tracks.
- DRM and encrypted streams are denied fail-closed.
- The CLI does not bypass DRM or decrypt protected streams.
- CLI output is designed to avoid exposing tokens, stream URLs, manifest URLs,
  and segment URLs.

## Common examples

```bash
soundcloud-downloader --version
soundcloud-downloader doctor --env-file .env
soundcloud-downloader doctor --env-file .env --plain
```

```bash
soundcloud-downloader oauth token-status --env-file .env --profile-id default
```

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --env-file .env \
  --format m4a \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes
```

```bash
soundcloud-downloader download track "https://soundcloud.com/example/track" \
  --env-file .env \
  --format mp3 \
  --profile-id default \
  --allow-network \
  --allow-filesystem-writes \
  --plain
```
