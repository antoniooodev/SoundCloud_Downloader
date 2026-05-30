from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_serializer, field_validator

from soundcloud_downloader.domain import (
    ErrorCode,
    HLSByteRange,
    HLSInitializationMapReference,
    HLSInitializationMapUrl,
    HLSSegmentPlan,
    HLSSegmentReference,
    HLSSegmentUrl,
    SoundcloudDownloaderError,
    SoundCloudResolvedStreamUrl,
)

_MALFORMED_MESSAGE = "Malformed HLS media playlist."
_ENCRYPTED_MESSAGE = "Encrypted HLS playlists cannot be planned."
_UNSUPPORTED_MESSAGE = "Unsupported HLS playlist type."
_UNSAFE_URL_MESSAGE = "Unsafe HLS segment URL."


class HLSSegmentPlanningError(SoundcloudDownloaderError):
    pass


class HLSSegmentPlanningRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_url: SoundCloudResolvedStreamUrl
    manifest_text: SecretStr

    @field_validator("manifest_text")
    @classmethod
    def validate_manifest_text(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value().strip() == "":
            raise ValueError("HLS manifest text must not be empty.")
        return value

    @field_serializer("manifest_text", when_used="always")
    def serialize_manifest_text(self, value: SecretStr) -> str:
        return str(value)


class HLSSegmentPlanner:
    def build_plan(
        self,
        request: HLSSegmentPlanningRequest,
    ) -> HLSSegmentPlan:
        manifest_text = request.manifest_text.get_secret_value()
        lines = tuple(line.strip() for line in manifest_text.splitlines())
        self._validate_supported_playlist(lines, manifest_text)

        target_duration_seconds: float | None = None
        media_sequence = 0
        end_list = False
        initialization_map: HLSInitializationMapReference | None = None
        pending_duration: float | None = None
        pending_title: str | None = None
        pending_byte_range: HLSByteRange | None = None
        segments: list[HLSSegmentReference] = []

        for line in lines:
            if line == "":
                continue
            if line.startswith("#EXT-X-TARGETDURATION:"):
                target_duration_seconds = self._parse_positive_float(
                    line.split(":", 1)[1],
                    _MALFORMED_MESSAGE,
                )
                continue
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                media_sequence = self._parse_non_negative_int(line.split(":", 1)[1])
                continue
            if line == "#EXT-X-ENDLIST":
                end_list = True
                continue
            if line.startswith("#EXT-X-BYTERANGE:"):
                pending_byte_range = self._parse_byte_range(line.split(":", 1)[1])
                continue
            if line.startswith("#EXT-X-MAP:"):
                initialization_map = self._parse_initialization_map(
                    line.split(":", 1)[1],
                    request.manifest_url,
                )
                continue
            if line.startswith("#EXTINF:"):
                if pending_duration is not None:
                    raise self._error(_MALFORMED_MESSAGE)
                pending_duration, pending_title = self._parse_extinf(line)
                continue
            if line.startswith("#"):
                continue

            if pending_duration is None:
                raise self._error(_MALFORMED_MESSAGE)
            if line == "":
                raise self._error(_MALFORMED_MESSAGE)
            segments.append(
                HLSSegmentReference(
                    index=len(segments),
                    url=self._segment_url(line, request.manifest_url),
                    duration_seconds=pending_duration,
                    title=pending_title,
                    byte_range=pending_byte_range,
                )
            )
            pending_duration = None
            pending_title = None
            pending_byte_range = None

        if pending_duration is not None:
            raise self._error(_MALFORMED_MESSAGE)
        if not segments:
            raise self._error(_MALFORMED_MESSAGE)

        try:
            return HLSSegmentPlan(
                manifest_url=request.manifest_url,
                segments=tuple(segments),
                initialization_map=initialization_map,
                target_duration_seconds=target_duration_seconds,
                media_sequence=media_sequence,
                end_list=end_list,
            )
        except ValidationError as exc:
            raise self._error(_MALFORMED_MESSAGE) from exc

    def _validate_supported_playlist(self, lines: tuple[str, ...], manifest_text: str) -> None:
        if "#EXTM3U" not in manifest_text:
            raise self._error(_MALFORMED_MESSAGE)
        if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
            raise self._error(_UNSUPPORTED_MESSAGE)

        upper_text = manifest_text.upper()
        if (
            "#EXT-X-KEY" in upper_text
            or "#EXT-X-SESSION-KEY" in upper_text
            or "SAMPLE-AES" in upper_text
            or "KEYFORMAT" in upper_text
        ):
            raise self._error(_ENCRYPTED_MESSAGE)

    def _parse_extinf(self, line: str) -> tuple[float, str | None]:
        extinf_value = line.split(":", 1)[1]
        duration_text, separator, title = extinf_value.partition(",")
        duration = self._parse_positive_float(duration_text, _MALFORMED_MESSAGE)
        return duration, title if separator else None

    def _parse_initialization_map(
        self,
        attribute_text: str,
        manifest_url: SoundCloudResolvedStreamUrl,
    ) -> HLSInitializationMapReference:
        attributes = self._parse_attributes(attribute_text)
        uri = attributes.get("URI")
        if uri is None or uri == "":
            raise self._error(_MALFORMED_MESSAGE)
        try:
            return HLSInitializationMapReference(
                url=HLSInitializationMapUrl(value=SecretStr(urljoin(manifest_url.get_secret_value(), uri))),
                byte_range=(
                    None
                    if "BYTERANGE" not in attributes
                    else self._parse_byte_range(attributes["BYTERANGE"])
                ),
            )
        except (ValueError, ValidationError) as exc:
            raise self._error(_UNSAFE_URL_MESSAGE) from exc

    def _segment_url(
        self,
        uri: str,
        manifest_url: SoundCloudResolvedStreamUrl,
    ) -> HLSSegmentUrl:
        if uri == "":
            raise self._error(_MALFORMED_MESSAGE)
        try:
            return HLSSegmentUrl(value=SecretStr(urljoin(manifest_url.get_secret_value(), uri)))
        except (ValueError, ValidationError) as exc:
            raise self._error(_UNSAFE_URL_MESSAGE) from exc

    def _parse_byte_range(self, value: str) -> HLSByteRange:
        length_text, separator, offset_text = value.partition("@")
        length = self._parse_positive_int(length_text)
        offset = None if not separator else self._parse_non_negative_int(offset_text)
        try:
            return HLSByteRange(length=length, offset=offset)
        except ValidationError as exc:
            raise self._error(_MALFORMED_MESSAGE) from exc

    def _parse_attributes(self, attribute_text: str) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for item in self._split_attribute_list(attribute_text):
            if "=" not in item:
                raise self._error(_MALFORMED_MESSAGE)
            name, value = item.split("=", 1)
            name = name.strip().upper()
            if name == "" or value.strip() == "":
                raise self._error(_MALFORMED_MESSAGE)
            attributes[name] = self._unquote(value.strip())
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
        if in_quotes:
            raise self._error(_MALFORMED_MESSAGE)
        if current:
            items.append("".join(current).strip())
        return tuple(items)

    def _parse_positive_float(self, value: str, message: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise self._error(message) from exc
        if parsed <= 0:
            raise self._error(message)
        return parsed

    def _parse_positive_int(self, value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise self._error(_MALFORMED_MESSAGE) from exc
        if parsed <= 0:
            raise self._error(_MALFORMED_MESSAGE)
        return parsed

    def _parse_non_negative_int(self, value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise self._error(_MALFORMED_MESSAGE) from exc
        if parsed < 0:
            raise self._error(_MALFORMED_MESSAGE)
        return parsed

    def _unquote(self, value: str) -> str:
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            return value[1:-1]
        return value

    def _error(self, message: str) -> HLSSegmentPlanningError:
        return HLSSegmentPlanningError(ErrorCode.MANIFEST_UNSUPPORTED, message)
