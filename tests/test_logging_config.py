import logging

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.infrastructure.observability import (
    REDACTED_VALUE,
    configure_logging,
    get_logger,
)


def test_configure_logging_accepts_default_settings() -> None:
    configure_logging(AppSettings())


def test_configure_logging_can_be_called_twice() -> None:
    settings = AppSettings()

    configure_logging(settings)
    configure_logging(settings)


def test_get_logger_returns_usable_logger_object() -> None:
    configure_logging(AppSettings())

    logger = get_logger("soundcloud_downloader.tests")

    assert hasattr(logger, "info")


def test_emitted_structured_logs_redact_sensitive_fields(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(AppSettings())
    logger = get_logger("soundcloud_downloader.tests.redaction")

    logger.info(
        "request_started",
        access_token="raw-access-token",
        headers={"Authorization": "Bearer raw-authorization-token"},
        track_id="track-1",
    )
    logging.shutdown()
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "raw-access-token" not in output
    assert "raw-authorization-token" not in output
    assert "track-1" in output


def test_emitted_structured_logs_do_not_contain_access_token_values(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(AppSettings())
    logger = get_logger("soundcloud_downloader.tests.token")

    logger.info("token_seen", access_token="access-token-value")
    logging.shutdown()
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "access-token-value" not in output


def test_emitted_structured_logs_do_not_contain_authorization_header_values(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging(AppSettings())
    logger = get_logger("soundcloud_downloader.tests.authorization")

    logger.info("headers_seen", headers={"Authorization": "Bearer authorization-value"})
    logging.shutdown()
    output = capsys.readouterr().err

    assert REDACTED_VALUE in output
    assert "authorization-value" not in output


def test_emitted_structured_logs_do_not_write_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    before = set(tmp_path.iterdir())

    configure_logging(AppSettings())
    get_logger("soundcloud_downloader.tests.files").info("no_file_logging")
    logging.shutdown()

    assert set(tmp_path.iterdir()) == before
