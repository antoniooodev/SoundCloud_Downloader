import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer

from soundcloud_downloader.application import (
    HLSSegmentPlanner,
    ResolvedStreamAnalysisWorkflow,
    SoundCloudMetadataNormalizer,
    TrackDownloadWorkflow,
    TrackDownloadWorkflowError,
)
from soundcloud_downloader.config import AppSettings, load_settings
from soundcloud_downloader.domain import (
    AccessMode,
    AudioExportFormat,
    ErrorCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthTokenProfileId,
    OutputProfile,
    SoundcloudDownloaderError,
    TrackDownloadRequest,
    TrackDownloadResult,
    redact_track_download_result,
)
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.media import (
    AudioExporter,
    M4ARemuxer,
    SubprocessFFMPEGRunner,
)
from soundcloud_downloader.infrastructure.oauth import (
    AutoRefreshingAccessTokenProvider,
    EncryptedOAuthTokenStore,
)
from soundcloud_downloader.infrastructure.observability import configure_logging
from soundcloud_downloader.infrastructure.soundcloud import (
    HLSMediaAssembler,
    HLSSegmentFetcher,
    OAuthRefreshTokenService,
    OfficialSoundCloudResolver,
    SoundCloudHLSManifestService,
    SoundCloudTranscodingEndpointService,
)
from soundcloud_downloader.infrastructure.storage import (
    LocalArtifactStorage,
    LocalTemporaryWorkspace,
)

download_app = typer.Typer(help="Download SoundCloud media artifacts.")


_FORMAT_TO_OUTPUT_PROFILE = {
    AudioExportFormat.M4A: OutputProfile.AAC_M4A,
    AudioExportFormat.MP3: OutputProfile.MP3_128,
    AudioExportFormat.WAV: OutputProfile.WAV_EXPORT,
}
_GENERIC_FAILURE_MESSAGE = "Track download failed."


