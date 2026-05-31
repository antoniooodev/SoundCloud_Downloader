import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from typer.testing import CliRunner

from soundcloud_downloader.application import (
    PolicyEvaluationResponse,
    ReconstructionPlan,
    ResolvedStreamAnalysisResult,
    TrackDownloadFailureReason,
    TrackDownloadFailureStage,
    TrackDownloadWorkflow,
    TrackDownloadWorkflowError,
)
from soundcloud_downloader.cli import download as download_cli
from soundcloud_downloader.cli.main import app
from soundcloud_downloader.domain import (
    ArtifactChecksum,
    ArtifactFormat,
    ArtifactId,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactRelativePath,
    AudioExportFormat,
    DRMStatus,
    ErrorCode,
    HLSMediaAssemblyResult,
    HLSSegmentPlan,
    HLSSegmentReference,
    HLSSegmentStagingResult,
    HLSSegmentUrl,
    MediaCodec,
    MediaContainer,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    SoundCloudResolvedStreamUrl,
    SoundCloudResourceId,
    SoundCloudTrackMetadata,
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
    SoundCloudUserMetadata,
    SoundcloudDownloaderError,
    SourceProtocol,
    StagedHLSSegment,
    TrackDownloadRequest,
    TrackDownloadResult,
    TrackDownloadStatus,
)


CLIENT_ID = "dummy-client-id"
CLIENT_SECRET = "dummy-client-secret"
RAW_ACCESS = "raw-access-token-value"
RAW_REFRESH = "raw-refresh-token-value"
RAW_MANIFEST_URL = "https://media.soundcloud.test/playlist.m3u8?Policy=manifest-policy"
RAW_SEGMENT_URL = "https://media.soundcloud.test/segment0.ts?Policy=segment-policy"
RAW_TRANSCODING_URL = "https://api.soundcloud.test/tracks/123/transcodings/hls"
RAW_MANIFEST_TEXT = "#EXTM3U\n#EXT-X-ENDLIST\n"
SHA256 = "a" * 64
TRACK_URL = "https://soundcloud.com/artist/example-track"


def write_env_file(
    tmp_path: Path,
    *,
    key: str | None,
    token_store_path: Path,
    artifact_storage_root: Path | None = None,
    artifact_temp_root: Path | None = None,
    allow_network: bool = True,
    allow_filesystem_writes: bool = True,
    client_id: str | None = CLIENT_ID,
    client_secret: str | None = CLIENT_SECRET,
) -> Path:
    lines = [
        f"SCD_ALLOW_NETWORK={str(allow_network).lower()}",
        f"SCD_ALLOW_FILESYSTEM_WRITES={str(allow_filesystem_writes).lower()}",
        f"SCD_OAUTH_TOKEN_STORE_PATH={token_store_path}",
        "SCD_SOUNDCLOUD_API_BASE_URL=https://api.soundcloud.test",
        "SCD_SOUNDCLOUD_AUTH_BASE_URL=https://auth.soundcloud.test",
    ]
    if key is not None:
        lines.append(f"SCD_OAUTH_TOKEN_ENCRYPTION_KEY={key}")
    if client_id is not None:
        lines.append(f"SCD_SOUNDCLOUD_CLIENT_ID={client_id}")
    if client_secret is not None:
        lines.append(f"SCD_SOUNDCLOUD_CLIENT_SECRET={client_secret}")
    if artifact_storage_root is not None:
        lines.append(f"SCD_ARTIFACT_STORAGE_ROOT={artifact_storage_root}")
    if artifact_temp_root is not None:
        lines.append(f"SCD_ARTIFACT_TEMP_ROOT={artifact_temp_root}")
    env_file = tmp_path / "settings.env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return env_file


def base_env_file(tmp_path: Path, **overrides: object) -> Path:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    kwargs: dict[str, object] = {
        "key": key,
        "token_store_path": token_store_path,
    }
    kwargs.update(overrides)
    return write_env_file(tmp_path, **kwargs)  # type: ignore[arg-type]


