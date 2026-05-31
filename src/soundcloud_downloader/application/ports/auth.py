from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from soundcloud_downloader.domain import ErrorCode, SoundcloudDownloaderError

if TYPE_CHECKING:
    from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken


class AccessTokenProviderFailureReason(str, Enum):
    TOKEN_REFRESH_FAILED = "token_refresh_failed"
    TOKEN_REFRESH_RESPONSE_INVALID = "token_refresh_response_invalid"
    TOKEN_REFRESH_PERSIST_FAILED = "token_refresh_persist_failed"


class AccessTokenProviderError(SoundcloudDownloaderError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        reason: AccessTokenProviderFailureReason,
    ) -> None:
        self.reason = reason
        super().__init__(code, message)


@runtime_checkable
class AccessTokenProviderPort(Protocol):
    async def get_access_token(self) -> SoundCloudAccessToken:
        ...
