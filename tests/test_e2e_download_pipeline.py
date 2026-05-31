import json
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from typer.testing import CliRunner

from soundcloud_downloader.application.ffmpeg import FFMPEGCommand, FFMPEGResult
from soundcloud_downloader.cli import download as download_cli
from soundcloud_downloader.cli.main import app
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    OAuthAccessToken,
    OAuthRefreshToken,
    OAuthTokenProfileId,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient
from soundcloud_downloader.infrastructure.oauth import EncryptedOAuthTokenStore


RAW_ACCESS = "raw-access-token-should-not-leak"
RAW_REFRESH = "raw-refresh-token-should-not-leak"
REFRESHED_ACCESS = "raw-refreshed-access-token-should-not-leak"
REFRESHED_REFRESH = "raw-refreshed-refresh-token-should-not-leak"
CLIENT_ID = "dummy-client-id"
CLIENT_SECRET = "raw-client-secret-should-not-leak"
TRACK_URL = "https://soundcloud.com/artist/example-track"
NORMALIZED_TRACK_URL = "https://soundcloud.com/artist/example-track"
RESOLVE_HOST = "api.soundcloud.test"
AUTH_HOST = "auth.soundcloud.test"
MEDIA_HOST = "cdn.example.test"
TRANSCODING_URL = f"https://{RESOLVE_HOST}/tracks/123/transcodings/hls"
REAL_LIKE_TRANSCODING_URL = (
    f"https://{RESOLVE_HOST}/media/soundcloud:tracks:123/abc/stream/hls"
    "?client_secret=SHOULD_NOT_LEAK"
)
STREAM_URL = f"https://{MEDIA_HOST}/manifest.m3u8"
DIRECT_HLS_STREAM_URL = (
    "https://playback.media-streaming.soundcloud.cloud/track/aac_160k/uuid/playlist.m3u8"
    "?client_secret=SHOULD_NOT_LEAK"
)
RAW_RESOLVER_STREAM_URL = (
    "https://api.soundcloud.test/tracks/123/stream?client_secret=SHOULD_NOT_LEAK"
)
SEGMENT_URLS = (
    f"https://{MEDIA_HOST}/segments/seg0.ts",
    f"https://{MEDIA_HOST}/segments/seg1.ts",
    f"https://{MEDIA_HOST}/segments/seg2.ts",
)
SEGMENT_BYTES = (b"seg-zero-bytes", b"seg-one-bytes", b"seg-two-bytes")


def media_manifest(segment_urls: tuple[str, ...]) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:10",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for url in segment_urls:
        lines.append("#EXTINF:10.0,")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def encrypted_media_manifest(segment_urls: tuple[str, ...], *, key_method: str = "AES-128") -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:10",
        f'#EXT-X-KEY:METHOD={key_method},URI="https://license.example.test/key.bin"',
    ]
    for url in segment_urls:
        lines.append("#EXTINF:10.0,")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def session_key_manifest(segment_urls: tuple[str, ...]) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:10",
        '#EXT-X-SESSION-KEY:METHOD=AES-128,URI="https://license.example.test/session.bin"',
    ]
    for url in segment_urls:
        lines.append("#EXTINF:10.0,")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def fairplay_manifest(segment_urls: tuple[str, ...]) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:10",
        (
            '#EXT-X-KEY:METHOD=SAMPLE-AES,'
            'KEYFORMAT="com.apple.streamingkeydelivery",'
            'URI="https://fairplay.example.test/license"'
        ),
    ]
    for url in segment_urls:
        lines.append("#EXTINF:10.0,")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def master_playlist() -> str:
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=128000,CODECS=\"mp4a.40.2\"\n"
        f"https://{MEDIA_HOST}/variant.m3u8\n"
    )


def official_track_payload(
    transcoding_url: str = TRANSCODING_URL,
    *,
    stream_url: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "track",
        "id": 123,
        "title": "Example Track",
        "duration": 30_000,
        "permalink_url": TRACK_URL,
        "sharing": "public",
        "downloadable": False,
        "user": {"id": 99, "username": "artist"},
        "media": {
            "transcodings": [
                {
                    "url": transcoding_url,
                    "preset": "aac_0_1",
                    "snipped": False,
                    "format": {
                        "protocol": "hls",
                        "mime_type": "audio/mp4",
                    },
                }
            ]
        },
    }
    if stream_url is not None:
        payload["stream_url"] = stream_url
    return payload