def fake_track_result(
    *,
    output_format: AudioExportFormat = AudioExportFormat.M4A,
    title: str = "Example Track",
    track_id: str = "track-1",
    username: str = "artist",
) -> TrackDownloadResult:
    final_artifact = _artifact(
        artifact_id=f"final-{output_format.value}",
        kind=ArtifactKind.FINAL_AUDIO,
        format=_artifact_format_for(output_format),
        relative_path=f"audio/final.{output_format.value}",
        size_bytes=12,
    )
    return TrackDownloadResult(
        status=TrackDownloadStatus.SUCCEEDED,
        metadata=SoundCloudTrackMetadata(
            id=SoundCloudResourceId(value=track_id),
            title=title,
            user=SoundCloudUserMetadata(
                id=SoundCloudResourceId(value="user-1"),
                username=username,
            ),
        ),
        selected_transcoding=_transcoding(),
        stream_analysis=_stream_analysis(),
        segment_plan=_segment_plan(),
        staging_result=_staging_result(),
        assembly_result=_assembly_result(),
        final_artifact=final_artifact,
        output_format=output_format,
    )


class FakeWorkflow:
    def __init__(
        self,
        *,
        result: TrackDownloadResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.requests: list[TrackDownloadRequest] = []

    async def download_track(self, request: TrackDownloadRequest) -> TrackDownloadResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def install_fake_workflow(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeWorkflow,
    *,
    profile_ids: list[str] | None = None,
) -> None:
    def factory(
        settings: object,
        *,
        profile_id: object,
        http_client: object | None = None,
    ) -> FakeWorkflow:
        del settings, http_client
        if profile_ids is not None:
            profile_ids.append(profile_id.value)  # type: ignore[union-attr]
        return fake

    monkeypatch.setattr(download_cli, "build_track_download_workflow", factory)


def invoke_download(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["download", "track", *args])
    return result.exit_code, result.output


def test_download_track_command_exists() -> None:
    result = CliRunner().invoke(app, ["download", "track", "--help"])
    assert result.exit_code == 0
    assert "SoundCloud track URL." in result.output


def test_command_exits_nonzero_when_network_is_disabled(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path, allow_network=False)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Network access must be enabled for track download." in output


def test_command_exits_nonzero_when_filesystem_writes_disabled(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path, allow_filesystem_writes=False)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Filesystem writes must be enabled for track download." in output


def test_command_exits_nonzero_when_token_encryption_key_is_missing(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path, key=None)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Download command is not configured." in output


def test_command_exits_nonzero_when_client_id_is_missing(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path, client_id=None)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Download command is not configured." in output


def test_command_exits_nonzero_when_client_secret_is_missing(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path, client_secret=None)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Download command is not configured." in output


@pytest.mark.parametrize("fmt", ["m4a", "mp3", "wav"])
def test_command_accepts_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fmt: str,
) -> None:
    env_file = base_env_file(tmp_path)
    fake = FakeWorkflow(result=fake_track_result(output_format=AudioExportFormat(fmt)))
    install_fake_workflow(monkeypatch, fake)

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--format",
        fmt,
    )

    assert exit_code == 0, output
    assert fake.requests[0].output_format is AudioExportFormat(fmt)


def test_invalid_format_exits_nonzero(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path)

    exit_code, _output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--format",
        "flac",
    )

    assert exit_code != 0


def test_command_builds_track_download_request_with_source_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    fake = FakeWorkflow(result=fake_track_result())
    install_fake_workflow(monkeypatch, fake)

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code == 0, output
    assert fake.requests[0].source_url == TRACK_URL


