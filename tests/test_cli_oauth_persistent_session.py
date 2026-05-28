import json
import socket
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from pydantic import SecretStr
from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import OAuthSessionId
from soundcloud_downloader.infrastructure import EncryptedOAuthAuthorizationSessionStore


CLIENT_ID = "example-client-id"
REDIRECT_URI = "http://localhost:8765/callback"
MEMORY_WARNING = (
    "This session is stored in memory only and will not survive process exit. "
    "Persistent secure storage will be implemented in a later task."
)
PERSISTENT_WARNING = (
    "This session was stored in the encrypted OAuth session store. "
    "Keep the encryption key available for token exchange."
)


def test_create_session_default_mode_remains_memory_mode(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=True)

    result = _invoke_create_session("--env-file", str(env_file))

    assert result.exit_code == 0, result.output
    assert _json_payload(result)["warning"] == MEMORY_WARNING
    assert store_path.exists() is False


def test_memory_mode_does_not_create_store_file(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=True)

    result = _invoke_create_session("--env-file", str(env_file), "--memory")

    assert result.exit_code == 0, result.output
    assert store_path.exists() is False


def test_memory_json_warning_says_session_is_memory_only(tmp_path: Path) -> None:
    env_file, _key = _write_env_file(tmp_path, allow_writes=True)

    result = _invoke_create_session("--env-file", str(env_file), "--memory")

    assert result.exit_code == 0, result.output
    assert _json_payload(result)["warning"] == MEMORY_WARNING


def test_persist_without_encryption_key_exits_non_zero(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file = tmp_path / ".env.oauth"
    env_file.write_text(
        "\n".join(
            [
                f"SCD_OAUTH_SESSION_STORE_PATH={store_path}",
                "SCD_ALLOW_FILESYSTEM_WRITES=true",
            ]
        ),
        encoding="utf-8",
    )

    result = _invoke_create_session("--env-file", str(env_file), "--persist")

    assert result.exit_code != 0
    assert "encryption key" in result.output.lower()
    assert store_path.exists() is False


def test_persist_without_filesystem_writes_exits_non_zero(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=False)

    result = _invoke_create_session("--env-file", str(env_file), "--persist")

    assert result.exit_code != 0
    assert "filesystem writes" in result.output.lower()
    assert store_path.exists() is False


def test_persist_with_env_file_key_and_allow_filesystem_writes_creates_encrypted_store_file(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=True)

    result = _invoke_create_session("--env-file", str(env_file), "--persist")

    assert result.exit_code == 0, result.output
    assert store_path.is_file()
    assert store_path.read_bytes() != b""


def test_persistent_store_file_does_not_contain_code_verifier(tmp_path: Path) -> None:
    result, store_path, key = _create_persistent_session(tmp_path)
    session = _load_stored_session(result, store_path, key)

    assert session.code_verifier.value.get_secret_value().encode("utf-8") not in store_path.read_bytes()
    assert b"code_verifier" not in store_path.read_bytes()


def test_persistent_store_file_does_not_contain_state_value_outside_encrypted_bytes(
    tmp_path: Path,
) -> None:
    result, store_path, key = _create_persistent_session(tmp_path)
    session = _load_stored_session(result, store_path, key)

    assert session.state.value.get_secret_value().encode("utf-8") not in store_path.read_bytes()


def test_persistent_store_file_does_not_contain_client_id_in_plaintext(tmp_path: Path) -> None:
    result, store_path, key = _create_persistent_session(tmp_path)
    session = _load_stored_session(result, store_path, key)

    assert session.client_id.value.get_secret_value().encode("utf-8") not in store_path.read_bytes()
    assert CLIENT_ID.encode("utf-8") not in store_path.read_bytes()


def test_persistent_json_warning_says_encrypted_store_was_used(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path)

    assert _json_payload(result)["warning"] == PERSISTENT_WARNING


def test_store_path_override_is_honored(tmp_path: Path) -> None:
    env_store_path = tmp_path / "env" / "oauth_sessions.enc"
    cli_store_path = tmp_path / "cli" / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=env_store_path, allow_writes=True)

    result = _invoke_create_session(
        "--env-file",
        str(env_file),
        "--persist",
        "--store-path",
        str(cli_store_path),
    )

    assert result.exit_code == 0, result.output
    assert cli_store_path.is_file()
    assert env_store_path.exists() is False


def test_allow_filesystem_writes_cli_override_is_honored(tmp_path: Path) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=False)

    result = _invoke_create_session(
        "--env-file",
        str(env_file),
        "--persist",
        "--allow-filesystem-writes",
    )

    assert result.exit_code == 0, result.output
    assert store_path.is_file()