def official_track_payload_without_media() -> dict[str, object]:
    payload = official_track_payload()
    payload["urn"] = "soundcloud:tracks:123"
    payload.pop("media")
    return payload


def real_like_official_track_payload(transcoding_url: str = TRANSCODING_URL) -> dict[str, object]:
    payload = official_track_payload(transcoding_url)
    payload.update(
        {
            "artwork_url": None,
            "publisher_metadata": None,
            "policy": "ALLOW",
            "streamable": True,
            "extra_field": {"ignored": True},
        }
    )
    payload["user"] = {"id": 99}
    return payload


class E2EHttpTransport:
    def __init__(
        self,
        *,
        manifest_body: str | None = None,
        segments: dict[str, bytes] | None = None,
        refresh_payload: dict[str, object] | None = None,
        refresh_status: int = 200,
        refresh_text: str | None = None,
        resolve_payload: dict[str, object] | None = None,
        resolve_redirect_location: str | None = None,
        stream_url: str = STREAM_URL,
        streams_payload: dict[str, object] | None = None,
        streams_status: int = 200,
        segment_failure_index: int | None = None,
    ) -> None:
        self.manifest_body = manifest_body if manifest_body is not None else media_manifest(SEGMENT_URLS)
        if segments is None:
            segments = {url: SEGMENT_BYTES[index] for index, url in enumerate(SEGMENT_URLS)}
        self.segments = segments
        self.resolve_payload = (
            resolve_payload if resolve_payload is not None else official_track_payload()
        )
        self.resolve_redirect_location = resolve_redirect_location
        self.refresh_payload = refresh_payload if refresh_payload is not None else {
            "access_token": REFRESHED_ACCESS,
            "refresh_token": REFRESHED_REFRESH,
            "expires_in": 3600,
        }
        self.refresh_status = refresh_status
        self.refresh_text = refresh_text
        self.stream_url = stream_url
        self.streams_payload = (
            streams_payload if streams_payload is not None else {"hls_aac_160_url": stream_url}
        )
        self.streams_status = streams_status
        self.segment_failure_index = segment_failure_index
        self.resolve_calls = 0
        self.transcoding_calls = 0
        self.streams_calls = 0
        self.manifest_calls = 0
        self.segment_calls: list[str] = []
        self.refresh_calls = 0
        self.resolve_authorizations: list[str | None] = []
        self.transcoding_authorizations: list[str | None] = []
        self.streams_authorizations: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/token":
            self.refresh_calls += 1
            if self.refresh_text is not None:
                return httpx.Response(self.refresh_status, text=self.refresh_text, request=request)
            return httpx.Response(200, json=self.refresh_payload, request=request)
        if path == "/resolve":
            self.resolve_calls += 1
            self.resolve_authorizations.append(request.headers.get("authorization"))
            if self.resolve_redirect_location is not None:
                return httpx.Response(
                    302,
                    headers={"Location": self.resolve_redirect_location},
                    request=request,
                )
            return httpx.Response(200, json=self.resolve_payload, request=request)
        if path == "/tracks/123":
            return httpx.Response(200, json=self.resolve_payload, request=request)
        if path in {
            "/tracks/123/streams",
            "/tracks/soundcloud:tracks:123/streams",
            "/tracks/soundcloud%3Atracks%3A123/streams",
        }:
            self.streams_calls += 1
            self.streams_authorizations.append(request.headers.get("authorization"))
            return httpx.Response(self.streams_status, json=self.streams_payload, request=request)
        full_url = str(request.url)
        if full_url in {TRANSCODING_URL, REAL_LIKE_TRANSCODING_URL}:
            self.transcoding_calls += 1
            self.transcoding_authorizations.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"url": self.stream_url}, request=request)
        if full_url == self.stream_url:
            self.manifest_calls += 1
            return httpx.Response(200, text=self.manifest_body, request=request)
        if full_url == DIRECT_HLS_STREAM_URL:
            self.manifest_calls += 1
            return httpx.Response(200, text=self.manifest_body, request=request)
        if full_url in self.segments:
            self.segment_calls.append(full_url)
            index = self.segment_calls.__len__() - 1
            if self.segment_failure_index is not None and index >= self.segment_failure_index:
                return httpx.Response(500, request=request)
            return httpx.Response(200, content=self.segments[full_url], request=request)
        return httpx.Response(599, request=request)