@download_app.command("track")
def download_track(
    url: Annotated[str, typer.Argument(help="SoundCloud track URL.")],
    output_format: Annotated[
        AudioExportFormat,
        typer.Option("--format", help="Output audio format."),
    ] = AudioExportFormat.M4A,
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="OAuth token profile to use."),
    ] = "default",
    output_path: Annotated[
        str | None,
        typer.Option("--output-path", help="Final artifact relative path override."),
    ] = None,
    token_store_path: Annotated[
        Path | None,
        typer.Option("--token-store-path", help="Override OAuth token store path."),
    ] = None,
    artifact_storage_root: Annotated[
        Path | None,
        typer.Option("--artifact-storage-root", help="Override artifact storage root."),
    ] = None,
    artifact_temp_root: Annotated[
        Path | None,
        typer.Option("--artifact-temp-root", help="Override temp workspace root."),
    ] = None,
    access_mode: Annotated[
        AccessMode,
        typer.Option("--access-mode", help="Track access mode."),
    ] = AccessMode.PUBLIC,
    output_profile: Annotated[
        OutputProfile | None,
        typer.Option("--output-profile", help="Reconstruction output profile."),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    allow_network: Annotated[
        bool | None,
        typer.Option(
            "--allow-network/--no-allow-network",
            help="Override settings network gate for this command.",
        ),
    ] = None,
    allow_filesystem_writes: Annotated[
        bool | None,
        typer.Option(
            "--allow-filesystem-writes/--no-allow-filesystem-writes",
            help="Override settings filesystem write gate for this command.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or safe key/value lines."),
    ] = True,
) -> None:
    del output_path
    settings = _apply_overrides(
        load_settings(env_file=env_file),
        token_store_path=token_store_path,
        artifact_storage_root=artifact_storage_root,
        artifact_temp_root=artifact_temp_root,
        allow_network=allow_network,
        allow_filesystem_writes=allow_filesystem_writes,
    )
    _validate_settings(settings)
    configure_logging(settings)

    token_profile_id = OAuthTokenProfileId(value=profile_id)
    effective_output_profile = output_profile or _FORMAT_TO_OUTPUT_PROFILE[output_format]

    try:
        request = TrackDownloadRequest(
            source_url=url,
            output_format=output_format,
            access_mode=access_mode,
            output_profile=effective_output_profile,
        )
    except ValueError:
        typer.echo(_GENERIC_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from None

    try:
        result = asyncio.run(
            _download_track_async(
                settings=settings,
                request=request,
                profile_id=token_profile_id,
            )
        )
    except TrackDownloadWorkflowError:
        typer.echo(_GENERIC_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from None
    except SoundcloudDownloaderError:
        typer.echo(_GENERIC_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo(_GENERIC_FAILURE_MESSAGE, err=True)
        raise typer.Exit(code=1) from None

    payload = redact_track_download_result(result)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    _echo_plain(payload)


async def _download_track_async(
    *,
    settings: AppSettings,
    request: TrackDownloadRequest,
    profile_id: OAuthTokenProfileId,
) -> TrackDownloadResult:
    async with build_safe_http_client(settings) as http_client:
        workflow = build_track_download_workflow(
            settings,
            profile_id=profile_id,
            http_client=http_client,
        )
        return await workflow.download_track(request)


def build_safe_http_client(settings: AppSettings) -> SafeAsyncHttpClient:
    return SafeAsyncHttpClient(settings=settings)


def build_ffmpeg_runner(settings: AppSettings) -> SubprocessFFMPEGRunner:
    return SubprocessFFMPEGRunner(settings)


def build_track_download_workflow(
    settings: AppSettings,
    *,
    profile_id: OAuthTokenProfileId,
    http_client: SafeAsyncHttpClient | None = None,
) -> TrackDownloadWorkflow:
    if http_client is None:
        http_client = SafeAsyncHttpClient(settings=settings)
    token_store = EncryptedOAuthTokenStore(settings)
    refresh_service = OAuthRefreshTokenService(settings=settings, http_client=http_client)
    client_id, client_secret = _oauth_client_credentials(settings)
    token_provider = AutoRefreshingAccessTokenProvider(
        token_store=token_store,
        refresh_service=refresh_service,
        client_id=client_id,
        client_secret=client_secret,
        profile_id=profile_id,
    )
    resolver = OfficialSoundCloudResolver(
        settings=settings,
        http_client=http_client,
        token_provider=token_provider,
    )
    transcoding_endpoint_service = SoundCloudTranscodingEndpointService(http_client=http_client)
    manifest_service = SoundCloudHLSManifestService(http_client=http_client)
    stream_analysis_workflow = ResolvedStreamAnalysisWorkflow(manifest_fetcher=manifest_service)
    storage = LocalArtifactStorage(settings)
    workspace = LocalTemporaryWorkspace(settings)
    segment_fetcher = HLSSegmentFetcher(http_client=http_client, storage=storage)
    media_assembler = HLSMediaAssembler(storage=storage)
    ffmpeg_runner = build_ffmpeg_runner(settings)
    m4a_remuxer = M4ARemuxer(
        settings=settings,
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=ffmpeg_runner,
    )
    audio_exporter = AudioExporter(
        settings=settings,
        storage=storage,
        workspace=workspace,
        ffmpeg_runner=ffmpeg_runner,
    )
    return TrackDownloadWorkflow(
        resolver=resolver,
        access_token_provider=token_provider,
        metadata_normalizer=SoundCloudMetadataNormalizer(),
        transcoding_endpoint_resolver=transcoding_endpoint_service,
        stream_analysis_workflow=stream_analysis_workflow,
        hls_segment_planner=HLSSegmentPlanner(),
        hls_segment_fetcher=segment_fetcher,
        hls_media_assembler=media_assembler,
        m4a_remuxer=m4a_remuxer,
        audio_exporter=audio_exporter,
    )


def _apply_overrides(
    settings: AppSettings,
    *,
    token_store_path: Path | None,
    artifact_storage_root: Path | None,
    artifact_temp_root: Path | None,
    allow_network: bool | None,
    allow_filesystem_writes: bool | None,
) -> AppSettings:
    updates: dict[str, object] = {}
    if token_store_path is not None:
        updates["oauth_token_store_path"] = token_store_path
    if artifact_storage_root is not None:
        updates["artifact_storage_root"] = artifact_storage_root
    if artifact_temp_root is not None:
        updates["artifact_temp_root"] = artifact_temp_root
    if allow_network is not None:
        updates["allow_network"] = allow_network
    if allow_filesystem_writes is not None:
        updates["allow_filesystem_writes"] = allow_filesystem_writes
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _validate_settings(settings: AppSettings) -> None:
    if not settings.allow_network:
        typer.echo("Network access must be enabled for track download.", err=True)
        raise typer.Exit(code=1)
    if not settings.allow_filesystem_writes:
        typer.echo("Filesystem writes must be enabled for track download.", err=True)
        raise typer.Exit(code=1)
    if settings.oauth_token_encryption_key is None:
        typer.echo("Download command is not configured.", err=True)
        raise typer.Exit(code=1)
    if settings.soundcloud_client_id is None:
        typer.echo("Download command is not configured.", err=True)
        raise typer.Exit(code=1)
    if settings.soundcloud_client_secret is None:
        typer.echo("Download command is not configured.", err=True)
        raise typer.Exit(code=1)


def _oauth_client_credentials(
    settings: AppSettings,
) -> tuple[OAuthClientId, OAuthClientSecret]:
    client_id = settings.soundcloud_client_id
    client_secret = settings.soundcloud_client_secret
    if client_id is None or client_secret is None:
        raise SoundcloudDownloaderError(
            ErrorCode.AUTH_REQUIRED,
            "Download command is not configured.",
        )
    return (
        OAuthClientId(value=client_id),
        OAuthClientSecret(value=client_secret),
    )


def _echo_plain(payload: dict[str, object]) -> None:
    track = payload.get("track", {})
    output = payload.get("output", {})
    segments = payload.get("segments", {})
    assert isinstance(track, dict)
    assert isinstance(output, dict)
    assert isinstance(segments, dict)
    typer.echo(f"status={payload.get('status', '')}")
    typer.echo(f"track_id={track.get('id', '') or ''}")
    typer.echo(f"title={track.get('title', '') or ''}")
    typer.echo(f"output_format={output.get('format', '') or ''}")
    typer.echo(f"relative_path={output.get('relative_path', '') or ''}")
    size_bytes = output.get("size_bytes")
    typer.echo(f"size_bytes={'' if size_bytes is None else size_bytes}")
    typer.echo(f"checksum={output.get('checksum', '') or ''}")
    segment_count = segments.get("count")
    typer.echo(f"segment_count={'' if segment_count is None else segment_count}")
