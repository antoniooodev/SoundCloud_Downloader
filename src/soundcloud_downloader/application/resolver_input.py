from urllib.parse import quote, unquote, urlsplit, urlunsplit

from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    ResolverInputType,
    SoundCloudResourceType,
)

_SOUNDCLOUD_HOSTS = {
    "soundcloud.com": "soundcloud.com",
    "www.soundcloud.com": "soundcloud.com",
    "m.soundcloud.com": "soundcloud.com",
    "on.soundcloud.com": "on.soundcloud.com",
}

_RESERVED_FIRST_PARTS = {
    "discover",
    "stream",
    "you",
    "signin",
    "upload",
    "popular",
    "search",
    "charts",
    "pages",
    "terms-of-use",
    "privacy",
}


class ResolverInputNormalizer:
    def normalize(self, value: str) -> NormalizedResolverInput:
        trimmed = value.strip()
        parsed = urlsplit(trimmed)
        if not parsed.scheme or not parsed.netloc:
            return NormalizedResolverInput(
                input_type=ResolverInputType.RAW_TEXT,
                resource_type=SoundCloudResourceType.UNKNOWN,
                warnings=("Input is not a URL; raw text search is not implemented.",),
            )

        host = parsed.netloc.lower()
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        if ":" in host:
            host = host.split(":", 1)[0]

        normalized_host = _SOUNDCLOUD_HOSTS.get(host)
        path_parts = self._normalize_path_parts(parsed.path)
        normalized_path = self._normalized_path(path_parts)

        if normalized_host is None:
            return NormalizedResolverInput(
                input_type=ResolverInputType.URL,
                resource_type=SoundCloudResourceType.UNKNOWN,
                normalized_url=self._build_url(parsed.scheme or "https", host, normalized_path),
                normalized_path=normalized_path,
                host=host,
                path_parts=path_parts,
                warnings=("Unsupported URL host for SoundCloud resolver input.",),
            )

        resource_type, requires_network, warnings = self._classify(
            normalized_host,
            path_parts,
        )
        normalized_url = self._build_url("https", normalized_host, normalized_path)
        return NormalizedResolverInput(
            input_type=ResolverInputType.URL,
            resource_type=resource_type,
            normalized_url=normalized_url,
            normalized_path=normalized_path,
            host=normalized_host,
            path_parts=path_parts,
            requires_network_resolution=requires_network,
            warnings=warnings,
        )

    def _classify(
        self,
        host: str,
        path_parts: tuple[str, ...],
    ) -> tuple[SoundCloudResourceType, bool, tuple[str, ...]]:
        if host == "on.soundcloud.com":
            if path_parts:
                return SoundCloudResourceType.SHORTLINK, True, ()
            return (
                SoundCloudResourceType.UNKNOWN,
                False,
                ("SoundCloud shortlink URL is missing a slug.",),
            )

        if not path_parts:
            return SoundCloudResourceType.UNKNOWN, False, ("SoundCloud URL is missing a path.",)

        first = path_parts[0].lower()
        if first in _RESERVED_FIRST_PARTS:
            return (
                SoundCloudResourceType.UNKNOWN,
                False,
                ("SoundCloud reserved route cannot be classified without resolver support.",),
            )

        if len(path_parts) == 1:
            return SoundCloudResourceType.USER, False, ()

        if len(path_parts) >= 3 and path_parts[1].lower() == "sets":
            warnings: tuple[str, ...] = ()
            if len(path_parts) > 3:
                warnings = ("Playlist URL contains extra path parts that were ignored.",)
            return SoundCloudResourceType.PLAYLIST, False, warnings

        if len(path_parts) == 2:
            return SoundCloudResourceType.TRACK, False, ()

        return (
            SoundCloudResourceType.UNKNOWN,
            False,
            ("SoundCloud URL path is too ambiguous to classify safely.",),
        )

    def _normalize_path_parts(self, path: str) -> tuple[str, ...]:
        parts = []
        for raw_part in path.split("/"):
            if raw_part == "":
                continue
            decoded = unquote(raw_part)
            if decoded:
                parts.append(decoded)
        return tuple(parts)

    def _normalized_path(self, path_parts: tuple[str, ...]) -> str:
        if not path_parts:
            return "/"
        encoded_parts = tuple(quote(part, safe="-._~") for part in path_parts)
        return "/" + "/".join(encoded_parts)

    def _build_url(self, scheme: str, host: str, path: str) -> str:
        normalized_scheme = "https" if scheme in {"http", "https"} else scheme
        return urlunsplit((normalized_scheme, host, path, "", ""))