class FakeFFMPEGRunner:
    def __init__(self, settings: AppSettings | None = None) -> None:
        del settings
        self.commands: list[FFMPEGCommand] = []
        self.return_code = 0
        self.error: Exception | None = None

    def run(self, command: FFMPEGCommand) -> FFMPEGResult:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        output_path = Path(command.args[-1])
        if self.return_code == 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake-ffmpeg-output-bytes-" + output_path.suffix.encode())
        return FFMPEGResult(return_code=self.return_code, stdout="", stderr="")


def install_test_doubles(
    monkeypatch: pytest.MonkeyPatch,
    transport: E2EHttpTransport,
    *,
    ffmpeg_runner: FakeFFMPEGRunner | None = None,
) -> FakeFFMPEGRunner:
    def build_client(settings: AppSettings) -> SafeAsyncHttpClient:
        return SafeAsyncHttpClient(settings=settings, transport=httpx.MockTransport(transport))

    runner = ffmpeg_runner or FakeFFMPEGRunner()

    def build_runner(settings: AppSettings) -> FakeFFMPEGRunner:
        del settings
        return runner

    monkeypatch.setattr(download_cli, "build_safe_http_client", build_client)
    monkeypatch.setattr(download_cli, "build_ffmpeg_runner", build_runner)
    return runner


def write_env_file(
    tmp_path: Path,
    *,
    key: str,
    token_store_path: Path,
    artifact_storage_root: Path | None = None,
    artifact_temp_root: Path | None = None,
    api_base_url: str = f"https://{RESOLVE_HOST}",
    auth_base_url: str = f"https://{AUTH_HOST}",
    client_id: str | None = CLIENT_ID,
    client_secret: str | None = CLIENT_SECRET,
) -> Path:
    lines = [
        "SCD_ALLOW_NETWORK=true",
        "SCD_ALLOW_FILESYSTEM_WRITES=true",
        "SCD_LOG_LEVEL=error",
        f"SCD_OAUTH_TOKEN_STORE_PATH={token_store_path}",
        f"SCD_OAUTH_TOKEN_ENCRYPTION_KEY={key}",
        f"SCD_SOUNDCLOUD_API_BASE_URL={api_base_url}",
        f"SCD_SOUNDCLOUD_AUTH_BASE_URL={auth_base_url}",
    ]
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


def setup_token_store(
    *,
    path: Path,
    key: str,
    profile_id: str = "default",
    access_token: str = RAW_ACCESS,
    refresh_token: str | None = RAW_REFRESH,
    expired: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(hours=2) if expired else now
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_token_store_path=path,
        oauth_token_encryption_key=SecretStr(key),
    )
    EncryptedOAuthTokenStore(settings).save(
        StoredOAuthTokenSet(
            profile_id=OAuthTokenProfileId(value=profile_id),
            access_token=OAuthAccessToken(value=SecretStr(access_token)),
            refresh_token=(
                OAuthRefreshToken(value=SecretStr(refresh_token))
                if refresh_token is not None
                else None
            ),
            created_at=created_at,
            expires_at=expires_at,
        )
    )


def _load_token_set(store_path: Path, key: str) -> StoredOAuthTokenSet:
    token_set = EncryptedOAuthTokenStore(
        AppSettings(
            allow_filesystem_writes=False,
            oauth_token_store_path=store_path,
            oauth_token_encryption_key=SecretStr(key),
        )
    ).get(OAuthTokenProfileId(value="default"))
    assert token_set is not None
    return token_set


