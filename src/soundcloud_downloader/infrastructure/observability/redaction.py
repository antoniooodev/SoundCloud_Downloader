from collections.abc import Mapping, MutableMapping
from urllib.parse import urlsplit, urlunsplit

REDACTED_VALUE = "[REDACTED]"

SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "client_secret",
        "password",
        "secret",
        "api_key",
        "x-api-key",
        "signature",
        "signed_url",
        "manifest_url",
        "stream_url",
        "license_url",
    }
)


def is_sensitive_field(name: str) -> bool:
    normalized = _normalize_field_name(name)
    variants = {normalized, normalized.replace("-", "_"), normalized.replace("_", "-")}
    if variants & SENSITIVE_FIELD_NAMES:
        return True
    if normalized.endswith(("_token", "-token")):
        return True
    return any(marker in normalized for marker in ("secret", "password", "cookie"))


def redact_value(value: object) -> str:
    return REDACTED_VALUE


def redact_mapping(mapping: Mapping[str, object]) -> dict[str, object]:
    return {key: _redact_item(key, value) for key, value in mapping.items()}


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return REDACTED_VALUE

    if parsed.scheme and not parsed.netloc:
        return REDACTED_VALUE
    if not parsed.scheme and not parsed.netloc and not parsed.path:
        return REDACTED_VALUE
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def redact_event_dict(
    _logger: object,
    _method_name: str,
    event_dict: MutableMapping[str, object],
) -> MutableMapping[str, object]:
    for key, value in tuple(event_dict.items()):
        event_dict[key] = _redact_item(key, value)
    return event_dict


def _redact_item(key: str, value: object) -> object:
    if is_sensitive_field(key):
        return redact_value(value)
    if _looks_like_sensitive_url_key(key) and isinstance(value, str):
        return redact_url(value)
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_list_item(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_list_item(item) for item in value)
    return value


def _redact_list_item(value: object) -> object:
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_list_item(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_list_item(item) for item in value)
    return value


def _looks_like_sensitive_url_key(key: str) -> bool:
    normalized = _normalize_field_name(key)
    return normalized.endswith("url") or "url" in normalized


def _normalize_field_name(name: str) -> str:
    return "-".join(name.strip().lower().replace("_", "-").split())
