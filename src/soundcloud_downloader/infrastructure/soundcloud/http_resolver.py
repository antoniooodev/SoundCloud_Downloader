import json
from collections.abc import Mapping

from soundcloud_downloader.application.ports import (
    SoundCloudResolvedResource,
    SoundCloudResolverPort,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    NormalizedResolverInput,
    SoundCloudResourceType,
)
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.soundcloud.response_mapper import (
    SoundCloudResponseMapper,
)


class SoundCloudHttpResolver(SoundCloudResolverPort):
    def __init__(
        self,
        settings: AppSettings,
        http_client: SafeAsyncHttpClient,
        mapper: SoundCloudResponseMapper | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._mapper = mapper or SoundCloudResponseMapper()

    async def resolve(
        self,
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        if self._settings.soundcloud_resolve_endpoint is None:
            return self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "SoundCloud resolve endpoint is not configured.",
            )

        if (
            not normalized.requires_network_resolution
            and normalized.resource_type is SoundCloudResourceType.UNKNOWN
        ):
            return self._resource(
                SoundCloudResolveStatus.UNSUPPORTED,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Normalized input is unsupported for SoundCloud HTTP resolution.",
            )

        response = await self._http_client.request(
            HttpRequest(
                method=HttpMethod.GET,
                url=self._settings.soundcloud_resolve_endpoint,
                params=self._params(normalized),
            )
        )

        if response.status_code == 404:
            return self._resource(
                SoundCloudResolveStatus.NOT_FOUND,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "SoundCloud resolver resource was not found.",
            )
        if response.status_code < 200 or response.status_code >= 300:
            return self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                f"SoundCloud resolver returned HTTP {response.status_code}.",
            )

        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "SoundCloud resolver returned invalid JSON.",
            )

        if not isinstance(payload, Mapping):
            return self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "SoundCloud resolver returned a non-object JSON payload.",
            )

        return self._mapper.map_resolved_resource(payload, normalized)

    def _params(self, normalized: NormalizedResolverInput) -> dict[str, str]:
        params = {"resource_type": normalized.resource_type.value}
        if normalized.normalized_url is not None:
            params["url"] = normalized.normalized_url
        if normalized.normalized_path is not None:
            params["path"] = normalized.normalized_path
        return params

    def _resource(
        self,
        status: SoundCloudResolveStatus,
        kind: SoundCloudResourceKind,
        normalized: NormalizedResolverInput,
        warning: str,
    ) -> SoundCloudResolvedResource:
        return SoundCloudResolvedResource(
            status=status,
            kind=kind,
            normalized=normalized,
            warnings=(warning,),
        )
