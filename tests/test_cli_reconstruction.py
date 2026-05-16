import json
from typing import Any

from typer.testing import CliRunner

from soundcloud_downloader.cli.main import app


PLAIN_HLS = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
seg0.aac
#EXT-X-ENDLIST
"""

AES_HLS = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://example.test/key.bin?token=secret"
seg0.aac
"""


def invoke_plan(*args: str) -> dict[str, Any]:
    runner = CliRunner()
    result = runner.invoke(app, ["plan", "evaluate", *args])

    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_version_command_still_works() -> None:
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
            "--requested-profile",
            "original",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["allowed"] is True
    assert payload["decision"] == "allow_original_download"


def test_plan_evaluate_allows_public_original_for_downloadable_progressive_source() -> None:
    payload = invoke_plan(
        "--access-mode",
        "public",
        "--source-protocol",
        "progressive",
        "--source-codec",
        "mp3",
        "--source-container",
        "mp3",
        "--source-downloadable",
        "--source-drm-status",
        "none",
    )

    assert payload["effective_drm_status"] == "none"
    assert payload["source"]["drm_status"] == "none"
    assert payload["policy"]["allowed"] is True
    assert payload["policy"]["decision"] == "allow_original_download"
    assert payload["policy"]["error_code"] is None
    assert payload["policy"]["output_profile"] == "original"


def test_plan_evaluate_allows_public_mp3_128_for_downloadable_source_when_requested() -> None:
    payload = invoke_plan(
        "--access-mode",
        "public",
        "--source-protocol",
        "download",
        "--source-codec",
        "mp3",
        "--source-downloadable",
        "--source-drm-status",
        "none",
        "--requested-profile",
        "mp3_128",
    )

    assert payload["policy"]["allowed"] is True
    assert payload["policy"]["decision"] == "allow_mp3_128_reconstruction"
    assert payload["policy"]["output_profile"] == "mp3_128"


def test_plan_evaluate_denies_public_non_downloadable_source() -> None:
    payload = invoke_plan(
        "--access-mode",
        "public",
        "--source-protocol",
        "progressive",
        "--source-drm-status",
        "none",
    )

    assert payload["policy"]["allowed"] is False
    assert payload["policy"]["decision"] == "deny_source_not_downloadable"
    assert payload["policy"]["error_code"] == "source_not_downloadable"
    assert payload["policy"]["output_profile"] is None


def test_plan_evaluate_public_hls_without_manifest_fails_closed() -> None:
    payload = invoke_plan(
        "--access-mode",
        "public",
        "--source-protocol",
        "hls",
        "--source-codec",
        "aac",
        "--source-container",
        "m4a",
    )

    assert payload["effective_drm_status"] == "unknown"
    assert payload["source"]["drm_status"] == "unknown"
    assert payload["policy"]["allowed"] is False
    assert payload["policy"]["decision"] == "deny_unknown_unsafe"
    assert payload["policy"]["error_code"] == "unknown_unsafe"
    assert payload["warnings"]


def test_plan_evaluate_allows_go_plus_plain_hls_aac_manifest_as_aac_m4a() -> None:
    payload = invoke_plan(
        "--access-mode",
        "go_plus",
        "--authenticated",
        "--has-go-plus",
        "--track-go-plus",
        "--offline-allowed",
        "true",
        "--source-protocol",
        "hls",
        "--source-codec",
        "aac",
        "--source-container",
        "m4a",
        "--manifest-text",
        PLAIN_HLS,
    )

    assert payload["effective_drm_status"] == "none"
    assert payload["hls_analysis"]["drm_status"] == "none"
    assert payload["policy"]["allowed"] is True
    assert payload["policy"]["decision"] == "allow_aac_m4a_remux"
    assert payload["policy"]["error_code"] is None
    assert payload["policy"]["output_profile"] == "aac_m4a"


def test_plan_evaluate_denies_go_plus_aes_128_hls_manifest_with_drm_error() -> None:
    payload = invoke_plan(
        "--access-mode",
        "go_plus",
        "--authenticated",
        "--has-go-plus",
        "--offline-allowed",
        "true",
        "--source-protocol",
        "hls",
        "--source-codec",
        "aac",
        "--manifest-text",
        AES_HLS,
    )

    assert payload["effective_drm_status"] == "encrypted_hls"
    assert payload["policy"]["allowed"] is False
    assert payload["policy"]["decision"] == "deny_drm"
    assert payload["policy"]["error_code"] == "encrypted_stream_unsupported"


def test_plan_evaluate_denies_go_plus_fairplay_widevine_playready_manifest_with_drm_error() -> None:
    for key_format in (
        "com.apple.streamingkeydelivery",
        "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed:widevine",
        "com.microsoft.playready",
    ):
        manifest = f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://asset",KEYFORMAT="{key_format}"
seg0.aac
"""
        payload = invoke_plan(
            "--access-mode",
            "go_plus",
            "--authenticated",
            "--has-go-plus",
            "--offline-allowed",
            "true",
            "--source-protocol",
            "hls",
            "--source-codec",
            "aac",
            "--manifest-text",
            manifest,
        )

        assert payload["effective_drm_status"] == "eme_drm"
        assert payload["policy"]["allowed"] is False
        assert payload["policy"]["decision"] == "deny_drm"
        assert payload["policy"]["error_code"] == "drm_unsupported"


def test_plan_evaluate_denies_go_plus_unauthenticated_context() -> None:
    payload = invoke_plan(
        "--access-mode",
        "go_plus",
        "--source-protocol",
        "download",
        "--source-downloadable",
        "--source-drm-status",
        "none",
    )

    assert payload["policy"]["allowed"] is False
    assert payload["policy"]["decision"] == "deny_auth_required"
    assert payload["policy"]["error_code"] == "auth_required"


def test_plan_evaluate_denies_offline_allowed_false() -> None:
    payload = invoke_plan(
        "--access-mode",
        "go_plus",
        "--authenticated",
        "--has-go-plus",
        "--offline-allowed",
        "false",
        "--source-protocol",
        "download",
        "--source-downloadable",
        "--source-drm-status",
        "none",
    )

    assert payload["policy"]["allowed"] is False
    assert payload["policy"]["decision"] == "deny_rights_restricted"
    assert payload["policy"]["error_code"] == "rights_restricted"


def test_plan_evaluate_rejects_non_hls_source_with_manifest_text() -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "evaluate",
            "--access-mode",
            "public",
            "--source-protocol",
            "download",
            "--manifest-text",
            PLAIN_HLS,
        ],
    )

    assert result.exit_code != 0


def test_plan_evaluate_rejects_invalid_offline_allowed() -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan",
            "evaluate",
            "--access-mode",
            "public",
            "--source-protocol",
            "download",
            "--offline-allowed",
            "maybe",
        ],
    )

    assert result.exit_code != 0


def test_plan_evaluate_output_is_valid_json_with_required_top_level_keys() -> None:
    payload = invoke_plan(
        "--access-mode",
        "public",
        "--source-protocol",
        "download",
        "--source-drm-status",
        "none",
    )

    assert {"source", "effective_drm_status", "policy", "warnings"}.issubset(payload)
