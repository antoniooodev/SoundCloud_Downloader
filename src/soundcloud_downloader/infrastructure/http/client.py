import asyncio
import logging
from collections.abc import Mapping
from types import TracebackType

import httpx

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError
from soundcloud_downloader.infrastructure.http.models import HttpRequest, HttpResponse
from soundcloud_downloader.infrastructure.observability import (
    get_logger,
    redact_mapping,
    redact_url,
)

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_SENSITIVE_FORM_FIELD_NAMES = frozenset({"code", "code_verifier", "client_secret"})
_REDACTED_FORM_VALUE = "[REDACTED]"


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
    ) -> None:
        self.status_code = status_code
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
        attempt = 1

        while True:
            self._log_request_started(request, url_redacted, attempt)
            try:
                response = await self._client.request(
                    request.method.value,
                    request.url,
                    headers=dict(request.headers),
                    params=dict(request.params) if request.params else None,
                    json=dict(request.json_body) if request.json_body is not None else None,
                    data=dict(request.form_data) if request.form_data is not None else None,
                    timeout=timeout_seconds,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
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
                    ) from exc
                await self._schedule_retry(request, url_redacted, attempt, type(exc).__name__)
                attempt += 1
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