def prepared_env(
    tmp_path: Path,
    *,
    expired: bool = False,
    refresh_token: str | None = RAW_REFRESH,
    profile_id: str = "default",
    access_token: str = RAW_ACCESS,
    artifact_storage_root: Path | None = None,
    artifact_temp_root: Path | None = None,
) -> tuple[Path, Path, str]:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    setup_token_store(
        path=token_store_path,
        key=key,
        profile_id=profile_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expired=expired,
    )
    env_file = write_env_file(
        tmp_path,
        key=key,
        token_store_path=token_store_path,
        artifact_storage_root=artifact_storage_root,
        artifact_temp_root=artifact_temp_root,
    )
    return env_file, token_store_path, key


def invoke(
    *args: str,
) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["download", "track", *args])
    return result.exit_code, result.output


def block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail)


def block_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real subprocess calls are not allowed")

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(subprocess, "Popen", fail)


def _common_args(
    env_file: Path,
    *,
    output_format: str = "m4a",
    output_profile: str | None = "aac_m4a",
    access_mode: str = "go_plus",
    profile_id: str | None = None,
) -> tuple[str, ...]:
    args: list[str] = [
        TRACK_URL,
        "--env-file",
        str(env_file),
        "--access-mode",
        access_mode,
        "--format",
        output_format,
    ]
    if output_profile is not None:
        args.extend(["--output-profile", output_profile])
    if profile_id is not None:
        args.extend(["--profile-id", profile_id])
    return tuple(args)


# ----- Happy-path E2E tests -----


@pytest.mark.parametrize("fmt", ["m4a", "mp3", "wav"])
def test_e2e_cli_downloads_non_drm_hls_track_to_audio_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fmt: str,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file, output_format=fmt))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert payload["output"]["format"] == fmt
    assert payload["output"]["relative_path"].endswith(f".{fmt}")
    assert payload["segments"]["count"] == 3


def test_e2e_pipeline_uses_resolver_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert transport.resolve_calls == 1


def test_e2e_pipeline_downloads_after_safe_resolver_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(resolve_redirect_location="/tracks/123")
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert transport.resolve_calls == 1
    assert transport.transcoding_calls == 1
    assert transport.manifest_calls == 1
    assert transport.segment_calls == list(SEGMENT_URLS)


