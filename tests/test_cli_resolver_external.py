import json
from pathlib import Path
from typing import Any

import httpx
from typer.testing import CliRunner

from soundcloud_downloader.cli import resolver as resolver_cli
from soundcloud_downloader.cli.main import app
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.http import SafeAsyncHttpClient


def invoke_resolver(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["resolver", "inspect", *args])
    return result.exit_code, result.output


def parse_output(output: str) -> dict[str, Any]:
    return json.loads(output)


def valid_track_payload() -> str:
    return json.dumps(
        {
            "status": "resolved",
            "kind": "track",
            "track": {
                "soundcloud_id": "123",
                "title": "External Track",
                "is_public": True,
                "is_downloadable": True,
            },
        }
    )


def patch_http_client(
    monkeypatch,
    handler,
) -> None:
    def build_client(settings: AppSettings) -> SafeAsyncHttpClient:
        return SafeAsyncHttpClient(settings=settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(resolver_cli, "build_safe_http_client", build_client)


def test_resolver_inspect_without_external_keeps_offline_behavior() -> None:
    exit_code, output = invoke_resolver("https://soundcloud.com/user/track")

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["resolved"] is False
    assert payload["resolved_resource"] is None
    assert payload["requires_network_resolution"] is True


def test_resolver_inspect_external_fails_when_network_is_not_allowed() -> None:
    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    assert exit_code != 0
    assert "requires network access" in output


def test_resolver_inspect_external_fails_when_endpoint_is_missing() -> None:
    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
    )

    assert exit_code != 0
    assert "requires an explicit resolve endpoint" in output


def test_resolver_inspect_external_with_overrides_uses_external_mode(monkeypatch) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text=valid_track_payload(), request=request)

    patch_http_client(monkeypatch, handler)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    payload = parse_output(output)
    assert exit_code == 0
    assert called is True
    assert payload["resolved"] is True
    assert payload["resolved_resource"]["status"] == "resolved"


def test_external_mode_successful_mocked_response_returns_resolved_true(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, text=valid_track_payload(), request=request),
    )

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
    assert payload["requires_network_resolution"] is False
    assert payload["resolved_resource"]["kind"] == "track"


def test_external_mode_404_returns_not_found_resource(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(404, request=request),
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/missing",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["resolved"] is False
    assert payload["resolved_resource"]["status"] == "not_found"


def test_external_mode_invalid_json_returns_error_resource(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, text="{", request=request),
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["resolved"] is False
    assert payload["resolved_resource"]["status"] == "error"


def test_external_mode_output_does_not_contain_configured_endpoint(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, text=valid_track_payload(), request=request),
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    assert exit_code == 0
    assert "resolver.example.invalid" not in output


def test_external_mode_output_strips_input_query_token_and_fragment(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, text=valid_track_payload(), request=request),
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track?token=secret-value#fragment-value",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    payload = parse_output(output)
    assert exit_code == 0
    assert payload["normalized"]["normalized_url"] == "https://soundcloud.com/user/track"
    assert "secret-value" not in output
    assert "fragment-value" not in output


def test_external_mode_output_omits_forbidden_secret_and_stream_fields(monkeypatch) -> None:
    patch_http_client(
        monkeypatch,
        lambda request: httpx.Response(200, text=valid_track_payload(), request=request),
    )

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    assert exit_code == 0
    for forbidden in (
        "stream_url",
        "manifest_url",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
    ):
        assert forbidden not in output.lower()


def test_external_mode_does_not_add_authorization_or_cookie_headers(monkeypatch) -> None:
    seen_headers: httpx.Headers | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_headers
        seen_headers = request.headers
        return httpx.Response(200, text=valid_track_payload(), request=request)

    patch_http_client(monkeypatch, handler)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--allow-network",
        "--resolve-endpoint",
        "https://resolver.example.invalid/resolve",
    )

    assert exit_code == 0, output
    assert seen_headers is not None
    assert "authorization" not in seen_headers
    assert "cookie" not in seen_headers


def test_env_file_loads_external_resolver_settings(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "\n".join(
            [
                "SCD_ALLOW_NETWORK=true",
                "SCD_SOUNDCLOUD_RESOLVE_ENDPOINT=https://env.example.invalid/resolve",
            ]
        ),
        encoding="utf-8",
    )
    seen_url: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, text=valid_track_payload(), request=request)

    patch_http_client(monkeypatch, handler)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--env-file",
        str(env_file),
    )

    assert exit_code == 0, output
    assert seen_url is not None
    assert seen_url.startswith("https://env.example.invalid/resolve")


def test_resolve_endpoint_override_takes_precedence_over_env_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "settings.env"
    env_file.write_text(
        "\n".join(
            [
                "SCD_ALLOW_NETWORK=true",
                "SCD_SOUNDCLOUD_RESOLVE_ENDPOINT=https://env.example.invalid/resolve",
            ]
        ),
        encoding="utf-8",
    )
    seen_url: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, text=valid_track_payload(), request=request)

    patch_http_client(monkeypatch, handler)

    exit_code, output = invoke_resolver(
        "https://soundcloud.com/user/track",
        "--external",
        "--env-file",
        str(env_file),
        "--resolve-endpoint",
        "https://override.example.invalid/resolve",
    )

    assert exit_code == 0, output
    assert seen_url is not None
    assert seen_url.startswith("https://override.example.invalid/resolve")


def test_existing_policy_cli_still_works() -> None:
    result = CliRunner().invoke(
        app,
        [
            "policy",
            "evaluate",
            "--access-mode",
            "public",
            "--source-present",
            "--source-protocol",
            "download",
            "--source-downloadable",
            "--source-drm-status",
            "none",
        ],
    )

    payload = parse_output(result.output)
    assert result.exit_code == 0
    assert payload["allowed"] is True


def test_existing_plan_cli_still_works() -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "evaluate",
            "--access-mode",
            "public",
            "--source-protocol",
            "download",
            "--source-downloadable",
            "--source-drm-status",
            "none",
        ],
    )

    payload = parse_output(result.output)
    assert result.exit_code == 0
    assert payload["policy"]["allowed"] is True
