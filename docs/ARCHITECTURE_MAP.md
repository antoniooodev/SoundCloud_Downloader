# Architecture Map

## Layers

- CLI: user-facing commands and option parsing.
- Application services: orchestration, policy decisions, ports, and workflow
  coordination.
- Domain models: typed values, errors, policy inputs, and artifact/media
  contracts.
- Infrastructure adapters: HTTP, SoundCloud API integration, OAuth stores,
  storage, media processing, and observability.

## CLI

- `src/soundcloud_downloader/cli/main.py`
- `src/soundcloud_downloader/cli/download.py`
- `src/soundcloud_downloader/cli/doctor.py`
- `src/soundcloud_downloader/cli/oauth.py`
- `src/soundcloud_downloader/cli/resolver.py`
- `src/soundcloud_downloader/cli/policy.py`
- `src/soundcloud_downloader/cli/reconstruction.py`

## Application services

- `src/soundcloud_downloader/application/track_download_workflow.py`
- `src/soundcloud_downloader/application/resolved_stream_analysis_workflow.py`
- `src/soundcloud_downloader/application/hls_segment_planner.py`
- `src/soundcloud_downloader/application/artifact_storage.py`
- `src/soundcloud_downloader/application/ffmpeg.py`
- `src/soundcloud_downloader/application/metadata_normalizer.py`
- `src/soundcloud_downloader/application/policy_service.py`
- `src/soundcloud_downloader/application/oauth_*`
- `src/soundcloud_downloader/application/ports/`

## Domain models

- `src/soundcloud_downloader/domain/artifact.py`
- `src/soundcloud_downloader/domain/download.py`
- `src/soundcloud_downloader/domain/hls_segments.py`
- `src/soundcloud_downloader/domain/hls_staging.py`
- `src/soundcloud_downloader/domain/hls_assembly.py`
- `src/soundcloud_downloader/domain/remux.py`
- `src/soundcloud_downloader/domain/audio_export.py`
- `src/soundcloud_downloader/domain/policy.py`
- `src/soundcloud_downloader/domain/reconstruction_policy.py`
- `src/soundcloud_downloader/domain/stream_analysis.py`
- `src/soundcloud_downloader/domain/transcoding.py`

## Infrastructure adapters

- `src/soundcloud_downloader/infrastructure/http/`
- `src/soundcloud_downloader/infrastructure/soundcloud/`
- `src/soundcloud_downloader/infrastructure/storage/`
- `src/soundcloud_downloader/infrastructure/media/`
- `src/soundcloud_downloader/infrastructure/oauth/`
- `src/soundcloud_downloader/infrastructure/observability/`

## Current download flow

```text
download track CLI
-> TrackDownloadWorkflow
-> resolver
-> metadata normalizer
-> transcoding endpoint resolver
-> HLS manifest service
-> stream analysis / policy
-> HLS segment planner
-> HLS segment fetcher
-> HLS media assembler
-> M4A remuxer / AudioExporter
-> ArtifactStoragePort
```

## Safety gates

- `allow_network` gates HTTP.
- `allow_filesystem_writes` gates storage and workspace writes.
- DRM and encrypted streams fail closed.
- `SecretStr`-backed values and sensitive URLs are redacted.
- `ffmpeg` runs only through `FFMPEGRunnerPort`.

## Test strategy

- Unit tests cover domain models, policy decisions, redaction, and adapters.
- CLI tests use `CliRunner` and avoid real network calls.
- HTTP and SoundCloud behavior use mock transports or fakes.
- Storage and workspace writes use temporary paths.
- `ffmpeg` behavior is tested through fake runners or controlled subprocess
  wrappers.
- End-to-end download coverage uses a mocked pipeline rather than real
  SoundCloud credentials.
