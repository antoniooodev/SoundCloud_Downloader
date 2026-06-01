import asyncio
import logging
from collections.abc import Mapping
from enum import Enum
from types import TracebackType
from urllib.parse import parse_qsl, urljoin, urlparse

import httpx

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError
from soundcloud_downloader.infrastructure.http.models import HttpMethod, HttpRequest, HttpResponse
from soundcloud_downloader.infrastructure.observability import (
    get_logger,
    redact_mapping,
    redact_url,
)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_SENSITIVE_FORM_FIELD_NAMES = frozenset({"code", "code_verifier", "client_secret"})
_SENSITIVE_REDIRECT_QUERY_KEYS = frozenset(
    {"access_token", "refresh_token", "client_secret", "authorization", "cookie", "set-cookie"}
)
_REDACTED_FORM_VALUE = "[REDACTED]"


class HttpRequestFailureKind(str, Enum):
    HOST_NOT_ALLOWED = "host_not_allowed"
    REDIRECT_REJECTED = "redirect_rejected"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


class NetworkDisabledError(SoundcloudDownloaderError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.NETWORK_PERMANENT,
            "Network access is disabled by application settings.",
        )


class HttpRequestError(SoundcloudDownloaderError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        failure_kind: HttpRequestFailureKind = HttpRequestFailureKind.UNKNOWN,
        redirect_count: int | None = None,
        allowed_host: bool | None = None,
    ) -> None:
        self.status_code = status_code
        self.failure_kind = failure_kind
        self.redirect_count = redirect_count
        self.allowed_host = allowed_host
        super().__init__(code, message)


