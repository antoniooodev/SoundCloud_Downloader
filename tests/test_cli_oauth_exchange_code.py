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
    OAuthTokenResponse,
)
from soundcloud_downloader.infrastructure import EncryptedOAuthAuthorizationSessionStore


SESSION_ID = "session-1"
CLIENT_ID = "client-id-private"
CLIENT_SECRET = "client-secret-private"
CODE_VERIFIER = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
RETURNED_CODE = "returned-authorization-code-private"
RETURNED_STATE = "returned-state-private"
ACCESS_TOKEN = "access-token-private"
REFRESH_TOKEN = "refresh-token-private"
ENCRYPTION_KEY_LABEL = "oauth_session_encryption_key"


class FakeTokenExchange:
    def __init__(self, *, include_refresh_token: bool = True, scope: str | None = "read") -> None:
        self.include_refresh_token = include_refresh_token
        self.scope = scope
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
        return OAuthTokenResponse(
            access_token=OAuthAccessToken(value=SecretStr(ACCESS_TOKEN)),
            refresh_token=(
                OAuthRefreshToken(value=SecretStr(REFRESH_TOKEN))
                if self.include_refresh_token
                else None
            ),
            expires_in=3600,
            scope=self.scope,
        )


class WorkflowFactoryCapture:
    def __init__(self, fake_exchange: FakeTokenExchange) -> None:
        self.fake_exchange = fake_exchange
        self.auth_base_urls: list[str] = []

    def __call__(
        self,
        *,
        settings: AppSettings,
        session_store: Any,
        http_client: Any,
    ) -> OAuthAuthorizationCodeExchangeWorkflow:
        self.auth_base_urls.append(settings.soundcloud_auth_base_url)
        return OAuthAuthorizationCodeExchangeWorkflow(
            session_service=OAuthAuthorizationSessionService(store=session_store),
            token_exchange=self.fake_exchange,
        )


def test_exchange_code_exits_non_zero_when_encryption_key_is_missing(tmp_path: Path) -> None:
    env_file = _write_env_file(tmp_path, encryption_key=None, allow_writes=True, allow_network=True)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    _assert_output_is_safe(result.output, encryption_key="")


def test_exchange_code_exits_non_zero_when_filesystem_writes_are_disabled(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_writes=False, allow_network=True)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert "filesystem writes" in result.output.lower()
    _assert_output_is_safe(result.output)


def test_exchange_code_exits_non_zero_when_network_is_disabled(tmp_path: Path) -> None:
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_writes=True, allow_network=False)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert "network access" in result.output.lower()
    _assert_output_is_safe(result.output)