def test_e2e_pipeline_continues_after_real_like_resolver_redirect_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(
        resolve_payload=real_like_official_track_payload(),
        resolve_redirect_location="/tracks/123",
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert transport.resolve_calls == 1
    assert transport.transcoding_calls == 1


def test_e2e_pipeline_ignores_raw_resolver_stream_url_and_uses_transcodings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(
        resolve_payload=official_track_payload(stream_url=RAW_RESOLVER_STREAM_URL)
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert transport.transcoding_calls == 1
    assert RAW_RESOLVER_STREAM_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_real_like_media_transcodings_succeed_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(
        resolve_payload=official_track_payload(transcoding_url=REAL_LIKE_TRANSCODING_URL)
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert transport.transcoding_calls == 1
    assert "stage=transcoding_selection" not in output
    assert "reason=no_transcodings" not in output
    assert REAL_LIKE_TRANSCODING_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_payload_with_stream_url_but_no_transcodings_fails_selection_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    payload = official_track_payload(stream_url=RAW_RESOLVER_STREAM_URL)
    media = payload["media"]
    assert isinstance(media, dict)
    media["transcodings"] = []
    transport = E2EHttpTransport(resolve_payload=payload, streams_payload={})
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert "stage=streams_selection" in output
    assert "reason=no_hls_streams" in output
    assert transport.transcoding_calls == 0
    assert transport.streams_calls == 1
    assert RAW_RESOLVER_STREAM_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_resolver_without_media_uses_official_streams_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(
        resolve_payload=official_track_payload_without_media(),
        stream_url=DIRECT_HLS_STREAM_URL,
        streams_payload={"hls_aac_160_url": DIRECT_HLS_STREAM_URL},
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0, output
    assert payload["status"] == "succeeded"
    assert transport.transcoding_calls == 0
    assert transport.streams_calls == 1
    assert transport.streams_authorizations == [f"Bearer {RAW_ACCESS}"]
    assert transport.manifest_calls == 1
    assert DIRECT_HLS_STREAM_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_streams_endpoint_http_failure_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(
        resolve_payload=official_track_payload_without_media(),
        streams_payload={"hls_aac_160_url": DIRECT_HLS_STREAM_URL},
        streams_status=500,
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert "stage=streams_endpoint" in output
    assert "reason=streams_endpoint_failed" in output
    assert DIRECT_HLS_STREAM_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_progressive_only_transcoding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    payload = official_track_payload(transcoding_url=REAL_LIKE_TRANSCODING_URL)
    first = payload["media"]["transcodings"][0]  # type: ignore[index]
    assert isinstance(first, dict)
    format_payload = first["format"]
    assert isinstance(format_payload, dict)
    format_payload["protocol"] = "progressive"
    transport = E2EHttpTransport(resolve_payload=payload)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert "stage=transcoding_selection" in output
    assert "reason=no_safe_hls_transcoding" in output
    assert transport.transcoding_calls == 0
    assert REAL_LIKE_TRANSCODING_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_e2e_pipeline_uses_transcoding_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert transport.transcoding_calls == 1


def test_e2e_pipeline_fetches_hls_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert transport.manifest_calls == 1


def test_e2e_pipeline_fetches_all_hls_segments_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert transport.segment_calls == list(SEGMENT_URLS)


def test_e2e_pipeline_writes_staged_segment_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    staged_dir = artifact_root / "hls" / "staged" / "segments"
    assert staged_dir.is_dir()
    staged_files = sorted(p.name for p in staged_dir.iterdir() if p.is_file())
    assert staged_files == ["000000.bin", "000001.bin", "000002.bin"]


def test_e2e_pipeline_writes_assembled_media_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assembled = artifact_root / "hls" / "assembled" / "media.bin"
    assert assembled.is_file()


def test_e2e_pipeline_writes_final_audio_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    final_path = artifact_root / "audio" / "final.m4a"
    assert final_path.is_file()


def test_final_json_output_contains_only_safe_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    payload = json.loads(output)

    assert exit_code == 0
    assert set(payload.keys()) == {"status", "track", "output", "segments"}
    assert set(payload["output"].keys()) == {
        "artifact_id",
        "format",
        "relative_path",
        "size_bytes",
        "checksum",
    }


def test_plain_output_contains_only_safe_key_value_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file), "--plain")

    assert exit_code == 0, output
    assert "status=succeeded" in output
    assert "track_id=123" in output
    assert "output_format=m4a" in output
    assert "segment_count=3" in output
    for forbidden in (RAW_ACCESS, RAW_REFRESH, CLIENT_SECRET, TRANSCODING_URL, STREAM_URL):
        assert forbidden not in output
    for url in SEGMENT_URLS:
        assert url not in output


def test_custom_artifact_storage_root_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)
    storage_root = tmp_path / "custom_artifacts"

    exit_code, _output = invoke(
        *_common_args(env_file),
        "--artifact-storage-root",
        str(storage_root),
    )

    assert exit_code == 0
    assert (storage_root / "audio" / "final.m4a").is_file()


def test_custom_artifact_temp_root_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)
    temp_root = tmp_path / "custom_tmp"
    temp_root.mkdir()
    seen_before = list(temp_root.iterdir())

    exit_code, _output = invoke(
        *_common_args(env_file),
        "--artifact-temp-root",
        str(temp_root),
    )

    assert exit_code == 0
    seen_after = list(temp_root.iterdir())
    assert seen_after == seen_before


def test_custom_output_path_option_does_not_break_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(
        *_common_args(env_file),
        "--output-path",
        "audio/final.m4a",
    )

    assert exit_code == 0, output


def test_custom_token_profile_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path, profile_id="custom")
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file, profile_id="custom"))

    assert exit_code == 0
    assert transport.resolve_authorizations == [f"OAuth {RAW_ACCESS}"]


