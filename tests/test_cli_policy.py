import json
from typing import Any

from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app


def invoke_policy(*args: str) -> tuple[int, dict[str, Any]]:
    runner = CliRunner()
    result = runner.invoke(app, ["policy", "evaluate", *args])

    assert result.exit_code == 0, result.output
    return result.exit_code, json.loads(result.output)


def test_policy_evaluate_allows_public_original_for_downloadable_source() -> None:
    _, payload = invoke_policy(
        "--access-mode",
        "public",
        "--source-present",
        "--source-protocol",
        "download",
        "--source-codec",
        "mp3",
        "--source-container",
        "original",
        "--source-downloadable",
        "--track-downloadable",
        "--requested-profile",
        "original",
        "--source-drm-status",
        "none",
    )

    assert payload["allowed"] is True
    assert payload["decision"] == "allow_original_download"
    assert payload["error_code"] is None
    assert payload["output_profile"] == "original"


def test_policy_evaluate_allows_public_mp3_128_for_downloadable_source() -> None:
    _, payload = invoke_policy(
        "--access-mode",
        "public",
        "--source-present",
        "--source-protocol",
        "download",
        "--source-codec",
        "mp3",
        "--source-downloadable",
        "--requested-profile",
        "mp3_128",
        "--source-drm-status",
        "none",
    )

    assert payload["allowed"] is True
    assert payload["decision"] == "allow_mp3_128_reconstruction"
    assert payload["error_code"] is None
    assert payload["output_profile"] == "mp3_128"


def test_policy_evaluate_denies_missing_source() -> None:
    _, payload = invoke_policy("--access-mode", "public")

    assert payload["allowed"] is False
    assert payload["decision"] == "deny_source_not_downloadable"
    assert payload["error_code"] == "source_not_downloadable"
    assert payload["output_profile"] is None


def test_policy_evaluate_denies_unknown_drm() -> None:
    _, payload = invoke_policy(
        "--access-mode",
        "public",
        "--source-present",
    )

    assert payload["allowed"] is False
    assert payload["decision"] == "deny_unknown_unsafe"
    assert payload["error_code"] == "unknown_unsafe"
    assert payload["output_profile"] is None


def test_policy_evaluate_allows_go_plus_aac_m4a_for_hls_aac_non_drm_source() -> None:
    _, payload = invoke_policy(
        "--access-mode",
        "go_plus",
        "--source-present",
        "--source-protocol",
        "hls",
        "--source-codec",
        "aac",
        "--source-container",
        "m4a",
        "--source-drm-status",
        "none",
        "--authenticated",
        "--has-go-plus",
        "--offline-allowed",
        "true",
        "--requested-profile",
        "aac_m4a",
    )

    assert payload["allowed"] is True
    assert payload["decision"] == "allow_aac_m4a_remux"
    assert payload["error_code"] is None
    assert payload["output_profile"] == "aac_m4a"


def test_policy_evaluate_offline_allowed_false_denies_go_plus_reconstruction() -> None:
    _, payload = invoke_policy(
        "--access-mode",
        "go_plus",
        "--source-present",
        "--source-protocol",
        "hls",
        "--source-codec",
        "aac",
        "--source-drm-status",
        "none",
        "--authenticated",
        "--has-go-plus",
        "--offline-allowed",
        "false",
        "--requested-profile",
        "aac_m4a",
    )

    assert payload["allowed"] is False
    assert payload["decision"] == "deny_rights_restricted"
    assert payload["error_code"] == "rights_restricted"
    assert payload["output_profile"] is None


def test_policy_evaluate_invalid_offline_allowed_exits_non_zero() -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "policy",
            "evaluate",
            "--access-mode",
            "public",
            "--offline-allowed",
            "maybe",
        ],
    )

    assert result.exit_code != 0


def test_policy_evaluate_output_is_valid_json() -> None:
    _, payload = invoke_policy("--access-mode", "public")

    assert set(payload) == {
        "allowed",
        "decision",
        "error_code",
        "output_profile",
        "reason",
    }
