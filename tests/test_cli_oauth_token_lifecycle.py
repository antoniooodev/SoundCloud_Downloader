import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from pydantic import SecretStr
from typer.testing import CliRunner

import soundcloud_downloader.cli.oauth as oauth_cli
from soundcloud_downloader.application import (
    OAuthAuthorizationCodeExchangeWorkflow,
    OAuthAuthorizationSessionService,
)
from soundcloud_downloader.cli.main import app
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthAuthorizationSession,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeChallenge,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthRefreshToken,
    OAuthSessionId,
    OAuthSessionStatus,
    OAuthState,
    OAuthTokenProfileId,
    OAuthTokenResponse,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure import (
    EncryptedOAuthAuthorizationSessionStore,
    EncryptedOAuthTokenStore,
)
from soundcloud_downloader.infrastructure.soundcloud import OAuthTokenExchangeError


SESSION_ID = "session-1"
CLIENT_ID = "client-id-private"
CLIENT_SECRET = "client-secret-private"
CODE_VERIFIER = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
RETURNED_CODE = "returned-authorization-code-private"
RETURNED_STATE = "returned-state-private"
ACCESS_TOKEN = "access-token-private"
REFRESH_TOKEN = "refresh-token-private"


class FakeTokenExchange:
    def __init__(
        self,
        *,
        refresh_token: str | None = REFRESH_TOKEN,
        error: OAuthTokenExchangeError | None = None,
    ) -> None:
        self.refresh_token = refresh_token
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def exchange_authorization_code(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret | None,
        redirect_uri: OAuthRedirectUri,
        code: OAuthAuthorizationCode,
        code_verifier: OAuthCodeVerifier,
    ) -> OAuthTokenResponse:
        self.calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            }
        )
        if self.error is not None:
            raise self.error
        return OAuthTokenResponse(
            access_token=OAuthAccessToken(value=SecretStr(ACCESS_TOKEN)),
            refresh_token=(
                OAuthRefreshToken(value=SecretStr(self.refresh_token))
                if self.refresh_token is not None
                else None
            ),
            expires_in=3600,
            scope="read",
        )


class WorkflowFactory:
    def __init__(self, fake_exchange: FakeTokenExchange) -> None:
        self.fake_exchange = fake_exchange

    def __call__(
        self,
        *,
        settings: AppSettings,
        session_store: Any,
        http_client: Any,
    ) -> OAuthAuthorizationCodeExchangeWorkflow:
        return OAuthAuthorizationCodeExchangeWorkflow(
            session_service=OAuthAuthorizationSessionService(store=session_store),
            token_exchange=self.fake_exchange,
        )


