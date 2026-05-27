from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken


@runtime_checkable
class AccessTokenProviderPort(Protocol):
    async def get_access_token(self) -> SoundCloudAccessToken:
        ...
