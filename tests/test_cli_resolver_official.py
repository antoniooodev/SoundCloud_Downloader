import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from typer.testing import CliRunner

from soundcloud_downloader.cli import resolver as resolver_cli
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


RAW_ACCESS = "raw-official-access-token"
RAW_REFRESH = "raw-official-refresh-token"
REFRESHED_ACCESS = "raw-refreshed-access-token"
REFRESHED_REFRESH = "raw-refreshed-refresh-token"
CLIENT_ID = "dummy-client-id"
CLIENT_SECRET = "dummy-client-secret"
STREAM_URL = "https://api.soundcloud.test/media/raw-stream-url"
RAW_RESOLVER_STREAM_URL = (
    "https://api.soundcloud.test/tracks/123/stream?client_secret=SHOULD_NOT_LEAK"
)


def invoke_resolver(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["resolver", "inspect", *args])
    return result.exit_code, result.output


def parse_output(output: str) -> dict[str, Any]:
    return json.loads(output)


def token_store_settings(
    *,
    path: Path,
    key: str,
    allow_filesystem_writes: bool = True,
) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        oauth_token_store_path=path,
        oauth_token_encryption_key=SecretStr(key),
    )


def save_token(
    *,
    path: Path,
    key: str,
    profile_id: str = "default",
    access_token: str = RAW_ACCESS,
    refresh_token: str | None = RAW_REFRESH,
    expired: bool = False,
    expires_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(hours=2) if expired else now
    effective_expires_at = expires_at
    if effective_expires_at is None:
        effective_expires_at = now - timedelta(hours=1) if expired else now + timedelta(hours=1)
    EncryptedOAuthTokenStore(token_store_settings(path=path, key=key)).save(
        StoredOAuthTokenSet(
            profile_id=OAuthTokenProfileId(value=profile_id),
            access_token=OAuthAccessToken(value=SecretStr(access_token)),
            refresh_token=(
                OAuthRefreshToken(value=SecretStr(refresh_token))
                if refresh_token is not None
                else None
            ),
            created_at=created_at,
            expires_at=effective_expires_at,
        )
    )


def load_token(path: Path, key: str, profile_id: str = "default") -> StoredOAuthTokenSet | None:
    return EncryptedOAuthTokenStore(
        token_store_settings(path=path, key=key, allow_filesystem_writes=False)
    ).get(OAuthTokenProfileId(value=profile_id))


def write_env_file(
    tmp_path: Path,
    *,
    key: str | None,
    token_store_path: Path,
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
    env_file = tmp_path / "settings.env"
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return env_file


def official_track_payload() -> dict[str, object]:
    return {
        "kind": "track",
        "id": 123,
        "title": "Official Track",
        "duration": 1_000,
        "permalink_url": "https://soundcloud.com/user/track",
        "sharing": "public",
        "downloadable": False,
        "media": {
            "transcodings": [
                {
                    "url": STREAM_URL,
                    "preset": "mp3_1_0",
                    "format": {
                        "protocol": "progressive",
                        "mime_type": "audio/mpeg",
                    },
                }
            ]
        },
    }


def official_playlist_payload() -> dict[str, object]:
    return {
        "kind": "playlist",
        "id": 456,
        "title": "Official Playlist",
        "track_count": 1,
        "tracks": [official_track_payload()],
    }


def official_user_payload() -> dict[str, object]:
    return {"kind": "user", "id": 789, "username": "official-user"}


class MockSoundCloudTransport:
    def __init__(
        self,
        *,
        resolve_payload: dict[str, object] | None = None,
        resolve_status: int = 200,
        resolve_text: str | None = None,
        redirect_location: str | None = None,
        refresh_payload: dict[str, object] | None = None,
    ) -> None:
        self.resolve_payload = resolve_payload if resolve_payload is not None else official_track_payload()
        self.resolve_status = resolve_status
        self.resolve_text = resolve_text
        self.redirect_location = redirect_location
        self.refresh_payload = refresh_payload if refresh_payload is not None else {
            "access_token": REFRESHED_ACCESS,
            "refresh_token": REFRESHED_REFRESH,
            "expires_in": 3600,
        }
        self.resolve_calls = 0
        self.refresh_calls = 0
        self.resolve_authorizations: list[str | None] = []
        self.resource_calls = 0
        self.resource_authorizations: list[str | None] = []
        self.request_urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.request_urls.append(str(request.url))
        if request.url.path == "/oauth/token":
            self.refresh_calls += 1
            return httpx.Response(200, json=self.refresh_payload, request=request)
        if request.url.path == "/resolve":
            self.resolve_calls += 1
            self.resolve_authorizations.append(request.headers.get("authorization"))
            if self.redirect_location is not None:
                return httpx.Response(
                    self.resolve_status,
                    headers={"Location": self.redirect_location},
                    request=request,
                )
            if self.resolve_text is not None:
                return httpx.Response(self.resolve_status, text=self.resolve_text, request=request)
            return httpx.Response(self.resolve_status, json=self.resolve_payload, request=request)
        if request.url.path == "/tracks/123":
            self.resource_calls += 1
            self.resource_authorizations.append(request.headers.get("authorization"))
            return httpx.Response(200, json=self.resolve_payload, request=request)
        return httpx.Response(599, request=request)


def patch_http_client(monkeypatch: pytest.MonkeyPatch, handler: MockSoundCloudTransport) -> None:
    def build_client(settings: AppSettings) -> SafeAsyncHttpClient:
        return SafeAsyncHttpClient(settings=settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(resolver_cli, "build_safe_http_client", build_client)


def prepared_env(tmp_path: Path, *, expired: bool = False) -> tuple[Path, Path, str]:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(path=token_store_path, key=key, expired=expired)
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)
    return env_file, token_store_path, key


def test_default_resolver_inspect_remains_offline() -> None:
    exit_code, output = invoke_resolver("https://soundcloud.com/user/track")

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["resolved"] is False
    assert "resolution_mode" not in payload


def test_external_mode_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    def build_client(settings: AppSettings) -> SafeAsyncHttpClient:
        return SafeAsyncHttpClient(
            settings=settings,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "status": "resolved",
                        "kind": "track",
                        "track": {"soundcloud_id": "1", "title": "External"},
                    },
                    request=request,
                )
            ),
        )

    monkeypatch.setattr(resolver_cli, "build_safe_http_client", build_client)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["resolved"] is True


