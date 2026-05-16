import json
from typing import Any

from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app


def invoke_resolver(value: str) -> dict[str, Any]:
    result = CliRunner().invoke(app, ["resolver", "inspect", value])

    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_version_command_still_exits_zero() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_policy_evaluate_command_still_works() -> None:
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

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["allowed"] is True


def test_plan_evaluate_command_still_works() -> None:
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

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["policy"]["allowed"] is True


def test_resolver_inspect_classifies_track_url() -> None:
    payload = invoke_resolver("https://soundcloud.com/user/track")

    assert payload["normalized"]["input_type"] == "url"
    assert payload["normalized"]["resource_type"] == "track"
    assert payload["normalized"]["host"] == "soundcloud.com"
    assert payload["resolved"] is False
    assert payload["requires_network_resolution"] is True


def test_resolver_inspect_classifies_playlist_url() -> None:
    payload = invoke_resolver("https://soundcloud.com/user/sets/playlist")

    assert payload["normalized"]["resource_type"] == "playlist"
    assert payload["requires_network_resolution"] is True


def test_resolver_inspect_classifies_user_url() -> None:
    payload = invoke_resolver("https://soundcloud.com/user")

    assert payload["normalized"]["resource_type"] == "user"
    assert payload["requires_network_resolution"] is True


def test_resolver_inspect_classifies_shortlink_url() -> None:
    payload = invoke_resolver("https://on.soundcloud.com/abc123")

    assert payload["normalized"]["resource_type"] == "shortlink"
    assert payload["normalized"]["host"] == "on.soundcloud.com"
    assert payload["requires_network_resolution"] is True


def test_resolver_inspect_marks_path_resources_as_requiring_network_resolution() -> None:
    for value in (
        "https://soundcloud.com/user",
        "https://soundcloud.com/user/track",
        "https://soundcloud.com/user/sets/playlist",
        "https://on.soundcloud.com/abc123",
    ):
        payload = invoke_resolver(value)
        assert payload["requires_network_resolution"] is True


def test_resolver_inspect_classifies_raw_text() -> None:
    payload = invoke_resolver("artist track name")

    assert payload["normalized"]["input_type"] == "raw_text"
    assert payload["normalized"]["resource_type"] == "unknown"
    assert payload["resolved"] is False
    assert payload["requires_network_resolution"] is False


def test_resolver_inspect_classifies_unsupported_host_with_warning() -> None:
    payload = invoke_resolver("https://example.test/user/track?token=secret")

    assert payload["normalized"]["resource_type"] == "unknown"
    assert payload["normalized"]["host"] == "example.test"
    assert payload["requires_network_resolution"] is False
    assert payload["warnings"]


def test_resolver_inspect_strips_query_strings_and_fragments() -> None:
    payload = invoke_resolver("https://soundcloud.com/user/sets/playlist?token=secret#fragment")

    assert payload["normalized"]["normalized_url"] == "https://soundcloud.com/user/sets/playlist"
    assert "?" not in payload["normalized"]["normalized_url"]
    assert "#" not in payload["normalized"]["normalized_url"]


def test_resolver_inspect_output_does_not_contain_raw_query_token_values() -> None:
    result = CliRunner().invoke(
        app,
        ["resolver", "inspect", "https://soundcloud.com/user/track?token=raw-secret"],
    )

    assert result.exit_code == 0
    assert "raw-secret" not in result.output


def test_resolver_inspect_output_does_not_contain_fragment_values() -> None:
    result = CliRunner().invoke(
        app,
        ["resolver", "inspect", "https://soundcloud.com/user/track#raw-fragment"],
    )

    assert result.exit_code == 0
    assert "raw-fragment" not in result.output


def test_resolver_inspect_rejects_whitespace_only_value() -> None:
    result = CliRunner().invoke(app, ["resolver", "inspect", "   "])

    assert result.exit_code != 0


def test_resolver_inspect_output_is_valid_json() -> None:
    payload = invoke_resolver("https://soundcloud.com/user/track")

    assert payload["normalized"]["resource_type"] == "track"


def test_resolver_inspect_json_contains_required_top_level_keys() -> None:
    payload = invoke_resolver("https://soundcloud.com/user/track")

    assert {"normalized", "resolved", "requires_network_resolution", "warnings"}.issubset(
        payload
    )
