from soundcloud_downloader.domain import SoundCloudTranscodingMetadata
from soundcloud_downloader.infrastructure.http.models import HttpMethod, HttpRequest
from soundcloud_downloader.infrastructure.observability import REDACTED_VALUE
from soundcloud_downloader.infrastructure.soundcloud.api_contract import SoundCloudAccessToken


class SoundCloudTranscodingEndpointRequestBuilder:
    def build_request(
        self,
        *,
        transcoding: SoundCloudTranscodingMetadata,
        access_token: SoundCloudAccessToken,
    ) -> HttpRequest:
        return HttpRequest(
            method=HttpMethod.GET,
            url=transcoding.endpoint_url.get_secret_value(),
            headers={
                "accept": "application/json; charset=utf-8",
                "authorization": (
                    f"{access_token.token_type} {access_token.value.get_secret_value()}"
                ),
            },
        )


def redact_transcoding_endpoint_request(request: HttpRequest) -> dict[str, object]:
    redacted = request.model_dump(mode="json")
    redacted["url"] = REDACTED_VALUE
    headers = redacted.get("headers", {})
    if isinstance(headers, dict):
        redacted["headers"] = {
            key: REDACTED_VALUE if key.lower() == "authorization" else value
            for key, value in headers.items()
        }
    return redacted