def test_exchange_code_default_persist_token_saves_token_set(monkeypatch: Any, tmp_path: Path) -> None:
    result, store_path, key = _successful_exchange(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert _load_token_set(store_path, key) is not None


def test_exchange_code_no_persist_token_does_not_require_token_encryption_key(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _install_fake_workflow(monkeypatch)
    env_file, _session_path, _session_key, token_path, _token_key = _prepare_session(
        tmp_path,
        include_token_key=False,
    )

    result = _invoke_exchange_code("--env-file", str(env_file), "--no-persist-token")

    assert result.exit_code == 0, result.output
    assert token_path.exists() is False


def test_exchange_code_no_persist_token_does_not_create_token_store_file(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _install_fake_workflow(monkeypatch)
    env_file, _session_path, _session_key, token_path, _token_key = _prepare_session(tmp_path)

    result = _invoke_exchange_code("--env-file", str(env_file), "--no-persist-token")

    assert result.exit_code == 0, result.output
    assert token_path.exists() is False


def test_persist_token_without_token_encryption_key_exits_before_consuming_session(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _install_fake_workflow(monkeypatch)
    env_file, session_path, session_key, token_path, _token_key = _prepare_session(
        tmp_path,
        include_token_key=False,
    )

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert token_path.exists() is False
    assert _load_session(session_path, session_key).status is OAuthSessionStatus.PENDING


def test_persist_token_with_filesystem_writes_disabled_exits_before_consuming_session(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _install_fake_workflow(monkeypatch)
    env_file, session_path, session_key, _token_path, _token_key = _prepare_session(
        tmp_path,
        allow_writes=False,
    )

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert _load_session(session_path, session_key).status is OAuthSessionStatus.PENDING


def test_persisted_token_store_file_does_not_contain_raw_access_token(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _result, store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert ACCESS_TOKEN.encode("utf-8") not in store_path.read_bytes()


def test_persisted_token_store_file_does_not_contain_raw_refresh_token(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _result, store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert REFRESH_TOKEN.encode("utf-8") not in store_path.read_bytes()


def test_persisted_token_can_be_loaded_by_encrypted_store(monkeypatch: Any, tmp_path: Path) -> None:
    _result, store_path, key = _successful_exchange(monkeypatch, tmp_path)

    token_set = _load_token_set(store_path, key)

    assert token_set.access_token.value.get_secret_value() == ACCESS_TOKEN


def test_persisted_profile_id_defaults_to_default(monkeypatch: Any, tmp_path: Path) -> None:
    result, store_path, key = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["profile_id"] == "default"
    assert _load_token_set(store_path, key).profile_id.value == "default"


def test_custom_profile_id_is_honored(monkeypatch: Any, tmp_path: Path) -> None:
    result, store_path, key = _successful_exchange(monkeypatch, tmp_path, "--profile-id", "custom")

    assert _json_payload(result)["profile_id"] == "custom"
    assert _load_token_set(store_path, key, profile_id="custom").profile_id.value == "custom"


def test_token_store_path_override_is_honored(monkeypatch: Any, tmp_path: Path) -> None:
    _install_fake_workflow(monkeypatch)
    env_file, _session_path, _session_key, env_token_path, token_key = _prepare_session(tmp_path)
    cli_token_path = tmp_path / "cli" / "oauth_tokens.enc"

    result = _invoke_exchange_code(
        "--env-file",
        str(env_file),
        "--token-store-path",
        str(cli_token_path),
    )

    assert result.exit_code == 0, result.output
    assert cli_token_path.is_file()
    assert env_token_path.exists() is False
    assert _load_token_set(cli_token_path, token_key) is not None


def test_json_exchange_output_contains_token_persisted_true(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["token_persisted"] is True


def test_json_exchange_output_contains_profile_id(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["profile_id"] == "default"


def test_json_exchange_output_does_not_contain_raw_access_token(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_json_exchange_output_does_not_contain_raw_refresh_token(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_json_exchange_output_does_not_contain_authorization_code(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_json_exchange_output_does_not_contain_returned_state(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_json_exchange_output_does_not_contain_code_verifier(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_plain_exchange_output_contains_token_persisted_true_and_safe_lines(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path, "--plain")

    assert "token_persisted=true" in result.output
    assert "profile_id=default" in result.output
    _assert_output_is_safe(result.output, token_key=token_key)


def test_token_status_with_existing_token_reports_present(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["token_present"] is True


def test_token_status_reports_refresh_token_present_true_when_refresh_token_exists(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["refresh_token_present"] is True


def test_token_status_reports_refresh_token_present_false_when_refresh_token_absent(
    tmp_path: Path,
) -> None:
    env_file, _store_path, _key = _prepare_token_store(tmp_path, _stored_token_set(refresh_token=None))

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["refresh_token_present"] is False


def test_token_status_reports_access_token_expired_false_for_valid_token(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["access_token_expired"] is False


def test_token_status_reports_access_token_expired_true_for_expired_token(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(
        tmp_path,
        _stored_token_set(expires_at=_past(seconds=1)),
    )

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["access_token_expired"] is True


def test_token_status_with_missing_token_reports_token_present_false(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_empty_token_store(tmp_path)

    result = _invoke_token_status("--env-file", str(env_file))

    assert _json_payload(result)["token_present"] is False


def test_token_status_does_not_require_filesystem_writes_true(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(
        tmp_path,
        _stored_token_set(),
        allow_writes_for_env=False,
    )

    result = _invoke_token_status("--env-file", str(env_file))

    assert result.exit_code == 0, result.output


def test_token_status_output_does_not_contain_raw_access_token(tmp_path: Path) -> None:
    env_file, _store_path, token_key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_token_status("--env-file", str(env_file))

    _assert_output_is_safe(result.output, token_key=token_key)


def test_token_status_output_does_not_contain_raw_refresh_token(tmp_path: Path) -> None:
    env_file, _store_path, token_key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_token_status("--env-file", str(env_file))

    _assert_output_is_safe(result.output, token_key=token_key)


def test_logout_deletes_stored_token(tmp_path: Path) -> None:
    env_file, store_path, key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_logout("--env-file", str(env_file))

    assert result.exit_code == 0, result.output
    assert _load_optional_token_set(store_path, key) is None


def test_logout_is_idempotent_for_missing_token(tmp_path: Path) -> None:
    env_file, store_path, key = _prepare_empty_token_store(tmp_path)

    first = _invoke_logout("--env-file", str(env_file))
    second = _invoke_logout("--env-file", str(env_file))

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert _load_optional_token_set(store_path, key) is None


def test_logout_requires_filesystem_writes_enabled(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_token_store(
        tmp_path,
        _stored_token_set(),
        allow_writes_for_env=False,
    )

    result = _invoke_logout("--env-file", str(env_file))

    assert result.exit_code != 0


def test_logout_output_does_not_contain_raw_access_token(tmp_path: Path) -> None:
    env_file, _store_path, token_key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_logout("--env-file", str(env_file))

    _assert_output_is_safe(result.output, token_key=token_key)


def test_logout_output_does_not_contain_raw_refresh_token(tmp_path: Path) -> None:
    env_file, _store_path, token_key = _prepare_token_store(tmp_path, _stored_token_set())

    result = _invoke_logout("--env-file", str(env_file))

    _assert_output_is_safe(result.output, token_key=token_key)


def test_token_store_path_override_is_honored_by_token_status(tmp_path: Path) -> None:
    env_file, _env_store_path, key = _prepare_empty_token_store(tmp_path)
    override_path = tmp_path / "override" / "oauth_tokens.enc"
    _save_token_set(override_path, key, _stored_token_set())

    result = _invoke_token_status(
        "--env-file",
        str(env_file),
        "--token-store-path",
        str(override_path),
    )

    assert _json_payload(result)["token_present"] is True


def test_token_store_path_override_is_honored_by_logout(tmp_path: Path) -> None:
    env_file, _env_store_path, key = _prepare_empty_token_store(tmp_path)
    override_path = tmp_path / "override" / "oauth_tokens.enc"
    _save_token_set(override_path, key, _stored_token_set())

    result = _invoke_logout(
        "--env-file",
        str(env_file),
        "--token-store-path",
        str(override_path),
    )

    assert result.exit_code == 0, result.output
    assert _load_optional_token_set(override_path, key) is None


def test_no_command_output_includes_oauth_token_encryption_key(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_no_command_output_includes_client_secret(monkeypatch: Any, tmp_path: Path) -> None:
    result, _store_path, token_key = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, token_key=token_key)


def test_tests_perform_no_real_network_calls(monkeypatch: Any, tmp_path: Path) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth token lifecycle tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    result, _store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output


def test_tests_write_only_inside_pytest_tmp_path(monkeypatch: Any, tmp_path: Path) -> None:
    result, store_path, _key = _successful_exchange(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert store_path.is_relative_to(tmp_path)
    assert store_path.is_file()


def test_exchange_code_token_response_validation_failure_output_is_safe(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _install_fake_workflow(
        monkeypatch,
        error=OAuthTokenExchangeError(
            ErrorCode.UNKNOWN_UNSAFE,
            'OAuth token response validation failed. invalid_fields=["token_type"]',
        ),
    )
    env_file, _session_path, _session_key, _token_path, token_key = _prepare_session(tmp_path)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert "OAuth token response validation failed." in result.output
    assert 'invalid_fields=["token_type"]' in result.output
    _assert_output_is_safe(result.output, token_key=token_key)


def _successful_exchange(
    monkeypatch: Any,
    tmp_path: Path,
    *extra_args: str,
) -> tuple[Any, Path, str]:
    _install_fake_workflow(monkeypatch)
    env_file, _session_path, _session_key, token_path, token_key = _prepare_session(tmp_path)
    result = _invoke_exchange_code("--env-file", str(env_file), *extra_args)
    assert result.exit_code == 0, result.output
    return result, token_path, token_key


def _install_fake_workflow(
    monkeypatch: Any,
    *,
    error: OAuthTokenExchangeError | None = None,
) -> FakeTokenExchange:
    fake_exchange = FakeTokenExchange(error=error)
    monkeypatch.setattr(oauth_cli, "build_oauth_token_exchange_workflow", WorkflowFactory(fake_exchange))
    return fake_exchange


def _prepare_session(
    tmp_path: Path,
    *,
    include_token_key: bool = True,
    allow_writes: bool = True,
) -> tuple[Path, Path, str, Path, str]:
    session_path = tmp_path / "oauth_sessions.enc"
    token_path = tmp_path / "oauth_tokens.enc"
    session_key = Fernet.generate_key().decode("ascii")
    token_key = Fernet.generate_key().decode("ascii")
    _save_session(session_path, session_key)
    env_file = _write_env_file(
        tmp_path,
        session_path=session_path,
        session_key=session_key,
        token_path=token_path,
        token_key=token_key if include_token_key else None,
        allow_writes=allow_writes,
        allow_network=True,
    )
    return env_file, session_path, session_key, token_path, token_key


def _prepare_token_store(
    tmp_path: Path,
    token_set: StoredOAuthTokenSet,
    *,
    allow_writes_for_env: bool = True,
) -> tuple[Path, Path, str]:
    store_path = tmp_path / "oauth_tokens.enc"
    key = Fernet.generate_key().decode("ascii")
    _save_token_set(store_path, key, token_set)
    env_file = _write_env_file(
        tmp_path,
        session_path=tmp_path / "oauth_sessions.enc",
        session_key=Fernet.generate_key().decode("ascii"),
        token_path=store_path,
        token_key=key,
        allow_writes=allow_writes_for_env,
        allow_network=False,
    )
    return env_file, store_path, key


def _prepare_empty_token_store(tmp_path: Path) -> tuple[Path, Path, str]:
    store_path = tmp_path / "oauth_tokens.enc"
    key = Fernet.generate_key().decode("ascii")
    env_file = _write_env_file(
        tmp_path,
        session_path=tmp_path / "oauth_sessions.enc",
        session_key=Fernet.generate_key().decode("ascii"),
        token_path=store_path,
        token_key=key,
        allow_writes=True,
        allow_network=False,
    )
    return env_file, store_path, key


def _write_env_file(
    tmp_path: Path,
    *,
    session_path: Path,
    session_key: str,
    token_path: Path,
    token_key: str | None,
    allow_writes: bool,
    allow_network: bool,
) -> Path:
    env_file = tmp_path / ".env.oauth"
    lines = [
        f"SCD_OAUTH_SESSION_STORE_PATH={session_path}",
        f"SCD_OAUTH_SESSION_ENCRYPTION_KEY={session_key}",
        f"SCD_OAUTH_TOKEN_STORE_PATH={token_path}",
        f"SCD_ALLOW_FILESYSTEM_WRITES={str(allow_writes).lower()}",
        f"SCD_ALLOW_NETWORK={str(allow_network).lower()}",
        "SCD_SOUNDCLOUD_AUTH_BASE_URL=https://secure.soundcloud.com",
        f"SCD_SOUNDCLOUD_CLIENT_SECRET={CLIENT_SECRET}",
    ]
    if token_key is not None:
        lines.append(f"SCD_OAUTH_TOKEN_ENCRYPTION_KEY={token_key}")
    env_file.write_text("\n".join(lines), encoding="utf-8")
    return env_file


def _invoke_exchange_code(*extra_args: str):
    args = [
        "oauth",
        "exchange-code",
        "--session-id",
        SESSION_ID,
        "--code",
        RETURNED_CODE,
        "--state",
        RETURNED_STATE,
        *extra_args,
    ]
    return CliRunner().invoke(app, args)


def _invoke_token_status(*extra_args: str):
    return CliRunner().invoke(app, ["oauth", "token-status", *extra_args])


def _invoke_logout(*extra_args: str):
    return CliRunner().invoke(app, ["oauth", "logout", *extra_args])


def _save_session(store_path: Path, key: str) -> None:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=store_path,
        oauth_session_encryption_key=SecretStr(key),
    )
    EncryptedOAuthAuthorizationSessionStore(settings).save(_session())


def _save_token_set(store_path: Path, key: str, token_set: StoredOAuthTokenSet) -> None:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_token_store_path=store_path,
        oauth_token_encryption_key=SecretStr(key),
    )
    EncryptedOAuthTokenStore(settings).save(token_set)


def _load_session(store_path: Path, key: str) -> OAuthAuthorizationSession:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=store_path,
        oauth_session_encryption_key=SecretStr(key),
    )
    session = EncryptedOAuthAuthorizationSessionStore(settings).get(OAuthSessionId(value=SESSION_ID))
    assert session is not None
    return session


def _load_token_set(
    store_path: Path,
    key: str,
    *,
    profile_id: str = "default",
) -> StoredOAuthTokenSet:
    token_set = _load_optional_token_set(store_path, key, profile_id=profile_id)
    assert token_set is not None
    return token_set


def _load_optional_token_set(
    store_path: Path,
    key: str,
    *,
    profile_id: str = "default",
) -> StoredOAuthTokenSet | None:
    settings = AppSettings(
        allow_filesystem_writes=False,
        oauth_token_store_path=store_path,
        oauth_token_encryption_key=SecretStr(key),
    )
    return EncryptedOAuthTokenStore(settings).get(OAuthTokenProfileId(value=profile_id))


def _json_payload(result: Any) -> dict[str, Any]:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _session() -> OAuthAuthorizationSession:
    created_at = datetime.now(timezone.utc)
    return OAuthAuthorizationSession(
        session_id=OAuthSessionId(value=SESSION_ID),
        client_id=OAuthClientId(value=SecretStr(CLIENT_ID)),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        authorization_url="https://secure.soundcloud.com/authorize",
        code_verifier=OAuthCodeVerifier(value=SecretStr(CODE_VERIFIER)),
        state=OAuthState(value=SecretStr(RETURNED_STATE)),
        code_challenge=OAuthCodeChallenge(value="code-challenge"),
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
    )


def _stored_token_set(
    *,
    refresh_token: str | None = REFRESH_TOKEN,
    expires_at: datetime | None = None,
) -> StoredOAuthTokenSet:
    created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    return StoredOAuthTokenSet(
        profile_id=OAuthTokenProfileId(value="default"),
        access_token=OAuthAccessToken(value=SecretStr(ACCESS_TOKEN)),
        refresh_token=(
            OAuthRefreshToken(value=SecretStr(refresh_token))
            if refresh_token is not None
            else None
        ),
        scope="read",
        created_at=created_at,
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _past(*, seconds: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def _assert_output_is_safe(output: str, *, token_key: str) -> None:
    assert ACCESS_TOKEN not in output
    assert REFRESH_TOKEN not in output
    assert RETURNED_CODE not in output
    assert RETURNED_STATE not in output
    assert CODE_VERIFIER not in output
    assert CLIENT_SECRET not in output
    assert token_key not in output