def test_expired_token_triggers_refresh_before_resolver_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert transport.refresh_calls == 1
    assert transport.resolve_authorizations == [f"OAuth {REFRESHED_ACCESS}"]


def test_refreshed_token_is_persisted_and_used_for_resolver_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, store_path, key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    refreshed = EncryptedOAuthTokenStore(
        AppSettings(
            allow_filesystem_writes=False,
            oauth_token_store_path=store_path,
            oauth_token_encryption_key=SecretStr(key),
        )
    ).get(OAuthTokenProfileId(value="default"))
    assert refreshed is not None
    assert refreshed.access_token.value.get_secret_value() == REFRESHED_ACCESS
    assert refreshed.refresh_token is not None
    assert refreshed.refresh_token.value.get_secret_value() == REFRESHED_REFRESH


def test_refresh_without_new_refresh_token_preserves_old_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, store_path, key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport(
        refresh_payload={
            "access_token": REFRESHED_ACCESS,
            "expires_in": 3599,
            "scope": "",
        }
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code == 0, output
    refreshed = _load_token_set(store_path, key)
    assert refreshed.access_token.value.get_secret_value() == REFRESHED_ACCESS
    assert refreshed.refresh_token is not None
    assert refreshed.refresh_token.value.get_secret_value() == RAW_REFRESH
    assert refreshed.expires_at is not None
    assert refreshed.expires_at > datetime.now(timezone.utc)


def test_token_status_after_refresh_reports_access_token_not_expired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, store_path, _key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))
    status = CliRunner().invoke(
        app,
        [
            "oauth",
            "token-status",
            "--env-file",
            str(env_file),
            "--token-store-path",
            str(store_path),
        ],
    )

    assert exit_code == 0, output
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["access_token_expired"] is False


def test_refresh_http_400_reports_auth_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store_path, _key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport(refresh_status=400, refresh_text='{"error":"invalid_grant"}')
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "stage=auth" in output
    assert "reason=token_refresh_failed" in output
    assert "stage=resolver" not in output
    assert RAW_ACCESS not in output
    assert RAW_REFRESH not in output
    assert CLIENT_SECRET not in output


def test_refresh_parse_failure_reports_auth_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store_path, _key = prepared_env(tmp_path, expired=True)
    transport = E2EHttpTransport(refresh_text="{")
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "stage=auth" in output
    assert "reason=token_refresh_response_invalid" in output
    assert "stage=resolver" not in output
    assert RAW_ACCESS not in output
    assert RAW_REFRESH not in output
    assert CLIENT_SECRET not in output


def test_workspace_is_cleaned_after_successful_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp_workspace"
    env_file, _store, _key = prepared_env(tmp_path, artifact_temp_root=temp_root)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert temp_root.is_dir()
    leftovers = [p for p in temp_root.iterdir() if p.is_dir()]
    assert leftovers == []