def test_official_and_external_modes_are_mutually_exclusive() -> None:
    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--external",
    )

    assert exit_code != 0
    assert "mutually exclusive" in output


@pytest.mark.parametrize(
    ("env_overrides", "expected"),
    [
        ({"allow_network": False}, "Network access must be enabled"),
        ({"allow_filesystem_writes": False}, "Filesystem writes must be enabled"),
        ({"key": None}, "Authenticated resolver mode is not configured"),
        ({"client_id": None}, "Authenticated resolver mode is not configured"),
        ({"client_secret": None}, "Authenticated resolver mode is not configured"),
    ],
)
def test_official_mode_preflight_failures_are_safe(
    tmp_path: Path,
    env_overrides: dict[str, object],
    expected: str,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    env_file = write_env_file(
        tmp_path,
        key=env_overrides.get("key", key),  # type: ignore[arg-type]
        token_store_path=token_store_path,
        allow_network=bool(env_overrides.get("allow_network", True)),
        allow_filesystem_writes=bool(env_overrides.get("allow_filesystem_writes", True)),
        client_id=env_overrides.get("client_id", CLIENT_ID),  # type: ignore[arg-type]
        client_secret=env_overrides.get("client_secret", CLIENT_SECRET),  # type: ignore[arg-type]
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert expected in output
    assert RAW_ACCESS not in output
    assert RAW_REFRESH not in output
    assert CLIENT_SECRET not in output


def test_official_mode_exits_nonzero_when_token_profile_is_missing(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "OAuth token profile is missing or unusable." in output


def test_allow_network_cli_override_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(path=token_store_path, key=key)
    env_file = write_env_file(
        tmp_path,
        key=key,
        token_store_path=token_store_path,
        allow_network=False,
    )
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--allow-network",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.resolve_calls == 1


def test_no_allow_network_cli_override_blocks_env_enabled(tmp_path: Path) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--no-allow-network",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Network access must be enabled" in output


def test_allow_filesystem_writes_cli_override_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(path=token_store_path, key=key)
    env_file = write_env_file(
        tmp_path,
        key=key,
        token_store_path=token_store_path,
        allow_filesystem_writes=False,
    )
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--allow-filesystem-writes",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.resolve_calls == 1


def test_no_allow_filesystem_writes_cli_override_blocks_env_enabled(tmp_path: Path) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--no-allow-filesystem-writes",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Filesystem writes must be enabled" in output


def test_expired_token_without_refresh_token_fails_closed(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(path=token_store_path, key=key, refresh_token=None, expired=True)
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "OAuth token profile is missing or unusable." in output
    assert RAW_ACCESS not in output
    assert RAW_REFRESH not in output


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (official_track_payload(), "track"),
        (official_playlist_payload(), "playlist"),
        (official_user_payload(), "user"),
    ],
)
def test_official_mode_resolves_mocked_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, object],
    kind: str,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_payload=payload)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )
    result = parse_output(output)

    assert exit_code == 0
    assert result["resolved"] is True
    assert result["resolution_mode"] == "official"
    assert result["resolved_resource"]["kind"] == kind


