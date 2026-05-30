# Known Limitations

## MVP scope

- The MVP supports single-track downloads only.
- Playlist batch download is not implemented yet.
- Resumable jobs are not implemented yet.
- A concurrent downloader manager is not implemented yet.
- A local library database or index is not implemented yet.
- A terminal UI is not implemented yet.

## Unsupported content

- DRM-protected and encrypted streams are denied fail-closed.
- Protected-stream decryption is not supported.
- License-server interaction is not supported.
- Preview-only and rights-restricted sources are expected to fail safely when
  policy denies them.

## Authentication

- Real downloads require authorized account access and valid SoundCloud
  credentials.
- Token scraping, browser cookie extraction, stolen credentials, and leaked
  credentials are not supported.
- OAuth setup is limited to the documented PKCE helper flow and encrypted local
  stores.

## Downloads

- The CLI currently handles one authorized track at a time.
- The project does not provide playlist queueing, batch retries, or background
  jobs.
- Network access and filesystem writes must be explicitly enabled through
  settings or command options.

## Media processing

- `ffmpeg` must be installed locally for real remuxing and export work.
- M4A remux, MP3 export, and WAV export depend on local `ffmpeg` execution.
- Protected media is not decrypted, reconstructed through bypasses, or sent to
  license-server workflows.

## Reliability

- Tests mock network and `ffmpeg` behavior where appropriate.
- Real-world SoundCloud availability, account entitlements, and source formats
  can still cause safe denials.
- There is no persistent job state for interrupted downloads.

## Planned improvements

- Playlist handling after explicit design and policy approval.
- Resumable job state and retry orchestration.
- Concurrent download management with bounded resource controls.
- Local library indexing and artifact browsing.
- More operator-facing diagnostics around safe denial categories.
