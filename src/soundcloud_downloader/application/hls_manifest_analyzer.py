from soundcloud_downloader.domain import (
    DRMStatus,
    HLSDrmIndicator,
    HLSManifestAnalysis,
    HLSManifestKind,
)

_KEY_TAGS = ("#EXT-X-KEY", "#EXT-X-SESSION-KEY")
_EME_KEY_FORMAT_MARKERS = (
    "com.apple.streamingkeydelivery",
    "fairplay",
    "widevine",
    "playready",
)
_KNOWN_ENCRYPTION_METHODS = {"AES-128", "SAMPLE-AES", "SAMPLE-AES-CTR"}


class HLSManifestAnalyzer:
    def analyze(self, manifest_text: str) -> HLSManifestAnalysis:
        lines = tuple(line.strip() for line in manifest_text.splitlines() if line.strip())
        is_hls = any(line == "#EXTM3U" for line in lines)

        if not is_hls:
            return HLSManifestAnalysis(
                kind=HLSManifestKind.UNKNOWN,
                is_hls=False,
                is_encrypted=False,
                drm_status=DRMStatus.UNKNOWN,
                has_ext_x_key=False,
                has_ext_x_session_key=False,
                has_stream_inf=False,
                has_media_sequence=False,
                has_endlist=False,
                segment_count=0,
                warnings=("Input text does not look like an HLS manifest.",),
            )

        has_stream_inf = any(line.startswith("#EXT-X-STREAM-INF") for line in lines)
        has_media_sequence = any(line.startswith("#EXT-X-MEDIA-SEQUENCE") for line in lines)
        has_endlist = any(line.startswith("#EXT-X-ENDLIST") for line in lines)
        segment_uri_count = self._count_uri_lines(lines)
        kind = self._classify_kind(has_stream_inf, has_media_sequence, segment_uri_count)
        warnings: list[str] = []
        if kind is HLSManifestKind.UNKNOWN:
            warnings.append("HLS manifest kind could not be determined.")

        indicators: list[HLSDrmIndicator] = []
        encrypted_methods = 0
        none_methods = 0
        ambiguous_key = False
        eme_drm = False
        unknown_method = False

        for line in lines:
            if not self._is_key_line(line):
                continue

            tag = line.split(":", 1)[0]
            attributes = self._parse_attributes(line)
            method = self._normalize_attribute(attributes.get("METHOD"))
            key_format = attributes.get("KEYFORMAT")
            key_format_normalized = self._normalize_attribute(key_format)
            uri_present = "URI" in attributes
            indicators.append(
                HLSDrmIndicator(
                    tag=tag,
                    method=method,
                    uri_present=uri_present,
                    key_format=key_format,
                    raw_line_redacted=self._redact_key_line(line),
                )
            )

            if method is None:
                ambiguous_key = True
                warnings.append("HLS key tag is missing METHOD.")
            elif method == "NONE":
                none_methods += 1
            elif method in _KNOWN_ENCRYPTION_METHODS:
                encrypted_methods += 1
            else:
                unknown_method = True
                warnings.append(f"Unknown HLS encryption method: {method}.")

            if key_format_normalized is not None and any(
                marker in key_format_normalized.lower() for marker in _EME_KEY_FORMAT_MARKERS
            ):
                eme_drm = True

        has_ext_x_key = any(indicator.tag == "#EXT-X-KEY" for indicator in indicators)
        has_ext_x_session_key = any(
            indicator.tag == "#EXT-X-SESSION-KEY" for indicator in indicators
        )

        if none_methods and encrypted_methods:
            warnings.append("Manifest contains mixed METHOD=NONE and encrypted key tags.")

        is_encrypted = encrypted_methods > 0 or eme_drm
        drm_status = self._classify_drm_status(
            indicators=tuple(indicators),
            is_encrypted=is_encrypted,
            eme_drm=eme_drm,
            ambiguous_key=ambiguous_key,
            unknown_method=unknown_method,
        )

        if kind is HLSManifestKind.MASTER:
            segment_count = 0
        else:
            segment_count = segment_uri_count

        return HLSManifestAnalysis(
            kind=kind,
            is_hls=True,
            is_encrypted=is_encrypted,
            drm_status=drm_status,
            has_ext_x_key=has_ext_x_key,
            has_ext_x_session_key=has_ext_x_session_key,
            has_stream_inf=has_stream_inf,
            has_media_sequence=has_media_sequence,
            has_endlist=has_endlist,
            segment_count=segment_count,
            drm_indicators=tuple(indicators),
            warnings=tuple(warnings),
        )

    def _classify_kind(
        self,
        has_stream_inf: bool,
        has_media_sequence: bool,
        segment_uri_count: int,
    ) -> HLSManifestKind:
        if has_stream_inf:
            return HLSManifestKind.MASTER
        if has_media_sequence or segment_uri_count > 0:
            return HLSManifestKind.MEDIA
        return HLSManifestKind.UNKNOWN

    def _classify_drm_status(
        self,
        *,
        indicators: tuple[HLSDrmIndicator, ...],
        is_encrypted: bool,
        eme_drm: bool,
        ambiguous_key: bool,
        unknown_method: bool,
    ) -> DRMStatus:
        if not indicators:
            return DRMStatus.NONE
        if eme_drm:
            return DRMStatus.EME_DRM
        if ambiguous_key or unknown_method:
            return DRMStatus.UNKNOWN
        if is_encrypted:
            return DRMStatus.ENCRYPTED_HLS
        return DRMStatus.NONE

    def _count_uri_lines(self, lines: tuple[str, ...]) -> int:
        return sum(1 for line in lines if not line.startswith("#"))

    def _is_key_line(self, line: str) -> bool:
        return any(line.startswith(tag) for tag in _KEY_TAGS)

    def _parse_attributes(self, line: str) -> dict[str, str]:
        if ":" not in line:
            return {}
        attribute_text = line.split(":", 1)[1]
        attributes: dict[str, str] = {}
        for item in self._split_attribute_list(attribute_text):
            if "=" not in item:
                continue
            name, value = item.split("=", 1)
            attributes[name.strip().upper()] = self._unquote(value.strip())
        return attributes

    def _split_attribute_list(self, attribute_text: str) -> tuple[str, ...]:
        items: list[str] = []
        current: list[str] = []
        in_quotes = False
        for char in attribute_text:
            if char == '"':
                in_quotes = not in_quotes
            if char == "," and not in_quotes:
                items.append("".join(current).strip())
                current = []
                continue
            current.append(char)
        if current:
            items.append("".join(current).strip())
        return tuple(items)

    def _redact_key_line(self, line: str) -> str:
        attributes = self._parse_attributes(line)
        if "URI" not in attributes:
            return line

        redacted = line
        uri_value = attributes["URI"]
        redacted_uri = self._redact_uri(uri_value)
        for needle in (f'URI="{uri_value}"', f"URI={uri_value}"):
            redacted = redacted.replace(needle, f'URI="{redacted_uri}"')
        return redacted

    def _redact_uri(self, uri: str) -> str:
        base_uri = uri.split("?", 1)[0]
        if not base_uri:
            return "[redacted-uri]"
        return f"{base_uri}?[redacted]"

    def _normalize_attribute(self, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    def _unquote(self, value: str) -> str:
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            return value[1:-1]
        return value
