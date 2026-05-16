from soundcloud_downloader.infrastructure.observability import (
    REDACTED_VALUE,
    is_sensitive_field,
    redact_event_dict,
    redact_mapping,
    redact_url,
    redact_value,
)


def test_is_sensitive_field_detects_required_names() -> None:
    for name in (
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "x-api-key",
    ):
        assert is_sensitive_field(name) is True


def test_is_sensitive_field_is_case_insensitive() -> None:
    assert is_sensitive_field("Authorization") is True
    assert is_sensitive_field("ACCESS_TOKEN") is True


def test_is_sensitive_field_handles_hyphen_underscore_and_token_suffixes() -> None:
    assert is_sensitive_field("access-token") is True
    assert is_sensitive_field("x_api_key") is True
    assert is_sensitive_field("session_token") is True
    assert is_sensitive_field("session-token") is True


def test_redact_value_always_returns_redacted_value() -> None:
    assert redact_value("secret-token") == REDACTED_VALUE
    assert redact_value(None) == REDACTED_VALUE
    assert redact_value(123) == REDACTED_VALUE


def test_redact_mapping_redacts_sensitive_top_level_keys() -> None:
    redacted = redact_mapping({"authorization": "Bearer token", "safe": "value"})

    assert redacted["authorization"] == REDACTED_VALUE
    assert redacted["safe"] == "value"


def test_redact_mapping_redacts_nested_mappings() -> None:
    redacted = redact_mapping(
        {
            "headers": {
                "Authorization": "Bearer token",
                "content-type": "application/json",
            }
        }
    )

    assert redacted["headers"] == {
        "Authorization": REDACTED_VALUE,
        "content-type": "application/json",
    }


def test_redact_mapping_redacts_mappings_inside_lists() -> None:
    redacted = redact_mapping(
        {
            "attempts": [
                {"access_token": "token-1"},
                {"value": "safe"},
            ]
        }
    )

    assert redacted["attempts"] == [
        {"access_token": REDACTED_VALUE},
        {"value": "safe"},
    ]


def test_redact_mapping_does_not_mutate_original_mapping() -> None:
    original = {"headers": {"Authorization": "Bearer token"}}

    redacted = redact_mapping(original)

    assert original == {"headers": {"Authorization": "Bearer token"}}
    assert redacted == {"headers": {"Authorization": REDACTED_VALUE}}


def test_redact_mapping_preserves_non_sensitive_scalar_values() -> None:
    redacted = redact_mapping({"status": 200, "retry": False, "duration": 1.25})

    assert redacted == {"status": 200, "retry": False, "duration": 1.25}


def test_redact_url_strips_query_string_and_fragment() -> None:
    redacted = redact_url("https://example.test/path/manifest.m3u8?token=secret#frag")

    assert redacted == "https://example.test/path/manifest.m3u8"


def test_redact_url_does_not_expose_signed_query_parameters() -> None:
    redacted = redact_url("https://cdn.example.test/audio.aac?Signature=secret&Expires=123")

    assert "Signature" not in redacted
    assert "secret" not in redacted
    assert "Expires" not in redacted


def test_redact_event_dict_redacts_sensitive_fields() -> None:
    event = {
        "event": "request",
        "access_token": "secret-token",
        "headers": {"Authorization": "Bearer secret-token"},
    }

    redacted = redact_event_dict(None, "info", event)

    assert redacted is event
    assert redacted["access_token"] == REDACTED_VALUE
    assert redacted["headers"] == {"Authorization": REDACTED_VALUE}
