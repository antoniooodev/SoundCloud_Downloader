import json
import socket
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app


IN_MEMORY_WARNING = (
    "This session is stored in memory only and will not survive process exit. "
    "Persistent secure storage will be implemented in a later task."
)


def test_existing_version_command_still_exits_zero() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0


def test_existing_oauth_authorize_url_command_still_works() -> None:
    result = CliRunner().invoke(
        app,
        [
            "oauth",
            "authorize-url",
            "--client-id",
            "example-client-id",
            "--redirect-uri",
            "http://localhost:8765/callback",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "authorization_url" in json.loads(result.output)


def test_oauth_create_session_exits_zero_with_client_id_and_localhost_redirect_uri() -> None:
    result = _invoke_create_session()

    assert result.exit_code == 0, result.output


def test_json_output_is_valid_json() -> None:
    payload = _session_payload()

    assert isinstance(payload, dict)


def test_json_output_contains_session_id() -> None:
    payload = _session_payload()

    assert payload["session_id"] != ""


def test_json_output_contains_authorization_url() -> None:
    payload = _session_payload()

    assert payload["authorization_url"] != ""


def test_json_output_contains_expires_at() -> None:
    payload = _session_payload()

    assert payload["expires_at"] != ""


def test_json_output_status_is_pending() -> None:
    payload = _session_payload()

    assert payload["status"] == "pending"


def test_json_output_does_not_include_code_verifier_required_for_token_exchange() -> None:
    payload = _session_payload()

    assert "code_verifier_required_for_token_exchange" not in payload


def test_json_output_contains_in_memory_warning() -> None:
    payload = _session_payload()

    assert payload["warning"] == IN_MEMORY_WARNING


def test_authorization_url_host_and_path_use_default_soundcloud_authorize() -> None:
    parsed = _parsed_authorization_url()

    assert parsed.netloc == "secure.soundcloud.com"
    assert parsed.path == "/authorize"


def test_authorization_url_includes_response_type_code() -> None:
    query = _authorization_query()

    assert query["response_type"] == ["code"]


def test_authorization_url_includes_code_challenge_method_s256() -> None:
    query = _authorization_query()

    assert query["code_challenge_method"] == ["S256"]


def test_authorization_url_includes_code_challenge() -> None:
    query = _authorization_query()

    assert query["code_challenge"][0] != ""


def test_authorization_url_includes_state() -> None:
    query = _authorization_query()

    assert query["state"][0] != ""


def test_authorization_url_includes_redirect_uri() -> None:
    query = _authorization_query()

    assert unquote(query["redirect_uri"][0]) == "http://localhost:8765/callback"


def test_json_output_does_not_include_code_verifier() -> None:
    payload = _session_payload()

    assert "code_verifier" not in payload


def test_json_output_does_not_include_access_token_or_refresh_token() -> None:
    result = _invoke_create_session()

    assert "access_token" not in result.output
    assert "refresh_token" not in result.output


def test_json_output_does_not_include_client_secret() -> None:
    result = _invoke_create_session()

    assert "client_secret" not in result.output


def test_plain_output_prints_exactly_two_non_empty_lines() -> None:
    lines = _plain_output_lines()

    assert len(lines) == 2
    assert all(line != "" for line in lines)


def test_plain_output_first_line_is_session_id_like_non_empty_text() -> None:
    lines = _plain_output_lines()

    assert lines[0] != ""
    assert "://" not in lines[0]


def test_plain_output_second_line_is_authorization_url() -> None:
    lines = _plain_output_lines()

    assert lines[1].startswith("https://secure.soundcloud.com/authorize?")


def test_plain_output_does_not_print_json_object() -> None:
    output = _invoke_create_session("--plain").output

    assert "{" not in output
    assert "}" not in output


def test_plain_output_does_not_include_code_verifier() -> None:
    output = _invoke_create_session("--plain").output

    assert "code_verifier" not in output


def test_auth_base_url_override_is_honored() -> None:
    parsed = _parsed_authorization_url("--auth-base-url", "https://auth.example.test/oauth")

    assert parsed.netloc == "auth.example.test"
    assert parsed.path == "/oauth/authorize"


def test_env_file_loads_auth_base_url_from_explicit_tmp_path_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.oauth"
    env_file.write_text("SCD_SOUNDCLOUD_AUTH_BASE_URL=https://env.example.test/oauth\n")

    parsed = _parsed_authorization_url("--env-file", str(env_file))

    assert parsed.netloc == "env.example.test"
    assert parsed.path == "/oauth/authorize"


def test_auth_base_url_override_takes_precedence_over_env_file_value(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.oauth"
    env_file.write_text("SCD_SOUNDCLOUD_AUTH_BASE_URL=https://env.example.test/oauth\n")

    parsed = _parsed_authorization_url(
        "--env-file",
        str(env_file),
        "--auth-base-url",
        "https://override.example.test/base",
    )

    assert parsed.netloc == "override.example.test"
    assert parsed.path == "/base/authorize"


def test_invalid_verifier_length_exits_non_zero() -> None:
    result = _invoke_create_session("--verifier-length", "42")

    assert result.exit_code != 0


def test_invalid_ttl_seconds_exits_non_zero() -> None:
    result = _invoke_create_session("--ttl-seconds", "0")

    assert result.exit_code != 0


def test_redirect_uri_with_fragment_exits_non_zero() -> None:
    result = _invoke_create_session("--redirect-uri", "http://localhost:8765/callback#fragment")

    assert result.exit_code != 0


def test_auth_base_url_with_query_exits_non_zero() -> None:
    result = _invoke_create_session("--auth-base-url", "https://secure.soundcloud.com?bad=true")

    assert result.exit_code != 0


def test_no_test_performs_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network calls are not allowed in OAuth session CLI tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    result = _invoke_create_session()

    assert result.exit_code == 0, result.output


def test_no_test_writes_files_except_explicit_tmp_path_env_file(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth session CLI tests.")

    monkeypatch.setattr("pathlib.Path.write_text", fail_file_write)
    monkeypatch.setattr("pathlib.Path.write_bytes", fail_file_write)

    result = _invoke_create_session()

    assert result.exit_code == 0, result.output


def _invoke_create_session(*extra_args: str):
    args = [
        "oauth",
        "create-session",
        "--client-id",
        "example-client-id",
        "--redirect-uri",
        "http://localhost:8765/callback",
        *extra_args,
    ]
    return CliRunner().invoke(app, args)


def _session_payload(*extra_args: str) -> dict[str, Any]:
    result = _invoke_create_session(*extra_args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _parsed_authorization_url(*extra_args: str):
    return urlparse(_session_payload(*extra_args)["authorization_url"])


def _authorization_query(*extra_args: str) -> dict[str, list[str]]:
    return parse_qs(_parsed_authorization_url(*extra_args).query)


def _plain_output_lines() -> list[str]:
    result = _invoke_create_session("--plain")
    assert result.exit_code == 0, result.output
    return result.output.strip().splitlines()
