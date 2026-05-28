from typing import Protocol, runtime_checkable

from soundcloud_downloader.domain import OAuthTokenProfileId, StoredOAuthTokenSet


@runtime_checkable
class OAuthTokenStore(Protocol):
    def save(self, token_set: StoredOAuthTokenSet) -> None:
        ...

    def get(self, profile_id: OAuthTokenProfileId) -> StoredOAuthTokenSet | None:
        ...

    def delete(self, profile_id: OAuthTokenProfileId) -> None:
        ...