def test_exchange_code_exits_non_zero_when_session_is_missing(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file = _write_env_file(tmp_path, allow_writes=True, allow_network=True)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code != 0
    assert fake_exchange.calls == []
    _assert_output_is_safe(result.output)


def test_exchange_code_exits_non_zero_when_state_is_wrong(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path)

    result = _invoke_exchange_code("--env-file", str(env_file), "--state", "wrong-state-private")

    assert result.exit_code != 0
    assert fake_exchange.calls == []
    _assert_output_is_safe(result.output)


def test_wrong_state_does_not_consume_the_stored_session(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, store_path, key = _prepare_session(tmp_path)

    result = _invoke_exchange_code("--env-file", str(env_file), "--state", "wrong-state-private")

    assert result.exit_code != 0
    assert _load_session(store_path, key).status is OAuthSessionStatus.PENDING


def test_exchange_code_succeeds_with_persisted_session_and_fake_token_exchange(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output


def test_successful_exchange_consumes_the_stored_session(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
    assert _load_session(store_path, key).status is OAuthSessionStatus.CONSUMED


def test_successful_json_output_contains_session_id(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["session_id"] == SESSION_ID


def test_successful_json_output_contains_session_consumed_true(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["session_consumed"] is True


def test_successful_json_output_contains_access_token_received_true(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["access_token_received"] is True


def test_successful_json_output_contains_refresh_token_received_true_when_present(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["refresh_token_received"] is True


def test_successful_json_output_contains_refresh_token_received_false_when_omitted(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange(include_refresh_token=False)
    result, _store_path, _key, _capture = _successful_exchange_with_fake(
        monkeypatch,
        tmp_path,
        fake_exchange,
    )

    assert _json_payload(result)["refresh_token_received"] is False


def test_successful_json_output_contains_expires_in(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["expires_in"] == 3600


def test_successful_json_output_contains_scope_when_present(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(monkeypatch, tmp_path)

    assert _json_payload(result)["scope"] == "read"


def test_json_output_does_not_contain_authorization_code(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_returned_state(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_code_verifier(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_access_token(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_refresh_token(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_client_secret(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_json_output_does_not_contain_encryption_key(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(monkeypatch, tmp_path)

    _assert_output_is_safe(result.output, encryption_key=key)


def test_plain_output_contains_only_safe_key_value_lines(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(
        monkeypatch,
        tmp_path,
        "--plain",
    )
    lines = result.output.strip().splitlines()

    assert lines == [
        f"session_id={SESSION_ID}",
        "session_consumed=true",
        "access_token_received=true",
        "refresh_token_received=true",
        "token_persisted=false",
        "profile_id=default",
        "expires_in=3600",
        "scope=read",
    ]


def test_plain_output_does_not_contain_raw_secrets(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, _store_path, key, _capture = _successful_exchange(
        monkeypatch,
        tmp_path,
        "--plain",
    )

    _assert_output_is_safe(result.output, encryption_key=key)


def test_store_path_override_is_honored(monkeypatch: Any, tmp_path: Path) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_store_path = tmp_path / "env" / "oauth_sessions.enc"
    cli_store_path = tmp_path / "cli" / "oauth_sessions.enc"
    key = Fernet.generate_key().decode("ascii")
    _save_session(cli_store_path, key)
    env_file = _write_env_file(
        tmp_path,
        store_path=env_store_path,
        encryption_key=key,
        allow_writes=True,
        allow_network=True,
    )

    result = _invoke_exchange_code(
        "--env-file",
        str(env_file),
        "--store-path",
        str(cli_store_path),
    )

    assert result.exit_code == 0, result.output
    assert _load_session(cli_store_path, key).status is OAuthSessionStatus.CONSUMED


def test_allow_network_cli_override_is_honored(monkeypatch: Any, tmp_path: Path) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_network=False)

    result = _invoke_exchange_code("--env-file", str(env_file), "--allow-network")

    assert result.exit_code == 0, result.output


def test_no_allow_network_blocks_exchange_even_if_env_enables_network(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_network=True)

    result = _invoke_exchange_code("--env-file", str(env_file), "--no-allow-network")

    assert result.exit_code != 0
    assert fake_exchange.calls == []


def test_allow_filesystem_writes_cli_override_is_honored(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_writes=False)

    result = _invoke_exchange_code("--env-file", str(env_file), "--allow-filesystem-writes")

    assert result.exit_code == 0, result.output


def test_no_allow_filesystem_writes_blocks_exchange_even_if_env_enables_writes(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, allow_writes=True)

    result = _invoke_exchange_code("--env-file", str(env_file), "--no-allow-filesystem-writes")

    assert result.exit_code != 0
    assert fake_exchange.calls == []


def test_auth_base_url_override_is_passed_to_workflow_builder(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    capture = _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path)

    result = _invoke_exchange_code(
        "--env-file",
        str(env_file),
        "--auth-base-url",
        "https://auth.example.test/base",
    )

    assert result.exit_code == 0, result.output
    assert capture.auth_base_urls == ["https://auth.example.test/base"]


def test_env_file_client_secret_is_passed_to_fake_exchange_workflow(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, client_secret=CLIENT_SECRET)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code == 0, result.output
    client_secret = fake_exchange.calls[0]["client_secret"]
    assert isinstance(client_secret, OAuthClientSecret)
    assert client_secret.value.get_secret_value() == CLIENT_SECRET


def test_absence_of_client_secret_is_supported(monkeypatch: Any, tmp_path: Path) -> None:
    fake_exchange = FakeTokenExchange()
    _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, _store_path, _key = _prepare_session(tmp_path, client_secret=None)

    result = _invoke_exchange_code("--env-file", str(env_file))

    assert result.exit_code == 0, result.output
    assert fake_exchange.calls[0]["client_secret"] is None


def test_no_test_performs_real_network_calls(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth exchange-code CLI tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    result, _fake_exchange, _store_path, _key, _capture = _successful_exchange(
        monkeypatch,
        tmp_path,
    )

    assert result.exit_code == 0, result.output


def test_tests_write_only_inside_pytest_tmp_path(monkeypatch: Any, tmp_path: Path) -> None:
    result, _fake_exchange, store_path, _key, _capture = _successful_exchange(
        monkeypatch,
        tmp_path,
    )

    assert result.exit_code == 0, result.output
    assert store_path.is_relative_to(tmp_path)
    assert store_path.is_file()


def _successful_exchange(
    monkeypatch: Any,
    tmp_path: Path,
    *extra_args: str,
) -> tuple[Any, FakeTokenExchange, Path, str, WorkflowFactoryCapture]:
    fake_exchange = FakeTokenExchange()
    result, store_path, key, capture = _successful_exchange_with_fake(
        monkeypatch,
        tmp_path,
        fake_exchange,
        *extra_args,
    )
    return result, fake_exchange, store_path, key, capture


def _successful_exchange_with_fake(
    monkeypatch: Any,
    tmp_path: Path,
    fake_exchange: FakeTokenExchange,
    *extra_args: str,
) -> tuple[Any, Path, str, WorkflowFactoryCapture]:
    capture = _install_fake_workflow(monkeypatch, fake_exchange)
    env_file, store_path, key = _prepare_session(tmp_path)
    result = _invoke_exchange_code("--env-file", str(env_file), *extra_args)
    assert result.exit_code == 0, result.output
    return result, store_path, key, capture


def _install_fake_workflow(
    monkeypatch: Any,
    fake_exchange: FakeTokenExchange,
) -> WorkflowFactoryCapture:
    capture = WorkflowFactoryCapture(fake_exchange)
    monkeypatch.setattr(oauth_cli, "build_oauth_token_exchange_workflow", capture)
    return capture


def _prepare_session(
    tmp_path: Path,
    *,
    allow_writes: bool = True,
    allow_network: bool = True,
    client_secret: str | None = None,
) -> tuple[Path, Path, str]:
    store_path = tmp_path / "oauth_sessions.enc"
    key = Fernet.generate_key().decode("ascii")
    _save_session(store_path, key)
    env_file = _write_env_file(
        tmp_path,
        store_path=store_path,
        encryption_key=key,
        allow_writes=allow_writes,
        allow_network=allow_network,
        client_secret=client_secret,
    )
    return env_file, store_path, key


def _save_session(store_path: Path, key: str) -> None:
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=store_path,
        oauth_session_encryption_key=SecretStr(key),
    )
    EncryptedOAuthAuthorizationSessionStore(settings).save(_session())


def _load_session(store_path: Path, key: str):
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=store_path,
        oauth_session_encryption_key=SecretStr(key),
    )
    session = EncryptedOAuthAuthorizationSessionStore(settings).get(OAuthSessionId(value=SESSION_ID))
    assert session is not None
    return session


def _write_env_file(
    tmp_path: Path,
    *,
    store_path: Path | None = None,
    encryption_key: str | None = "generated",
    allow_writes: bool,
    allow_network: bool,
    client_secret: str | None = None,
) -> Path:
    env_file = tmp_path / ".env.oauth"
    selected_key = (
        Fernet.generate_key().decode("ascii")
        if encryption_key == "generated"
        else encryption_key
    )
    lines = [
        f"SCD_OAUTH_SESSION_STORE_PATH={store_path or tmp_path / 'oauth_sessions.enc'}",
        f"SCD_ALLOW_FILESYSTEM_WRITES={str(allow_writes).lower()}",
        f"SCD_ALLOW_NETWORK={str(allow_network).lower()}",
        "SCD_SOUNDCLOUD_AUTH_BASE_URL=https://secure.soundcloud.com",
    ]
    if selected_key is not None:
        lines.append(f"SCD_OAUTH_SESSION_ENCRYPTION_KEY={selected_key}")
    if client_secret is not None:
        lines.append(f"SCD_SOUNDCLOUD_CLIENT_SECRET={client_secret}")
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
        "--no-persist-token",
        *extra_args,
    ]
    return CliRunner().invoke(app, args)


def _json_payload(result: Any) -> dict[str, Any]:
    return json.loads(result.output)


def _session() -> OAuthAuthorizationSession:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OAuthAuthorizationSession(
        session_id=OAuthSessionId(value=SESSION_ID),
        client_id=OAuthClientId(value=SecretStr(CLIENT_ID)),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        authorization_url="https://secure.soundcloud.com/authorize",
        code_verifier=OAuthCodeVerifier(value=SecretStr(CODE_VERIFIER)),
        state=OAuthState(value=SecretStr(RETURNED_STATE)),
        code_challenge=OAuthCodeChallenge(value="code-challenge"),
        created_at=created_at,
        expires_at=created_at + timedelta(days=365),
    )


def _assert_output_is_safe(output: str, *, encryption_key: str | None = None) -> None:
    assert RETURNED_CODE not in output
    assert RETURNED_STATE not in output
    assert CODE_VERIFIER not in output
    assert CLIENT_SECRET not in output
    assert ACCESS_TOKEN not in output
    assert REFRESH_TOKEN not in output
    assert ENCRYPTION_KEY_LABEL not in output.lower()
    if encryption_key:
        assert encryption_key not in output