def test_official_mode_uses_stored_access_token_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.refresh_calls == 0
    assert transport.resolve_authorizations == [f"OAuth {RAW_ACCESS}"]


def test_official_mode_follows_safe_absolute_resolve_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(
        resolve_status=302,
        redirect_location="https://api.soundcloud.test/tracks/123",
    )
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )
    result = parse_output(output)

    assert exit_code == 0, output
    assert result["resolved"] is True
    assert transport.resolve_calls == 1
    assert transport.resource_calls == 1


def test_official_mode_follows_safe_relative_resolve_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=302, redirect_location="/tracks/123")
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.resource_calls == 1


def test_official_mode_preserves_authorization_for_safe_soundcloud_api_redirect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=302, redirect_location="/tracks/123")
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.resolve_authorizations == [f"OAuth {RAW_ACCESS}"]
    assert transport.resource_authorizations == [f"OAuth {RAW_ACCESS}"]


@pytest.mark.parametrize(
    "redirect_location",
    [
        "https://evil.example.test/tracks/123",
        "https://user:pass@api.soundcloud.test/tracks/123",
        "https://api.soundcloud.test/tracks/123?access_token=secret",
        "https://api.soundcloud.test/tracks/123?client_secret=secret",
    ],
)
def test_official_mode_rejects_unsafe_resolve_redirects_without_leaking_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    redirect_location: str,
) -> None:
    source_url = "https://soundcloud.com/user/private-track"
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=302, redirect_location=redirect_location)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        source_url,
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Official resolver request failed." in output
    assert source_url not in output
    assert redirect_location not in output
    assert RAW_ACCESS not in output
    assert CLIENT_SECRET not in output
    assert transport.resource_calls == 0


def test_official_mode_stops_redirect_loop_at_max_redirects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=302, redirect_location="/resolve")
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Official resolver request failed." in output
    assert transport.resolve_calls == 4


def test_official_mode_refreshes_expired_token_and_persists_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, token_store_path, key = prepared_env(tmp_path, expired=True)
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )
    stored = load_token(token_store_path, key)

    assert exit_code == 0, output
    assert transport.refresh_calls == 1
    assert transport.resolve_authorizations == [f"OAuth {REFRESHED_ACCESS}"]
    assert stored is not None
    assert stored.access_token.value.get_secret_value() == REFRESHED_ACCESS
    assert stored.refresh_token is not None
    assert stored.refresh_token.value.get_secret_value() == REFRESHED_REFRESH