@pytest.mark.parametrize(
    "fmt,export_format",
    [
        ("m4a", AudioExportFormat.M4A),
        ("mp3", AudioExportFormat.MP3),
        ("wav", AudioExportFormat.WAV),
    ],
)
def test_format_maps_to_audio_export_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fmt: str,
    export_format: AudioExportFormat,
) -> None:
    env_file = base_env_file(tmp_path)
    fake = FakeWorkflow(result=fake_track_result(output_format=export_format))
    install_fake_workflow(monkeypatch, fake)

    exit_code, _output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--format",
        fmt,
    )

    assert exit_code == 0
    assert fake.requests[0].output_format is export_format


def test_command_passes_profile_id_to_workflow_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    fake = FakeWorkflow(result=fake_track_result())
    seen_profile_ids: list[str] = []
    install_fake_workflow(monkeypatch, fake, profile_ids=seen_profile_ids)

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--profile-id",
        "custom-profile",
    )

    assert exit_code == 0, output
    assert seen_profile_ids == ["custom-profile"]


def test_token_store_path_override_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    override_path = tmp_path / "override_tokens.enc"
    fake = FakeWorkflow(result=fake_track_result())
    captured: dict[str, object] = {}

    def factory(
        settings: object,
        *,
        profile_id: object,
        http_client: object | None = None,
    ) -> FakeWorkflow:
        del http_client, profile_id
        captured["oauth_token_store_path"] = settings.oauth_token_store_path  # type: ignore[union-attr]
        return fake

    monkeypatch.setattr(download_cli, "build_track_download_workflow", factory)

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--token-store-path",
        str(override_path),
    )

    assert exit_code == 0, output
    assert captured["oauth_token_store_path"] == override_path


def test_artifact_storage_root_override_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    override_root = tmp_path / "artifacts"
    fake = FakeWorkflow(result=fake_track_result())
    captured: dict[str, object] = {}

    def factory(
        settings: object,
        *,
        profile_id: object,
        http_client: object | None = None,
    ) -> FakeWorkflow:
        del http_client, profile_id
        captured["artifact_storage_root"] = settings.artifact_storage_root  # type: ignore[union-attr]
        return fake

    monkeypatch.setattr(download_cli, "build_track_download_workflow", factory)

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--artifact-storage-root",
        str(override_root),
    )

    assert exit_code == 0, output
    assert captured["artifact_storage_root"] == override_root


def test_artifact_temp_root_override_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    override_root = tmp_path / "tmp"
    fake = FakeWorkflow(result=fake_track_result())
    captured: dict[str, object] = {}

    def factory(
        settings: object,
        *,
        profile_id: object,
        http_client: object | None = None,
    ) -> FakeWorkflow:
        del http_client, profile_id
        captured["artifact_temp_root"] = settings.artifact_temp_root  # type: ignore[union-attr]
        return fake

    monkeypatch.setattr(download_cli, "build_track_download_workflow", factory)

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--artifact-temp-root",
        str(override_root),
    )

    assert exit_code == 0, output
    assert captured["artifact_temp_root"] == override_root


def test_successful_json_output_contains_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["status"] == "succeeded"


def test_successful_json_output_contains_output_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["output"]["format"] == "m4a"
    assert payload["output"]["relative_path"] == "audio/final.m4a"
    assert payload["output"]["size_bytes"] == 12
    assert payload["output"]["checksum"] == SHA256


def test_successful_json_output_contains_segment_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert payload["segments"]["count"] == 1


def test_successful_plain_output_contains_safe_key_value_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    exit_code, output = invoke_download(
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--plain",
    )

    assert exit_code == 0, output
    assert "status=succeeded" in output
    assert "track_id=track-1" in output
    assert "title=Example Track" in output
    assert "output_format=m4a" in output
    assert "relative_path=audio/final.m4a" in output
    assert "size_bytes=12" in output
    assert f"checksum={SHA256}" in output
    assert "segment_count=1" in output


def test_json_output_does_not_contain_sensitive_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code == 0, output
    for forbidden in (
        RAW_ACCESS,
        "access_token",
        RAW_REFRESH,
        "refresh_token",
        CLIENT_SECRET,
        "client_secret",
        RAW_MANIFEST_URL,
        "manifest_url",
        RAW_SEGMENT_URL,
        "segment_url",
        RAW_TRANSCODING_URL,
        "transcoding_endpoint",
    ):
        assert forbidden not in output


