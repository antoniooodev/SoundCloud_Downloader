from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr, ValidationError

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

_UNKNOWN_INVALID_FIELD = "unknown"
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


class _PayloadMappingError(ValueError):
    def __init__(self, *field_paths: str) -> None:
        self.field_paths = _safe_invalid_fields(field_paths)
        super().__init__("SoundCloud resolver payload was malformed.")


def summarize_soundcloud_payload_shape(payload: Mapping[str, object]) -> dict[str, object]:
    media = payload.get("media")
    transcodings: object = None
    if isinstance(media, Mapping):
        transcodings = media.get("transcodings")

    summary: dict[str, object] = {
        "top_level_keys": tuple(sorted(str(key) for key in payload)),
        "kind_present": "kind" in payload,
        "kind_type": type(payload.get("kind")).__name__ if "kind" in payload else None,
        "media_present": "media" in payload,
        "transcodings_count": len(transcodings) if isinstance(transcodings, list | tuple) else None,
        "transcodings_field_keys": (),
        "transcodings_format_field_keys": (),
        "nullable_field_names": tuple(sorted(_nullable_field_names(payload))),
    }
    if isinstance(transcodings, list | tuple):
        field_keys: set[str] = set()
        format_keys: set[str] = set()
        for item in transcodings:
            if not isinstance(item, Mapping):
                continue
            field_keys.update(str(key) for key in item)
            format_payload = item.get("format")
            if isinstance(format_payload, Mapping):
                format_keys.update(str(key) for key in format_payload)
        summary["transcodings_field_keys"] = tuple(sorted(field_keys))
        summary["transcodings_format_field_keys"] = tuple(sorted(format_keys))
    return summary


