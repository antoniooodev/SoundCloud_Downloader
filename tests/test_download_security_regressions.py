import logging
from pathlib import Path

import pytest

from tests.test_e2e_download_pipeline import (
    E2EHttpTransport,
    RAW_ACCESS,
    RAW_REFRESH,
    REFRESHED_ACCESS,
    SEGMENT_URLS,
    STREAM_URL,
    TRANSCODING_URL,
    _common_args,
    encrypted_media_manifest,
    install_test_doubles,
    invoke,
    prepared_env,
)


RAW_ACCESS_TOKEN_SHOULD_NOT_LEAK = RAW_ACCESS
RAW_REFRESH_TOKEN_SHOULD_NOT_LEAK = RAW_REFRESH
RAW_CLIENT_SECRET_SHOULD_NOT_LEAK = "raw-client-secret-should-not-leak"
RAW_SEGMENT_URL_SHOULD_NOT_LEAK = SEGMENT_URLS[0]
RAW_TRANSCODING_URL_SHOULD_NOT_LEAK = TRANSCODING_URL
RAW_STREAM_URL_SHOULD_NOT_LEAK = STREAM_URL
RAW_MANIFEST_URL_SHOULD_NOT_LEAK = STREAM_URL
UNSAFE_QUERY_URL = "https://media.example.test/playlist.m3u8?access_token=raw"


def _all_forbidden_strings(env_key: str) -> tuple[str, ...]:
    return (
        RAW_ACCESS_TOKEN_SHOULD_NOT_LEAK,
        RAW_REFRESH_TOKEN_SHOULD_NOT_LEAK,
        REFRESHED_ACCESS,
        RAW_CLIENT_SECRET_SHOULD_NOT_LEAK,
        env_key,
        RAW_TRANSCODING_URL_SHOULD_NOT_LEAK,
        RAW_STREAM_URL_SHOULD_NOT_LEAK,
        RAW_MANIFEST_URL_SHOULD_NOT_LEAK,
        *SEGMENT_URLS,
    )


# ---- Success-path redaction tests ----


def test_cli_success_output_does_not_leak_secrets_or_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code == 0, output
    for forbidden in _all_forbidden_strings(key):
        assert forbidden not in output


@pytest.mark.parametrize(
    "needle",
    [
        RAW_ACCESS_TOKEN_SHOULD_NOT_LEAK,
        RAW_REFRESH_TOKEN_SHOULD_NOT_LEAK,
        RAW_CLIENT_SECRET_SHOULD_NOT_LEAK,
        RAW_TRANSCODING_URL_SHOULD_NOT_LEAK,
        RAW_STREAM_URL_SHOULD_NOT_LEAK,
        RAW_MANIFEST_URL_SHOULD_NOT_LEAK,
        RAW_SEGMENT_URL_SHOULD_NOT_LEAK,
    ],
)
def test_cli_success_output_does_not_contain_specific_sensitive_string(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    needle: str,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert needle not in output


def test_cli_success_output_does_not_contain_token_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code == 0
    assert key not in output


# ---- Error-path redaction tests ----


def test_cli_error_output_does_not_leak_secrets_on_drm_denial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, key = prepared_env(tmp_path)
    drm_manifest = encrypted_media_manifest(SEGMENT_URLS, key_method="AES-128")
    transport = E2EHttpTransport(manifest_body=drm_manifest)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    for forbidden in _all_forbidden_strings(key):
        assert forbidden not in output
    assert drm_manifest not in output


def test_cli_error_output_does_not_contain_segment_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(segment_failure_index=1)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    for segment_bytes in (b"seg-zero-bytes", b"seg-one-bytes", b"seg-two-bytes"):
        assert segment_bytes.decode() not in output


def test_cli_error_output_does_not_contain_manifest_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    drm_manifest = encrypted_media_manifest(SEGMENT_URLS, key_method="AES-128")
    transport = E2EHttpTransport(manifest_body=drm_manifest)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "license.example.test" not in output
    assert "#EXT-X-KEY" not in output


# ---- caplog redaction tests ----


def test_caplog_does_not_leak_secrets_in_success_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file, _store, key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    with caplog.at_level(logging.DEBUG):
        exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    log_text = caplog.text
    for forbidden in _all_forbidden_strings(key):
        assert forbidden not in log_text


def test_caplog_does_not_leak_secrets_in_error_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file, _store, key = prepared_env(tmp_path)
    drm_manifest = encrypted_media_manifest(SEGMENT_URLS, key_method="AES-128")
    transport = E2EHttpTransport(manifest_body=drm_manifest)
    install_test_doubles(monkeypatch, transport)

    with caplog.at_level(logging.DEBUG):
        exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code != 0
    log_text = caplog.text
    for forbidden in _all_forbidden_strings(key):
        assert forbidden not in log_text
    assert drm_manifest not in log_text


def test_caplog_does_not_contain_raw_temp_paths_when_ffmpeg_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    runner = install_test_doubles(monkeypatch, transport)

    with caplog.at_level(logging.DEBUG):
        exit_code, _output = invoke(*_common_args(env_file))

    assert exit_code == 0
    raw_paths_in_commands = [
        arg
        for command in runner.commands
        for arg in command.args
        if "/" in arg and arg != "/"
    ]
    if raw_paths_in_commands:
        for raw_path in raw_paths_in_commands:
            assert raw_path not in caplog.text


# ---- URL validation tests ----


def test_invalid_source_url_with_userinfo_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(
        "https://user:pass@soundcloud.com/artist/track",
        "--env-file",
        str(env_file),
        "--access-mode",
        "go_plus",
        "--output-profile",
        "aac_m4a",
        "--format",
        "m4a",
    )

    assert exit_code != 0
    assert "user:pass" not in output


def test_source_url_with_access_token_query_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport()
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(
        "https://soundcloud.com/artist/track?access_token=raw-leak-token",
        "--env-file",
        str(env_file),
        "--access-mode",
        "go_plus",
        "--output-profile",
        "aac_m4a",
        "--format",
        "m4a",
    )

    assert exit_code != 0
    assert "raw-leak-token" not in output


def test_transcoding_endpoint_payload_with_unsafe_url_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    transport = E2EHttpTransport(stream_url=UNSAFE_QUERY_URL)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "Track download failed." in output
    assert UNSAFE_QUERY_URL not in output
    assert "raw" not in _strip_known_safe(output)


def test_manifest_text_with_unsafe_segment_url_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    unsafe_segment = "https://cdn.example.test/seg.ts?access_token=raw-leak"
    unsafe_manifest = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        "#EXTINF:10.0,\n"
        f"{unsafe_segment}\n"
        "#EXT-X-ENDLIST\n"
    )
    transport = E2EHttpTransport(manifest_body=unsafe_manifest)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert unsafe_segment not in output
    assert "raw-leak" not in output


def test_transcoding_response_with_userinfo_in_stream_url_is_rejected_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file, _store, _key = prepared_env(tmp_path)
    bad_stream_url = "https://user:pass@cdn.example.test/playlist.m3u8"
    transport = E2EHttpTransport(stream_url=bad_stream_url)
    install_test_doubles(monkeypatch, transport)

    exit_code, output = invoke(*_common_args(env_file))

    assert exit_code != 0
    assert "user:pass" not in output
    assert bad_stream_url not in output


# ---- helpers ----


def _strip_known_safe(text: str) -> str:
    return text.replace("aac_m4a", "").replace("aac_0_1", "")