def test_workflow_failure_exits_nonzero_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(
        monkeypatch,
        FakeWorkflow(
            error=TrackDownloadWorkflowError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Track download workflow failed.",
            )
        ),
    )

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert "stage=unknown" in output
    assert "reason=unknown" in output
    for forbidden in (
        RAW_ACCESS,
        RAW_REFRESH,
        CLIENT_SECRET,
        RAW_MANIFEST_TEXT,
        RAW_SEGMENT_URL,
    ):
        assert forbidden not in output


def test_policy_denied_workflow_failure_exits_nonzero_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(
        monkeypatch,
        FakeWorkflow(
            error=TrackDownloadWorkflowError(
                ErrorCode.DRM_UNSUPPORTED,
                "Track reconstruction was denied by policy.",
            )
        ),
    )

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Track download failed." in output


def test_workflow_failure_prints_safe_stage_and_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(
        monkeypatch,
        FakeWorkflow(
            error=TrackDownloadWorkflowError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Track download workflow failed.",
                stage=TrackDownloadFailureStage.RESOLVER,
                reason=TrackDownloadFailureReason.OFFICIAL_RESOLVER_PAYLOAD_INVALID,
            )
        ),
    )

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert "stage=resolver" in output
    assert "reason=official_resolver_payload_invalid" in output


def test_unexpected_error_exits_nonzero_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(
        monkeypatch,
        FakeWorkflow(
            error=SoundcloudDownloaderError(ErrorCode.AUTH_REQUIRED, "oauth not configured"),
        ),
    )

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert RAW_ACCESS not in output


def test_error_output_does_not_contain_sensitive_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(
        monkeypatch,
        FakeWorkflow(
            error=TrackDownloadWorkflowError(
                ErrorCode.UNKNOWN_UNSAFE,
                f"failed with manifest text {RAW_MANIFEST_TEXT}",
            )
        ),
    )

    exit_code, output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code != 0
    for forbidden in (
        RAW_ACCESS,
        RAW_REFRESH,
        CLIENT_SECRET,
        RAW_MANIFEST_TEXT,
        RAW_MANIFEST_URL,
        RAW_SEGMENT_URL,
    ):
        assert forbidden not in output