class SoundCloudResponseMapper:
    def map_resolved_resource(
        self,
        payload: Mapping[str, object],
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        try:
            forbidden_path = self._forbidden_key_path(payload)
            if forbidden_path is not None:
                return self._error(
                    normalized,
                    "SoundCloud resolver payload contained forbidden fields.",
                    invalid_fields=(forbidden_path,),
                )

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
        except _PayloadMappingError as exc:
            return self._error(
                normalized,
                "SoundCloud resolver payload was malformed.",
                invalid_fields=exc.field_paths,
            )
        except ValidationError as exc:
            return self._error(
                normalized,
                "SoundCloud resolver payload was malformed.",
                invalid_fields=_safe_invalid_fields(_validation_error_paths(exc)),
            )
        except (TypeError, ValueError):
            return self._error(
                normalized,
                "SoundCloud resolver payload was malformed.",
                invalid_fields=(_UNKNOWN_INVALID_FIELD,),
            )

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
            duration_ms=self._optional_int(payload.get("duration_ms"), "duration_ms"),
            permalink=self._optional_string(payload.get("permalink"), "permalink"),
            permalink_url_redacted=self._optional_string(
                payload.get("permalink_url_redacted"), "permalink_url_redacted"
            ),
            artwork_url_redacted=self._optional_string(
                payload.get("artwork_url_redacted"), "artwork_url_redacted"
            ),
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
            permalink=self._optional_string(payload.get("permalink"), "permalink"),
            permalink_url_redacted=self._optional_string(
                payload.get("permalink_url_redacted"), "permalink_url_redacted"
            ),
            artwork_url_redacted=self._optional_string(
                payload.get("artwork_url_redacted"), "artwork_url_redacted"
            ),
            user=self._user(user_payload) if (user_payload := self._mapping(payload.get("user"))) else None,
            track_count=self._optional_int(payload.get("track_count"), "track_count"),
            tracks=tuple(self._track(item) for item in self._mapping_sequence(payload.get("tracks"))),
        )

    def _user(self, payload: Mapping[str, object]) -> SoundCloudUserSummary:
        return SoundCloudUserSummary(
            soundcloud_id=self._required_string(payload, "soundcloud_id"),
            username=self._optional_string(payload.get("username"), "username"),
            permalink=self._optional_string(payload.get("permalink"), "permalink"),
            permalink_url_redacted=self._optional_string(
                payload.get("permalink_url_redacted"), "permalink_url_redacted"
            ),
            avatar_url_redacted=self._optional_string(
                payload.get("avatar_url_redacted"), "avatar_url_redacted"
            ),
        )

    def _transcoding(self, payload: Mapping[str, object]) -> SoundCloudTranscodingSummary:
        return SoundCloudTranscodingSummary(
            preset=self._optional_string(payload.get("preset"), "preset"),
            protocol=SourceProtocol(str(payload.get("protocol", SourceProtocol.UNKNOWN.value))),
            mime_type=self._optional_string(payload.get("mime_type"), "mime_type"),
            quality=self._optional_string(payload.get("quality"), "quality"),
            codec=MediaCodec(str(payload.get("codec", MediaCodec.UNKNOWN.value))),
            container=MediaContainer(str(payload.get("container", MediaContainer.UNKNOWN.value))),
            requires_auth=self._bool(payload.get("requires_auth")),
            is_downloadable=self._bool(payload.get("is_downloadable")),
        )

    def _official_track(self, payload: Mapping[str, object]) -> SoundCloudTrackSummary:
        return SoundCloudTrackSummary(
            soundcloud_id=self._required_id(payload, "id", "id"),
            title=self._required_string(payload, "title", "title"),
            duration_ms=self._optional_int(payload.get("duration"), "duration"),
            permalink=self._optional_string(payload.get("permalink"), "permalink"),
            permalink_url_redacted=self._redacted_url(
                payload.get("permalink_url"), "permalink_url"
            ),
            artwork_url_redacted=self._redacted_url(payload.get("artwork_url"), "artwork_url"),
            user=(
                self._official_user(user_payload)
                if (user_payload := self._mapping(payload.get("user"), "user"))
                else None
            ),
            is_public=self._official_public_flag(payload.get("sharing"), "sharing"),
            is_go_plus=False,
            is_preview_only=False,
            is_downloadable=self._bool(payload.get("downloadable"), "downloadable"),
            transcodings=self._official_transcodings(payload),
        )

    def _official_playlist(self, payload: Mapping[str, object]) -> SoundCloudPlaylistSummary:
        tracks = tuple(
            self._official_track(item) for item in self._mapping_sequence(payload.get("tracks"))
        )
        return SoundCloudPlaylistSummary(
            soundcloud_id=self._required_id(payload, "id", "id"),
            title=self._required_string(payload, "title", "title"),
            permalink=self._optional_string(payload.get("permalink"), "permalink"),
            permalink_url_redacted=self._redacted_url(
                payload.get("permalink_url"), "permalink_url"
            ),
            artwork_url_redacted=self._redacted_url(payload.get("artwork_url"), "artwork_url"),
            user=(
                self._official_user(user_payload)
                if (user_payload := self._mapping(payload.get("user"), "user"))
                else None
            ),
            track_count=self._optional_int(payload.get("track_count"), "track_count") or len(tracks),
            tracks=tracks,
        )

    def _official_user(self, payload: Mapping[str, object]) -> SoundCloudUserSummary:
        return SoundCloudUserSummary(
            soundcloud_id=self._required_id(payload, "id", "user.id"),
            username=self._official_username(payload, "user.username"),
            permalink=self._optional_string(payload.get("permalink"), "user.permalink"),
            permalink_url_redacted=self._redacted_url(
                payload.get("permalink_url"), "user.permalink_url"
            ),
            avatar_url_redacted=self._redacted_url(payload.get("avatar_url"), "user.avatar_url"),
        )

    def _official_transcodings(
        self,
        payload: Mapping[str, object],
    ) -> tuple[SoundCloudTranscodingMetadata, ...]:
        media = self._mapping(payload.get("media"), "media")
        if media is None:
            return ()
        return tuple(
            self._official_transcoding(item, index=index)
            for index, item in enumerate(
                self._mapping_sequence(media.get("transcodings"), "media.transcodings")
            )
        )

    def _official_transcoding(
        self,
        payload: Mapping[str, object],
        *,
        index: int,
    ) -> SoundCloudTranscodingMetadata:
        path = f"media.transcodings.{index}"
        format_payload = self._mapping(payload.get("format"), f"{path}.format")
        if format_payload is None:
            raise _PayloadMappingError(f"{path}.format")
        try:
            return SoundCloudTranscodingMetadata(
                preset=self._optional_string(payload.get("preset"), f"{path}.preset"),
                quality=self._optional_string(payload.get("quality"), f"{path}.quality"),
                snipped=self._optional_bool(payload.get("snipped"), f"{path}.snipped"),
                format=SoundCloudTranscodingFormat(
                    protocol=self._transcoding_protocol(
                        format_payload.get("protocol"), f"{path}.format.protocol"
                    ),
                    mime_type=self._transcoding_mime_type(
                        format_payload.get("mime_type"), f"{path}.format.mime_type"
                    ),
                ),
                endpoint_url=SoundCloudTranscodingEndpointUrl(
                    value=SecretStr(self._required_string(payload, "url", f"{path}.url"))
                ),
            )
        except ValidationError as exc:
            fields = tuple(
                f"{path}.url"
                if _path_mentions(field, "endpoint_url") or field == "value"
                else f"{path}.{field}"
                for field in _validation_error_paths(exc)
            )
            raise _PayloadMappingError(*fields) from exc

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

    def _mapping(self, value: object, path: str = _UNKNOWN_INVALID_FIELD) -> Mapping[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise _PayloadMappingError(path)
        return value

    def _mapping_sequence(
        self,
        value: object,
        path: str = _UNKNOWN_INVALID_FIELD,
    ) -> tuple[Mapping[str, object], ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise _PayloadMappingError(path)
        items = []
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise _PayloadMappingError(f"{path}.{index}")
            items.append(item)
        return tuple(items)

    def _required_string(
        self,
        payload: Mapping[str, object],
        key: str,
        path: str | None = None,
    ) -> str:
        path = key if path is None else path
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise _PayloadMappingError(path)
        return value

    def _required_id(self, payload: Mapping[str, object], key: str, path: str) -> str:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            raise _PayloadMappingError(path)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value:
            return value
        raise _PayloadMappingError(path)

    def _optional_string(self, value: object, path: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise _PayloadMappingError(path)
        return value

    def _official_username(self, payload: Mapping[str, object], path: str) -> str | None:
        username = payload.get("username", payload.get("full_name"))
        if username is None:
            return None
        if not isinstance(username, str) or not username:
            raise _PayloadMappingError(path)
        return username

    def _redacted_url(self, value: object, path: str) -> str | None:
        url = self._optional_string(value, path)
        if url is None:
            return None
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise _PayloadMappingError(path)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _optional_int(self, value: object, path: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise _PayloadMappingError(path)
        return value

    def _bool(self, value: object, path: str = _UNKNOWN_INVALID_FIELD) -> bool:
        if value is None:
            return False
        if not isinstance(value, bool):
            raise _PayloadMappingError(path)
        return value

    def _optional_bool(self, value: object, path: str) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise _PayloadMappingError(path)
        return value

    def _official_public_flag(self, value: object, path: str) -> bool:
        if value is None:
            return False
        if not isinstance(value, str):
            raise _PayloadMappingError(path)
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

    def _transcoding_protocol(self, value: object, path: str) -> SoundCloudTranscodingProtocol:
        if value is None:
            return SoundCloudTranscodingProtocol.UNKNOWN
        if not isinstance(value, str):
            raise _PayloadMappingError(path)
        try:
            return SoundCloudTranscodingProtocol(value)
        except ValueError:
            return SoundCloudTranscodingProtocol.UNKNOWN

    def _transcoding_mime_type(self, value: object, path: str) -> SoundCloudTranscodingMimeType:
        if value is None:
            return SoundCloudTranscodingMimeType.UNKNOWN
        if not isinstance(value, str):
            raise _PayloadMappingError(path)
        try:
            return SoundCloudTranscodingMimeType(value)
        except ValueError:
            return SoundCloudTranscodingMimeType.UNKNOWN

    def _forbidden_key_path(
        self,
        value: object,
        *,
        prefix: tuple[str, ...] = (),
    ) -> str | None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                path = prefix + (str(key),)
                if str(key).lower() in _FORBIDDEN_KEYS:
                    return ".".join(path)
                if nested_path := self._forbidden_key_path(item, prefix=path):
                    return nested_path
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                if nested_path := self._forbidden_key_path(item, prefix=prefix + (str(index),)):
                    return nested_path
        return None

    def _error(
        self,
        normalized: NormalizedResolverInput,
        warning: str,
        *,
        invalid_fields: tuple[str, ...] = (),
    ) -> SoundCloudResolvedResource:
        return SoundCloudResolvedResource(
            status=SoundCloudResolveStatus.ERROR,
            kind=SoundCloudResourceKind.UNKNOWN,
            normalized=normalized,
            warnings=(warning,),
            invalid_fields=_safe_invalid_fields(invalid_fields),
        )


def _validation_error_paths(exc: ValidationError) -> tuple[str, ...]:
    paths = []
    for error in exc.errors():
        loc = error.get("loc")
        if isinstance(loc, tuple) and loc:
            paths.append(".".join(str(part) for part in loc))
    return tuple(paths) or (_UNKNOWN_INVALID_FIELD,)


def _safe_invalid_fields(field_paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    safe_paths = []
    for field_path in field_paths:
        normalized = field_path.lower()
        if not normalized or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_." for char in normalized
        ):
            safe_paths.append(_UNKNOWN_INVALID_FIELD)
        else:
            safe_paths.append(normalized)
    return tuple(dict.fromkeys(safe_paths)) or (_UNKNOWN_INVALID_FIELD,)


def _path_mentions(field_path: str, marker: str) -> bool:
    return marker in field_path.split(".")


def _nullable_field_names(value: object, *, prefix: tuple[str, ...] = ()) -> set[str]:
    nullable = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = prefix + (str(key),)
            if item is None:
                nullable.add(".".join(path))
            else:
                nullable.update(_nullable_field_names(item, prefix=path))
    elif isinstance(value, list | tuple):
        for item in value:
            nullable.update(_nullable_field_names(item, prefix=prefix))
    return nullable
