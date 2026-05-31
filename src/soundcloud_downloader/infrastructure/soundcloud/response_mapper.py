from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr

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
    SoundCloudTranscodingEndpointUrl,
    SoundCloudTranscodingFormat,
    SoundCloudTranscodingMetadata,
    SoundCloudTranscodingMimeType,
    SoundCloudTranscodingProtocol,
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
        "set-cookie",
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

            if "status" not in payload:
                return self._official_resource(payload, normalized)

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

    def _official_resource(
        self,
        payload: Mapping[str, object],
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        kind = self._official_resource_kind(payload.get("kind"))

        if kind is SoundCloudResourceKind.TRACK:
            return SoundCloudResolvedResource(
                status=SoundCloudResolveStatus.RESOLVED,
                kind=kind,
                normalized=normalized,
                track=self._official_track(payload),
            )

        if kind is SoundCloudResourceKind.PLAYLIST:
            return SoundCloudResolvedResource(
                status=SoundCloudResolveStatus.RESOLVED,
                kind=kind,
                normalized=normalized,
                playlist=self._official_playlist(payload),
            )

        if kind is SoundCloudResourceKind.USER:
            return SoundCloudResolvedResource(
                status=SoundCloudResolveStatus.RESOLVED,
                kind=kind,
                normalized=normalized,
                user=self._official_user(payload),
            )

        raise ValueError("unsupported official resource kind")

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

    def _official_track(self, payload: Mapping[str, object]) -> SoundCloudTrackSummary:
        return SoundCloudTrackSummary(
            soundcloud_id=self._required_id(payload, "id"),
            title=self._required_string(payload, "title"),
            duration_ms=self._optional_int(payload.get("duration")),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._redacted_url(payload.get("permalink_url")),
            artwork_url_redacted=self._redacted_url(payload.get("artwork_url")),
            user=(
                self._official_user(user_payload)
                if (user_payload := self._mapping(payload.get("user")))
                else None
            ),
            is_public=self._official_public_flag(payload.get("sharing")),
            is_go_plus=False,
            is_preview_only=False,
            is_downloadable=self._bool(payload.get("downloadable")),
            transcodings=self._official_transcodings(payload),
        )

    def _official_playlist(self, payload: Mapping[str, object]) -> SoundCloudPlaylistSummary:
        tracks = tuple(
            self._official_track(item) for item in self._mapping_sequence(payload.get("tracks"))
        )
        return SoundCloudPlaylistSummary(
            soundcloud_id=self._required_id(payload, "id"),
            title=self._required_string(payload, "title"),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._redacted_url(payload.get("permalink_url")),
            artwork_url_redacted=self._redacted_url(payload.get("artwork_url")),
            user=(
                self._official_user(user_payload)
                if (user_payload := self._mapping(payload.get("user")))
                else None
            ),
            track_count=self._optional_int(payload.get("track_count")) or len(tracks),
            tracks=tracks,
        )

    def _official_user(self, payload: Mapping[str, object]) -> SoundCloudUserSummary:
        return SoundCloudUserSummary(
            soundcloud_id=self._required_id(payload, "id"),
            username=self._official_username(payload),
            permalink=self._optional_string(payload.get("permalink")),
            permalink_url_redacted=self._redacted_url(payload.get("permalink_url")),
            avatar_url_redacted=self._redacted_url(payload.get("avatar_url")),
        )

    def _official_transcodings(
        self,
        payload: Mapping[str, object],
    ) -> tuple[SoundCloudTranscodingMetadata, ...]:
        media = self._mapping(payload.get("media"))
        if media is None:
            return ()
        return tuple(
            self._official_transcoding(item)
            for item in self._mapping_sequence(media.get("transcodings"))
        )

    def _official_transcoding(
        self,
        payload: Mapping[str, object],
    ) -> SoundCloudTranscodingMetadata:
        format_payload = self._mapping(payload.get("format"))
        if format_payload is None:
            raise TypeError("transcoding format must be a mapping")
        return SoundCloudTranscodingMetadata(
            preset=self._optional_string(payload.get("preset")),
            quality=self._optional_string(payload.get("quality")),
            snipped=self._optional_bool(payload.get("snipped")),
            format=SoundCloudTranscodingFormat(
                protocol=self._transcoding_protocol(format_payload.get("protocol")),
                mime_type=self._transcoding_mime_type(format_payload.get("mime_type")),
            ),
            endpoint_url=SoundCloudTranscodingEndpointUrl(
                value=SecretStr(self._required_string(payload, "url"))
            ),
        )

    def _resolve_status(self, value: object) -> SoundCloudResolveStatus:
        return SoundCloudResolveStatus(str(value))

    def _resource_kind(self, value: object) -> SoundCloudResourceKind:
        return SoundCloudResourceKind(str(value))

    def _official_resource_kind(self, value: object) -> SoundCloudResourceKind:
        raw_kind = str(value)
        if raw_kind == "track":
            return SoundCloudResourceKind.TRACK
        if raw_kind == "playlist":
            return SoundCloudResourceKind.PLAYLIST
        if raw_kind in {"user", "profile"}:
            return SoundCloudResourceKind.USER
        return SoundCloudResourceKind.UNKNOWN

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

    def _required_id(self, payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            raise TypeError(f"{key} must be a non-empty string or integer")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value:
            return value
        raise TypeError(f"{key} must be a non-empty string or integer")

    def _optional_string(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("expected optional string")
        return value

    def _official_username(self, payload: Mapping[str, object]) -> str | None:
        username = payload.get("username", payload.get("full_name"))
        if username is None:
            return None
        if not isinstance(username, str) or not username:
            raise TypeError("username must be a non-empty string")
        return username

    def _redacted_url(self, value: object) -> str | None:
        url = self._optional_string(value)
        if url is None:
            return None
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise TypeError("URL must not contain userinfo")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

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

    def _optional_bool(self, value: object) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise TypeError("expected optional bool")
        return value

    def _official_public_flag(self, value: object) -> bool:
        if value is None:
            return False
        if not isinstance(value, str):
            raise TypeError("sharing must be a string")
        return value == "public"

    def _source_protocol(self, value: object) -> SourceProtocol:
        if value is None:
            return SourceProtocol.UNKNOWN
        if not isinstance(value, str):
            raise TypeError("protocol must be a string")
        try:
            return SourceProtocol(value)
        except ValueError:
            return SourceProtocol.UNKNOWN

    def _transcoding_protocol(self, value: object) -> SoundCloudTranscodingProtocol:
        if value is None:
            return SoundCloudTranscodingProtocol.UNKNOWN
        if not isinstance(value, str):
            raise TypeError("protocol must be a string")
        try:
            return SoundCloudTranscodingProtocol(value)
        except ValueError:
            return SoundCloudTranscodingProtocol.UNKNOWN

    def _transcoding_mime_type(self, value: object) -> SoundCloudTranscodingMimeType:
        if value is None:
            return SoundCloudTranscodingMimeType.UNKNOWN
        if not isinstance(value, str):
            raise TypeError("mime_type must be a string")
        try:
            return SoundCloudTranscodingMimeType(value)
        except ValueError:
            return SoundCloudTranscodingMimeType.UNKNOWN

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
