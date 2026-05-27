from collections.abc import Mapping

from soundcloud_downloader.application.ports import (
    SoundCloudPlaylistSummary,
    SoundCloudResolvedResource,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
    SoundCloudTranscodingSummary,
    SoundCloudUserSummary,
)
from soundcloud_downloader.domain import (
    MediaCodec,
    MediaContainer,
    NormalizedResolverInput,
    SourceProtocol,
)

_FORBIDDEN_KEYS = frozenset(
    {
        "endpoint_url",
        "stream_url",
        "manifest_url",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set_cookie",
        "client_secret",
        "license_url",
    }
)


class SoundCloudResponseMapper:
    def map_resolved_resource(
        self,
        payload: Mapping[str, object],
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        try:
            if self._contains_forbidden_key(payload):
                return self._error(normalized, "SoundCloud resolver payload contained forbidden fields.")

            status = self._resolve_status(payload.get("status"))
            kind = self._resource_kind(payload.get("kind"))
            warnings = self._warnings(payload.get("warnings"))

            if status is not SoundCloudResolveStatus.RESOLVED:
                return SoundCloudResolvedResource(
                    status=status,
                    kind=kind,
                    normalized=normalized,
                    warnings=warnings,
                )

            if kind is SoundCloudResourceKind.TRACK:
                track_payload = self._mapping(payload.get("track"))
                if track_payload is None:
                    return self._error(normalized, "Resolved track payload is missing.")
                return SoundCloudResolvedResource(
                    status=status,
                    kind=kind,
                    normalized=normalized,
                    track=self._track(track_payload),
                    warnings=warnings,
                )

            if kind is SoundCloudResourceKind.PLAYLIST:
                playlist_payload = self._mapping(payload.get("playlist"))
                if playlist_payload is None:
                    return self._error(normalized, "Resolved playlist payload is missing.")
                return SoundCloudResolvedResource(
                    status=status,
                    kind=kind,
                    normalized=normalized,
                    playlist=self._playlist(playlist_payload),
                    warnings=warnings,
                )

            if kind is SoundCloudResourceKind.USER:
                user_payload = self._mapping(payload.get("user"))
                if user_payload is None:
                    return self._error(normalized, "Resolved user payload is missing.")
                return SoundCloudResolvedResource(
                    status=status,
                    kind=kind,
                    normalized=normalized,
                    user=self._user(user_payload),
                    warnings=warnings,
                )

            return SoundCloudResolvedResource(
                status=status,
                kind=kind,
                normalized=normalized,
                warnings=warnings,
            )
        except (TypeError, ValueError):
            return self._error(normalized, "SoundCloud resolver payload was malformed.")

    def _track(self, payload: Mapping[str, object]) -> SoundCloudTrackSummary:
        return SoundCloudTrackSummary(
            soundcloud_id=self._required_string(payload, "soundcloud_id"),
            title=self._required_string(payload, "title"),
            duration_ms=self._optional_int(payload.get("duration_ms")),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._optional_string(payload.get("permalink_url_redacted")),
            artwork_url_redacted=self._optional_string(payload.get("artwork_url_redacted")),
            user=self._user(user_payload) if (user_payload := self._mapping(payload.get("user"))) else None,
            is_public=self._bool(payload.get("is_public")),
            is_go_plus=self._bool(payload.get("is_go_plus")),
            is_preview_only=self._bool(payload.get("is_preview_only")),
            is_downloadable=self._bool(payload.get("is_downloadable")),
            transcodings=tuple(
                self._transcoding(item)
                for item in self._mapping_sequence(payload.get("transcodings"))
            ),
        )

    def _playlist(self, payload: Mapping[str, object]) -> SoundCloudPlaylistSummary:
        return SoundCloudPlaylistSummary(
            soundcloud_id=self._required_string(payload, "soundcloud_id"),
            title=self._required_string(payload, "title"),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._optional_string(payload.get("permalink_url_redacted")),
            artwork_url_redacted=self._optional_string(payload.get("artwork_url_redacted")),
            user=self._user(user_payload) if (user_payload := self._mapping(payload.get("user"))) else None,
            track_count=self._optional_int(payload.get("track_count")),
            tracks=tuple(self._track(item) for item in self._mapping_sequence(payload.get("tracks"))),
        )

    def _user(self, payload: Mapping[str, object]) -> SoundCloudUserSummary:
        return SoundCloudUserSummary(
            soundcloud_id=self._required_string(payload, "soundcloud_id"),
            username=self._optional_string(payload.get("username")),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._optional_string(payload.get("permalink_url_redacted")),
            avatar_url_redacted=self._optional_string(payload.get("avatar_url_redacted")),
        )

    def _transcoding(self, payload: Mapping[str, object]) -> SoundCloudTranscodingSummary:
        return SoundCloudTranscodingSummary(
            preset=self._optional_string(payload.get("preset")),
            protocol=SourceProtocol(str(payload.get("protocol", SourceProtocol.UNKNOWN.value))),
            mime_type=self._optional_string(payload.get("mime_type")),
            quality=self._optional_string(payload.get("quality")),
            codec=MediaCodec(str(payload.get("codec", MediaCodec.UNKNOWN.value))),
            container=MediaContainer(str(payload.get("container", MediaContainer.UNKNOWN.value))),
            requires_auth=self._bool(payload.get("requires_auth")),
            is_downloadable=self._bool(payload.get("is_downloadable")),
        )

    def _resolve_status(self, value: object) -> SoundCloudResolveStatus:
        return SoundCloudResolveStatus(str(value))

    def _resource_kind(self, value: object) -> SoundCloudResourceKind:
        return SoundCloudResourceKind(str(value))

    def _warnings(self, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise TypeError("warnings must be a sequence")
        return tuple(str(item) for item in value)

    def _mapping(self, value: object) -> Mapping[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("expected mapping")
        return value

    def _mapping_sequence(self, value: object) -> tuple[Mapping[str, object], ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise TypeError("expected sequence")
        items = []
        for item in value:
            if not isinstance(item, Mapping):
                raise TypeError("expected mapping item")
            items.append(item)
        return tuple(items)

    def _required_string(self, payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise TypeError(f"{key} must be a non-empty string")
        return value

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("expected optional string")
        return value

    def _optional_int(self, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("expected optional int")
        return value

    def _bool(self, value: object) -> bool:
        if value is None:
            return False
        if not isinstance(value, bool):
            raise TypeError("expected bool")
        return value

    def _contains_forbidden_key(self, value: object) -> bool:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in _FORBIDDEN_KEYS:
                    return True
                if self._contains_forbidden_key(item):
                    return True
        elif isinstance(value, list | tuple):
            return any(self._contains_forbidden_key(item) for item in value)
        return False

    def _error(self, normalized: NormalizedResolverInput, warning: str) -> SoundCloudResolvedResource:
        return SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.ERROR,
            kind=SoundCloudResourceKind.UNKNOWN,
            normalized=normalized,
            warnings=(warning,),
        )