def test_no_real_network_calls_occur(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    exit_code, _output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code == 0


def test_no_real_ffmpeg_execution_occurs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import subprocess

    env_file = base_env_file(tmp_path)
    install_fake_workflow(monkeypatch, FakeWorkflow(result=fake_track_result()))

    def fail_subprocess(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real subprocess calls are not allowed")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    monkeypatch.setattr(subprocess, "Popen", fail_subprocess)

    exit_code, _output = invoke_download(TRACK_URL, "--env-file", str(env_file))

    assert exit_code == 0


def test_tests_write_only_inside_tmp_path(tmp_path: Path) -> None:
    env_file = base_env_file(tmp_path)

    assert env_file.is_relative_to(tmp_path)


def test_build_track_download_workflow_produces_real_workflow(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    env_file = write_env_file(tmp_path, key=key, token_store_path=tmp_path / "tokens.enc")
    from soundcloud_downloader.config import load_settings

    settings = load_settings(env_file=env_file)

    from soundcloud_downloader.domain import OAuthTokenProfileId

    workflow = download_cli.build_track_download_workflow(
        settings,
        profile_id=OAuthTokenProfileId(value="default"),
    )

    assert isinstance(workflow, TrackDownloadWorkflow)


def _artifact_format_for(fmt: AudioExportFormat) -> ArtifactFormat:
    if fmt is AudioExportFormat.M4A:
        return ArtifactFormat.M4A
    if fmt is AudioExportFormat.MP3:
        return ArtifactFormat.MP3
    return ArtifactFormat.WAV


def _artifact(
    *,
    artifact_id: str,
    kind: ArtifactKind,
    format: ArtifactFormat,
    relative_path: str,
    size_bytes: int,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=ArtifactId(value=artifact_id),
        kind=kind,
        format=format,
        relative_path=ArtifactRelativePath(value=relative_path),
        size_bytes=size_bytes,
        checksum=ArtifactChecksum(value=SHA256),
        created_at=datetime(2026, 5, 30, tzinfo=UTC),
    )


def _transcoding() -> SoundCloudTranscodingMetadata:
    return SoundCloudTranscodingMetadata(
        preset="aac_0_1",
        quality="sq",
        snipped=False,
        format=SoundCloudTranscodingFormat(
            protocol=SoundCloudTranscodingProtocol.HLS,
            mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        ),
        endpoint_url=SoundCloudTranscodingEndpointUrl(value=SecretStr(RAW_TRANSCODING_URL)),
    )


def _stream_analysis() -> ResolvedStreamAnalysisResult:
    from soundcloud_downloader.domain import (
        SoundCloudResolvedStream,
        SoundCloudResolvedStreamKind,
    )

    stream = SoundCloudResolvedStream(
        kind=SoundCloudResolvedStreamKind.HLS_MANIFEST,
        url=SoundCloudResolvedStreamUrl(value=SecretStr(RAW_MANIFEST_URL)),
        protocol=SoundCloudTranscodingProtocol.HLS,
        mime_type=SoundCloudTranscodingMimeType.AUDIO_MP4,
        preset="aac_0_1",
        quality="sq",
        snipped=False,
    )
    return ResolvedStreamAnalysisResult(
        stream=stream,
        manifest_analysis=None,
        plan=ReconstructionPlan(
            source=MediaSource(
                protocol=SourceProtocol.HLS,
                mime_type="audio/mp4",
                codec=MediaCodec.AAC,
                container=MediaContainer.M4A,
                drm_status=DRMStatus.NONE,
            ),
            effective_drm_status=DRMStatus.NONE,
            policy=PolicyEvaluationResponse(
                decision=OfflineDecision.ALLOW_AAC_M4A_REMUX,
                allowed=True,
                reason="allowed",
                error_code=None,
                output_profile=OutputProfile.AAC_M4A,
            ),
        ),
        manifest_text=SecretStr(RAW_MANIFEST_TEXT),
    )


def _segment_plan() -> HLSSegmentPlan:
    return HLSSegmentPlan(
        manifest_url=SoundCloudResolvedStreamUrl(value=SecretStr(RAW_MANIFEST_URL)),
        segments=(
            HLSSegmentReference(
                index=0,
                url=HLSSegmentUrl(value=SecretStr(RAW_SEGMENT_URL)),
                duration_seconds=10.0,
            ),
        ),
    )


def _staging_result() -> HLSSegmentStagingResult:
    return HLSSegmentStagingResult(
        manifest_url=SoundCloudResolvedStreamUrl(value=SecretStr(RAW_MANIFEST_URL)),
        segments=(
            StagedHLSSegment(
                index=0,
                artifact=_artifact(
                    artifact_id="segment-0",
                    kind=ArtifactKind.HLS_SEGMENT,
                    format=ArtifactFormat.AAC,
                    relative_path="segments/0.aac",
                    size_bytes=7,
                ),
                duration_seconds=10.0,
            ),
        ),
        total_bytes=7,
    )


def _assembly_result() -> HLSMediaAssemblyResult:
    return HLSMediaAssemblyResult(
        artifact=_artifact(
            artifact_id="assembled-media",
            kind=ArtifactKind.STAGED_MEDIA,
            format=ArtifactFormat.AAC,
            relative_path="assembled/media.aac",
            size_bytes=7,
        ),
        source_segment_count=1,
        total_duration_seconds=10.0,
        total_bytes=7,
    )
