import json
from collections.abc import Mapping

from soundcloud_downloader.application.ports import (
    AccessTokenProviderPort,
    SoundCloudResolvedResource,
    SoundCloudResolverPort,
    SoundCloudResolveStatus,
    SoundCloudResourceKind,
)
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import NormalizedResolverInput, SoundCloudResourceType
from soundcloud_downloader.infrastructure.http import (
    HttpRequestError,
    HttpMethod,
    HttpRequest,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.soundcloud.api_contract import (
    SoundCloudAccessToken,
    SoundCloudApiEndpoint,
    SoundCloudApiRequest,
)
from soundcloud_downloader.infrastructure.soundcloud.response_mapper import (
    SoundCloudResponseMapper,
    summarize_soundcloud_payload_shape,
)


class SoundCloudResolveRequestBuilder:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def build(
        self,
        normalized: NormalizedResolverInput,
        token: SoundCloudAccessToken,
    ) -> SoundCloudApiRequest:
        if normalized.normalized_url is None:
            raise ValueError("Normalized SoundCloud URL is required for resolve requests.")
        if "?" in normalized.normalized_url or "#" in normalized.normalized_url:
            raise ValueError("Normalized SoundCloud URL must not contain query or fragment.")

        return SoundCloudApiRequest(
            method=HttpMethod.GET,
            url=f"{self._settings.soundcloud_api_base_url}/{SoundCloudApiEndpoint.RESOLVE.value}",
            headers={
                "Authorization": f"{token.token_type} {token.value.get_secret_value()}",
                "accept": "application/json; charset=utf-8",
            },
            params={"url": normalized.normalized_url},
        )


class OfficialSoundCloudResolver(SoundCloudResolverPort):
    def __init__(
        self,
        settings: AppSettings,
        http_client: SafeAsyncHttpClient,
        token_provider: AccessTokenProviderPort,
        mapper: SoundCloudResponseMapper | None = None,
        request_builder: SoundCloudResolveRequestBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._token_provider = token_provider
        self._mapper = mapper or SoundCloudResponseMapper()
        self._request_builder = request_builder or SoundCloudResolveRequestBuilder(settings)

    async def resolve(
        self,
        normalized: NormalizedResolverInput,
    ) -> SoundCloudResolvedResource:
        payload, error = await self._fetch_payload(normalized)
        if error is not None:
            return error
        if payload is None:
            return self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve returned a non-object JSON payload.",
                invalid_fields=("unknown",),
            )
        return self._mapper.map_resolved_resource(payload, normalized)

    async def resolve_with_payload_shape(
        self,
        normalized: NormalizedResolverInput,
    ) -> tuple[SoundCloudResolvedResource, dict[str, object]]:
        payload, error = await self._fetch_payload(normalized)
        if error is not None:
            return error, {}
        if payload is None:
            return (
                self._resource(
                    SoundCloudResolveStatus.ERROR,
                    SoundCloudResourceKind.UNKNOWN,
                    normalized,
                    "Official SoundCloud resolve returned a non-object JSON payload.",
                    invalid_fields=("unknown",),
                ),
                {},
            )
        return self._mapper.map_resolved_resource(payload, normalized), (
            summarize_soundcloud_payload_shape(payload)
        )

    async def _fetch_payload(
        self,
        normalized: NormalizedResolverInput,
    ) -> tuple[Mapping[str, object] | None, SoundCloudResolvedResource | None]:
        if normalized.normalized_url is None:
            return None, self._resource(
                SoundCloudResolveStatus.UNSUPPORTED,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve requires a normalized URL.",
            )
        if normalized.resource_type is SoundCloudResourceType.UNKNOWN:
            return None, self._resource(
                SoundCloudResolveStatus.UNSUPPORTED,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve does not support unknown inputs.",
            )

        token = await self._token_provider.get_access_token()
        api_request = self._request_builder.build(normalized, token)
        try:
            response = await self._http_client.request(
                HttpRequest(
                    method=api_request.method,
                    url=api_request.url,
                    headers=api_request.headers,
                    params=api_request.params,
                    follow_redirects=True,
                    max_redirects=3,
                )
            )
        except HttpRequestError:
            return None, self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Unable to resolve SoundCloud URL.",
            )

        if response.status_code == 404:
            return None, self._resource(
                SoundCloudResolveStatus.NOT_FOUND,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve resource was not found.",
            )
        if response.status_code in {401, 403}:
            return None, self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve authorization failed.",
            )
        if response.status_code == 429:
            return None, self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve was rate limited.",
            )
        if response.status_code < 200 or response.status_code >= 300:
            return None, self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                f"Official SoundCloud resolve returned HTTP {response.status_code}.",
            )

        try:
            payload = json.loads(response.text)
        except json.JSONDecodeError:
            return None, self._resource(
                SoundCloudResolveStatus.ERROR,
                SoundCloudResourceKind.UNKNOWN,
                normalized,
                "Official SoundCloud resolve returned invalid JSON.",
                invalid_fields=("unknown",),
            )

        if not isinstance(payload, Mapping):
            return None, None

        return payload, None

    def _resource(
        self,
        status: SoundCloudResolveStatus,
        kind: SoundCloudResourceKind,
        normalized: NormalizedResolverInput,
        warning: str,
        *,
        invalid_fields: tuple[str, ...] = (),
    ) -> SoundCloudResolvedResource:
        return SoundCloudResolvedResource(
            status=status,
            kind=kind,
            normalized=normalized,
            warnings=(warning,),
            invalid_fields=invalid_fields,
        )