def test_no_real_network_calls_occur_in_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_network(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0


def test_no_real_ffmpeg_execution_occurs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    block_real_subprocess(monkeypatch)
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0


def test_pipeline_writes_only_under_tmp_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    temp_root = tmp_path / "tmp"
    env_file, store_path, _key = prepared_env(
        tmp_path,
        artifact_storage_root=storage_root,
        artifact_temp_root=temp_root,
    )
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    for path in (storage_root, temp_root, store_path, env_file):
        assert path.is_relative_to(tmp_path)


# ----- DRM / fail-closed denial tests -----


@pytest.mark.parametrize(
    "manifest_body",
    [
        encrypted_media_manifest(SEGMENT_URLS, key_method="AES-128"),
        encrypted_media_manifest(SEGMENT_URLS, key_method="SAMPLE-AES"),
        session_key_manifest(SEGMENT_URLS),
        fairplay_manifest(SEGMENT_URLS),
    ],
)
def test_drm_or_encrypted_manifest_is_denied_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest_body: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport(manifest_body=manifest_body)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0, output
    assert "Track download failed." in output
    final_audio_dir = artifact_root / "audio"
    if final_audio_dir.exists():
        assert list(final_audio_dir.iterdir()) == []
    for forbidden in (
        manifest_body,
        STREAM_URL,
        TRANSCODING_URL,
        *SEGMENT_URLS,
    ):
        assert forbidden not in output


def test_master_playlist_is_denied_when_media_playlist_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(manifest_body=master_playlist())
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output


def test_denied_drm_output_does_not_create_final_audio_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport(
        manifest_body=encrypted_media_manifest(SEGMENT_URLS, key_method="AES-128")
    )
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert not (artifact_root / "audio" / "final.m4a").exists()


# ----- Partial failure / cleanup tests -----


def test_segment_failure_after_partial_success_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport(segment_failure_index=1)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert not (artifact_root / "audio" / "final.m4a").exists()


def test_segment_failure_does_not_create_final_audio_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport(segment_failure_index=2)
    install_test_doubles(monkeypatch, transport)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert not (artifact_root / "audio" / "final.m4a").exists()


def test_remux_failure_does_not_create_final_output_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()
    runner = FakeFFMPEGRunner()
    runner.return_code = 1
    install_test_doubles(monkeypatch, transport, ffmpeg_runner=runner)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0, output
    assert not (artifact_root / "audio" / "final.m4a").exists()


def test_export_failure_does_not_create_final_output_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()

    class ExportFailingRunner(FakeFFMPEGRunner):
        def __init__(self, settings: AppSettings | None = None) -> None:
            super().__init__(settings)
            self._step = 0

        def run(self, command: FFMPEGCommand) -> FFMPEGResult:
            self._step += 1
            if self._step == 1:
                return super().run(command)
            self.commands.append(command)
            return FFMPEGResult(return_code=1, stdout="", stderr="")

    install_test_doubles(monkeypatch, transport, ffmpeg_runner=ExportFailingRunner())

    exit_code, output = invoke(*_common_args(env_file, output_format="mp3"))

    assert exit_code != 0, output
    assert not (artifact_root / "audio" / "final.mp3").exists()


def test_temporary_workspace_is_cleaned_after_remux_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp_workspace"
    env_file, _store, _key = prepared_env(tmp_path, artifact_temp_root=temp_root)
    transport = E2EHttpTransport()
    runner = FakeFFMPEGRunner()
    runner.return_code = 1
    install_test_doubles(monkeypatch, transport, ffmpeg_runner=runner)

    exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code != 0
    leftovers = [p for p in temp_root.iterdir() if p.is_dir()] if temp_root.exists() else []
    assert leftovers == []


def test_temporary_workspace_is_cleaned_after_export_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "tmp_workspace"
    env_file, _store, _key = prepared_env(tmp_path, artifact_temp_root=temp_root)
    transport = E2EHttpTransport()

    class ExportFailingRunner(FakeFFMPEGRunner):
        def __init__(self, settings: AppSettings | None = None) -> None:
            super().__init__(settings)
            self._step = 0

        def run(self, command: FFMPEGCommand) -> FFMPEGResult:
            self._step += 1
            if self._step == 1:
                return super().run(command)
            self.commands.append(command)
            return FFMPEGResult(return_code=1, stdout="", stderr="")

    install_test_doubles(monkeypatch, transport, ffmpeg_runner=ExportFailingRunner())

    exit_code, _output = invoke(*_common_args(env_file, output_format="mp3"))

    assert exit_code != 0
    leftovers = [p for p in temp_root.iterdir() if p.is_dir()] if temp_root.exists() else []
    assert leftovers == []


def test_storage_write_failure_exits_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    env_file, _store, _key = prepared_env(tmp_path, artifact_storage_root=artifact_root)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    from soundcloud_downloader.infrastructure.storage import local_storage as ls_module

    def fail_write(self: object, *, relative_path: object, data: object) -> Any:
        del self, relative_path, data
        from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError

        raise SoundcloudDownloaderError(ErrorCode.STORAGE_FAILED, "fake storage failure")

    monkeypatch.setattr(ls_module.LocalArtifactStorage, "write_bytes", fail_write)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output


def test_missing_token_profile_exits_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output


def test_expired_token_without_refresh_token_exits_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path, expired=True, refresh_token=None)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert RAW_ACCESS not in output