def test_no_allow_filesystem_writes_blocks_persistence_even_if_env_file_enables_writes(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, _key = _write_env_file(tmp_path, store_path=store_path, allow_writes=True)

    result = _invoke_create_session(
        "--env-file",
        str(env_file),
        "--persist",
        "--no-allow-filesystem-writes",
    )

    assert result.exit_code != 0
    assert "filesystem writes" in result.output.lower()
    assert store_path.exists() is False


def test_persistent_plain_output_prints_exactly_two_non_empty_lines(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path, "--plain")
    lines = result.output.strip().splitlines()

    assert len(lines) == 2
    assert all(line != "" for line in lines)


def test_persistent_plain_output_does_not_include_json_braces(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path, "--plain")

    assert "{" not in result.output
    assert "}" not in result.output


def test_persistent_plain_output_does_not_include_code_verifier(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path, "--plain")

    assert "code_verifier" not in result.output


def test_persistent_json_output_does_not_include_code_verifier(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path)

    assert "code_verifier" not in result.output


def test_persistent_json_output_does_not_include_oauth_session_encryption_key(
    tmp_path: Path,
) -> None:
    result, _store_path, key = _create_persistent_session(tmp_path)

    assert "oauth_session_encryption_key" not in result.output
    assert key not in result.output


def test_persistent_json_output_contains_session_id_and_authorization_url(tmp_path: Path) -> None:
    result, _store_path, _key = _create_persistent_session(tmp_path)
    payload = _json_payload(result)

    assert payload["session_id"] != ""
    assert payload["authorization_url"].startswith("https://secure.soundcloud.com/authorize?")


def test_stored_session_can_be_loaded_with_same_key_and_path(tmp_path: Path) -> None:
    result, store_path, key = _create_persistent_session(tmp_path)

    session = _load_stored_session(result, store_path, key)

    assert session is not None


def test_loaded_session_id_matches_cli_output_session_id(tmp_path: Path) -> None:
    result, store_path, key = _create_persistent_session(tmp_path)
    payload = _json_payload(result)
    session = _load_stored_session(result, store_path, key)

    assert session.session_id.value == payload["session_id"]


def test_no_test_performs_network_calls(monkeypatch: Any, tmp_path: Path) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in persistent OAuth CLI tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    result, _store_path, _key = _create_persistent_session(tmp_path)

    assert result.exit_code == 0, result.output


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    result, store_path, _key = _create_persistent_session(tmp_path)

    assert result.exit_code == 0, result.output
    assert store_path.is_relative_to(tmp_path)
    assert store_path.is_file()


def _invoke_create_session(*extra_args: str):
    args = [
        "oauth",
        "create-session",
        "--client-id",
        CLIENT_ID,
        "--redirect-uri",
        REDIRECT_URI,
        *extra_args,
    ]
    return CliRunner().invoke(app, args)


def _create_persistent_session(
    tmp_path: Path,
    *extra_args: str,
) -> tuple[Any, Path, str]:
    store_path = tmp_path / "oauth_sessions.enc"
    env_file, key = _write_env_file(tmp_path, store_path=store_path, allow_writes=True)
    result = _invoke_create_session("--env-file", str(env_file), "--persist", *extra_args)
    assert result.exit_code == 0, result.output
    return result, store_path, key


def _write_env_file(
    tmp_path: Path,
    *,
    store_path: Path | None = None,
    allow_writes: bool,
) -> tuple[Path, str]:
    key = Fernet.generate_key().decode("ascii")
    env_file = tmp_path / ".env.oauth"
    selected_store_path = store_path or tmp_path / "oauth_sessions.enc"
    env_file.write_text(
        "\n".join(
            [
                "SCD_SOUNDCLOUD_AUTH_BASE_URL=https://secure.soundcloud.com",
                f"SCD_OAUTH_SESSION_STORE_PATH={selected_store_path}",
                f"SCD_OAUTH_SESSION_ENCRYPTION_KEY={key}",
                f"SCD_ALLOW_FILESYSTEM_WRITES={str(allow_writes).lower()}",
            ]
        ),
        encoding="utf-8",
    )
    return env_file, key


def _json_payload(result: Any) -> dict[str, Any]:
    return json.loads(result.output)


def _load_stored_session(result: Any, store_path: Path, key: str):
    payload = _json_payload(result)
    settings = AppSettings(
        allow_filesystem_writes=True,
        oauth_session_store_path=store_path,
        oauth_session_encryption_key=SecretStr(key),
    )
    store = EncryptedOAuthAuthorizationSessionStore(settings)
    session = store.get(OAuthSessionId(value=payload["session_id"]))
    assert session is not None
    query = parse_qs(urlparse(payload["authorization_url"]).query)
    assert session.state.value.get_secret_value() == query["state"][0]
    return session