class SafeAsyncHttpClient:
    def __init__(
        self,
        settings: AppSettings,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        if client is not None:
            self._client = client
        elif transport is not None:
            self._client = httpx.AsyncClient(transport=transport)
        else:
            self._client = httpx.AsyncClient()
        self._logger = get_logger(__name__)

    async def request(self, request: HttpRequest) -> HttpResponse:
        if not self._settings.allow_network:
            raise NetworkDisabledError()

        timeout_seconds = request.timeout_seconds or self._settings.http_timeout_seconds
        max_attempts = 1 + self._settings.http_max_retries
        url_redacted = redact_url(request.url)
        redirect_count = 0
        current_method = request.method.value
        current_url = request.url
        current_headers = dict(request.headers)
        current_params = dict(request.params) if request.params else None
        current_json = dict(request.json_body) if request.json_body is not None else None
        current_data = dict(request.form_data) if request.form_data is not None else None
        attempt = 1

        while True:
            self._log_request_started(request, url_redacted, attempt)
            try:
                response = await self._client.request(
                    current_method,
                    current_url,
                    headers=current_headers,
                    params=current_params,
                    json=current_json,
                    data=current_data,
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                if attempt >= max_attempts:
                    self._logger.warning(
                        "http_request_failed",
                        url=url_redacted,
                        method=request.method.value,
                        attempt=attempt,
                        error_type=type(exc).__name__,
                    )
                    raise HttpRequestError(
                        ErrorCode.NETWORK_RETRYABLE,
                        f"HTTP request failed after {attempt} attempts for {url_redacted}.",
                        failure_kind=HttpRequestFailureKind.TIMEOUT,
                    ) from exc
                await self._schedule_retry(request, url_redacted, attempt, type(exc).__name__)
                attempt += 1
                continue
            except httpx.NetworkError as exc:
                if attempt >= max_attempts:
                    self._logger.warning(
                        "http_request_failed",
                        url=url_redacted,
                        method=request.method.value,
                        attempt=attempt,
                        error_type=type(exc).__name__,
                    )
                    raise HttpRequestError(
                        ErrorCode.NETWORK_RETRYABLE,
                        f"HTTP request failed after {attempt} attempts for {url_redacted}.",
                        failure_kind=HttpRequestFailureKind.NETWORK_ERROR,
                    ) from exc
                await self._schedule_retry(request, url_redacted, attempt, type(exc).__name__)
                attempt += 1
                continue

            if request.follow_redirects and response.status_code in _REDIRECT_STATUS_CODES:
                if redirect_count >= request.max_redirects:
                    raise HttpRequestError(
                        ErrorCode.NETWORK_PERMANENT,
                        "HTTP redirect limit exceeded.",
                        status_code=response.status_code,
                        failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
                        redirect_count=redirect_count,
                    )
                location = response.headers.get("location")
                try:
                    current_url = _safe_redirect_url(
                        current_url,
                        location,
                        allowed_hosts=request.redirect_allowed_hosts,
                        allow_sensitive_query=request.allow_sensitive_redirect_query,
                    )
                except HttpRequestError as exc:
                    raise HttpRequestError(
                        exc.code,
                        exc.message,
                        status_code=response.status_code,
                        failure_kind=exc.failure_kind,
                        redirect_count=redirect_count + 1,
                        allowed_host=exc.allowed_host,
                    ) from exc
                redirect_count += 1
                current_params = None
                if response.status_code == 303:
                    current_method = HttpMethod.GET.value
                    current_json = None
                    current_data = None
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                await self._schedule_retry(
                    request,
                    url_redacted,
                    attempt,
                    f"HTTP {response.status_code}",
                    status_code=response.status_code,
                )
                attempt += 1
                continue

            self._logger.info(
                "http_request_completed",
                url=url_redacted,
                method=request.method.value,
                status_code=response.status_code,
                attempt=attempt,
            )
            return HttpResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=response.text,
                content=response.content,
                url_redacted=redact_url(str(response.url)),
            )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SafeAsyncHttpClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _log_request_started(
        self,
        request: HttpRequest,
        url_redacted: str,
        attempt: int,
    ) -> None:
        self._logger.info(
            "http_request_started",
            url=url_redacted,
            method=request.method.value,
            attempt=attempt,
            headers=redact_mapping(request.headers),
            params=redact_mapping({key: value for key, value in request.params.items()}),
            json_body=redact_mapping(request.json_body) if request.json_body is not None else None,
            form_data=self._redact_form_data(request.form_data),
        )

    def _redact_form_data(
        self,
        form_data: Mapping[str, str] | None,
    ) -> dict[str, object] | None:
        if form_data is None:
            return None
        redacted = redact_mapping(form_data)
        for key in tuple(redacted):
            if key.strip().lower() in _SENSITIVE_FORM_FIELD_NAMES:
                redacted[key] = _REDACTED_FORM_VALUE
        return redacted

    async def _schedule_retry(
        self,
        request: HttpRequest,
        url_redacted: str,
        attempt: int,
        reason: str,
        *,
        status_code: int | None = None,
    ) -> None:
        delay = self._settings.http_backoff_base_seconds * attempt
        self._logger.warning(
            "http_request_retry_scheduled",
            url=url_redacted,
            method=request.method.value,
            attempt=attempt,
            next_attempt=attempt + 1,
            delay_seconds=delay,
            reason=reason,
            status_code=status_code,
        )
        await asyncio.sleep(delay)


def _safe_redirect_url(
    current_url: str,
    location: str | None,
    *,
    allowed_hosts: tuple[str, ...] = (),
    allow_sensitive_query: bool = False,
) -> str:
    if location is None or location == "":
        raise HttpRequestError(
            ErrorCode.NETWORK_PERMANENT,
            "Unsafe HTTP redirect was rejected.",
            failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
            allowed_host=False,
        )
    target = urljoin(current_url, location)
    current = urlparse(current_url)
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise HttpRequestError(
            ErrorCode.NETWORK_PERMANENT,
            "Unsafe HTTP redirect was rejected.",
            failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
            allowed_host=False,
        )
    if parsed.username is not None or parsed.password is not None:
        raise HttpRequestError(
            ErrorCode.NETWORK_PERMANENT,
            "Unsafe HTTP redirect was rejected.",
            failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
            allowed_host=False,
        )
    if parsed.hostname != current.hostname and not _host_is_allowed(
        parsed.hostname,
        allowed_hosts,
    ):
        raise HttpRequestError(
            ErrorCode.NETWORK_PERMANENT,
            "Unsafe HTTP redirect was rejected.",
            failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
            allowed_host=False,
        )
    query_keys = {key.strip().lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    if not allow_sensitive_query and query_keys & _SENSITIVE_REDIRECT_QUERY_KEYS:
        raise HttpRequestError(
            ErrorCode.NETWORK_PERMANENT,
            "Unsafe HTTP redirect was rejected.",
            failure_kind=HttpRequestFailureKind.REDIRECT_REJECTED,
            allowed_host=True,
        )
    return target


def _host_is_allowed(hostname: str | None, allowed_hosts: tuple[str, ...]) -> bool:
    if hostname is None:
        return False
    normalized = hostname.lower().rstrip(".")
    for allowed in allowed_hosts:
        allowed_value = allowed.lower().rstrip(".")
        allow_subdomains = allowed_value.startswith(".")
        allowed_normalized = allowed_value.lstrip(".")
        if normalized == allowed_normalized or (
            allow_subdomains and normalized.endswith(f".{allowed_normalized}")
        ):
            return True
    return False