def test_profile_id_selects_custom_token_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(
        path=token_store_path,
        key=key,
        profile_id="custom",
        access_token="raw-custom-access-token",
    )
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--profile-id",
        "custom",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert transport.resolve_authorizations == ["OAuth raw-custom-access-token"]


def test_token_store_path_override_is_honored(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    env_store_path = tmp_path / "env_tokens.enc"
    override_store_path = tmp_path / "override_tokens.enc"
    save_token(path=override_store_path, key=key)
    env_file = write_env_file(tmp_path, key=key, token_store_path=env_store_path)
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
        "--token-store-path",
        str(override_store_path),
    )

    assert exit_code == 0, output
    assert transport.resolve_calls == 1
    assert env_store_path.exists() is False


@pytest.mark.parametrize("status_code", [401, 403, 404, 429, 500])
def test_official_http_failures_exit_nonzero_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status_code: int,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=status_code)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Official resolver request failed." in output
    assert RAW_ACCESS not in output
    assert RAW_REFRESH not in output
    assert CLIENT_SECRET not in output


@pytest.mark.parametrize(
    ("resolve_payload", "resolve_text"),
    [
        (None, "{"),
        ({"kind": "comment", "id": 1}, None),
    ],
)
def test_official_malformed_payloads_exit_nonzero_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolve_payload: dict[str, object] | None,
    resolve_text: str | None,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_payload=resolve_payload, resolve_text=resolve_text)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Official resolver request failed." in output
    assert "reason=official_resolver_payload_invalid" in output


def test_official_malformed_payload_prints_invalid_fields_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    payload = official_track_payload()
    media = payload["media"]
    assert isinstance(media, dict)
    transcodings = media["transcodings"]
    assert isinstance(transcodings, list)
    first = transcodings[0]
    assert isinstance(first, dict)
    raw_url = "https://api.soundcloud.test/media/secret-url?access_token=raw-token"
    first["url"] = raw_url
    transport = MockSoundCloudTransport(resolve_payload=payload)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    assert "Official resolver request failed." in output
    assert "reason=official_resolver_payload_invalid" in output
    assert "invalid_fields=media.transcodings.0.url" in output
    assert raw_url not in output
    assert "raw-token" not in output
    assert RAW_ACCESS not in output
    assert CLIENT_SECRET not in output


def test_official_mode_accepts_and_ignores_top_level_stream_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, _key = prepared_env(tmp_path)
    payload = official_track_payload()
    payload["stream_url"] = RAW_RESOLVER_STREAM_URL
    transport = MockSoundCloudTransport(resolve_payload=payload)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert "official_resolver_payload_invalid" not in output
    assert "stream_url" not in output
    assert RAW_RESOLVER_STREAM_URL not in output
    assert "SHOULD_NOT_LEAK" not in output


def test_official_output_does_not_expose_secrets_or_stream_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = Fernet.generate_key().decode()
    token_store_path = tmp_path / "oauth_tokens.enc"
    save_token(path=token_store_path, key=key)
    env_file = write_env_file(tmp_path, key=key, token_store_path=token_store_path)
    transport = MockSoundCloudTransport()
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    for forbidden in (
        RAW_ACCESS,
        RAW_REFRESH,
        CLIENT_SECRET,
        key,
        STREAM_URL,
        "access_token",
        "refresh_token",
        "client_secret",
        "stream_url",
        "manifest_url",
    ):
        assert forbidden not in output


def test_official_error_output_does_not_expose_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _token_store_path, key = prepared_env(tmp_path)
    transport = MockSoundCloudTransport(resolve_status=401)
    patch_http_client(monkeypatch, transport)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--official",
        "--env-file",
        str(env_file),
    )

    assert exit_code != 0
    for forbidden in (RAW_ACCESS, RAW_REFRESH, CLIENT_SECRET, key):
        assert forbidden not in output
