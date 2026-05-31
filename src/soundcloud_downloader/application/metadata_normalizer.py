from pydantic import ValidationError

from soundcloud_downloader.application.ports import (
    SoundCloudPlaylistSummary,
    SoundCloudResolvedResource,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
    SoundCloudTrackSummary,
    SoundCloudUserSummary,
)
from soundcloud_downloader.domain import (
    ErrorCode,
    SoundCloudArtworkUrl,
    SoundcloudDownloaderError,
    SoundCloudMetadataKind,
    SoundCloudPermalinkUrl,
    SoundCloudPlaylistMetadata,
    SoundCloudResolvedMetadata,
    SoundCloudResourceId,
    SoundCloudTrackMetadata,
    SoundCloudUserMetadata,
)


class SoundCloudMetadataNormalizationError(SoundcloudDownloaderError):
    pass


class SoundCloudMetadataNormalizer:
    def normalize(
        self,
        resource: SoundCloudResolvedResource,
    ) -> SoundCloudResolvedMetadata:
        try:
            if resource.status is not SoundCloudResolveStatus.RESOLVED:
                raise SoundCloudMetadataNormalizationError(
                    ErrorCode.UNKNOWN_UNSAFE,
                    "Unsupported SoundCloud metadata resource.",
                )
            if resource.kind is SoundCloudResourceKind.TRACK and resource.track is not None:
                return self._track(resource.track)
            if resource.kind is SoundCloudResourceKind.PLAYLIST and resource.playlist is not None:
                return self._playlist(resource.playlist)
            if resource.kind is SoundCloudResourceKind.USER and resource.user is not None:
                return self._user(resource.user)
            raise SoundCloudMetadataNormalizationError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Unsupported SoundCloud metadata resource.",
            )
        except SoundCloudMetadataNormalizationError:
            raise
        except (TypeError, ValueError, ValidationError) as exc:
            raise SoundCloudMetadataNormalizationError(
                ErrorCode.UNKNOWN_UNSAFE,
                "Malformed SoundCloud metadata resource.",
            ) from exc

    def _track(self, track: SoundCloudTrackSummary) -> SoundCloudTrackMetadata:
        return SoundCloudTrackMetadata(
            kind=SoundCloudMetadataKind.TRACK,
            id=SoundCloudResourceId(value=track.soundcloud_id),
            title=track.title,
            duration_ms=track.duration_ms,
            permalink_url=self._permalink_url(track.permalink_url_redacted),
            artwork_url=self._artwork_url(track.artwork_url_redacted),
            user=self._optional_user(track.user),
            streamable=bool(track.transcodings) if track.transcodings else None,
            downloadable=track.is_downloadable,
            access=self._track_access(track),
        )

    def _playlist(self, playlist: SoundCloudPlaylistSummary) -> SoundCloudPlaylistMetadata:
        return SoundCloudPlaylistMetadata(
            kind=SoundCloudMetadataKind.PLAYLIST,
            id=SoundCloudResourceId(value=playlist.soundcloud_id),
            title=playlist.title,
            duration_ms=self._playlist_duration(playlist),
            permalink_url=self._permalink_url(playlist.permalink_url_redacted),
            artwork_url=self._artwork_url(playlist.artwork_url_redacted),
            user=self._optional_user(playlist.user),
            track_count=playlist.track_count,
            tracks=tuple(self._track(track) for track in playlist.tracks),
        )

    def _user(self, user: SoundCloudUserSummary) -> SoundCloudUserMetadata:
        username = user.username
        if username is None:
            raise ValueError("SoundCloud user metadata requires a username.")
        return SoundCloudUserMetadata(
            kind=SoundCloudMetadataKind.USER,
            id=SoundCloudResourceId(value=user.soundcloud_id),
            username=username,
            display_name=None,
            permalink_url=self._permalink_url(user.permalink_url_redacted),
            avatar_url=self._artwork_url(user.avatar_url_redacted),
        )

    def _optional_user(self, user: SoundCloudUserSummary | None) -> SoundCloudUserMetadata | None:
        if user is None or user.username is None:
            return None
        return self._user(user)

    def _permalink_url(self, value: str | None) -> SoundCloudPermalinkUrl | None:
        return SoundCloudPermalinkUrl(value=value) if value is not None else None

    def _artwork_url(self, value: str | None) -> SoundCloudArtworkUrl | None:
        return SoundCloudArtworkUrl(value=value) if value is not None else None

    def _playlist_duration(self, playlist: SoundCloudPlaylistSummary) -> int | None:
        durations = [track.duration_ms for track in playlist.tracks if track.duration_ms is not None]
        if not durations:
            return None
        return sum(durations)

    def _track_access(self, track: SoundCloudTrackSummary) -> str | None:
        if track.is_preview_only:
            return "preview"
        if track.is_go_plus:
            return "go_plus"
        if track.is_public:
            return "public"
        return None
