import asyncio
import logging
import socket
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import ArtifactStoragePort
from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ArtifactMetadata,
    ArtifactRelativePath,
    HLSByteRange,
    HLSSegmentPlan,
    HLSSegmentReference,
    HLSSegmentUrl,
    SoundCloudResolvedStreamUrl,
)
from soundcloud_downloader.infrastructure.http import (
    HttpMethod,
    HttpRequest,
    NetworkDisabledError,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.soundcloud import (
    HLSSegmentFetcher,
    HLSSegmentFetchError,
    redact_hls_segment_request,
)
from soundcloud_downloader.infrastructure.storage import LocalArtifactStorage

T = TypeVar("T")

MANIFEST_URL = "https://media.soundcloud.test/path/playlist.m3u8?Policy=dummy-manifest"
SEGMENT_URL_0 = "https://media.soundcloud.test/path/segment0.ts?Policy=dummy-segment-0"
SEGMENT_URL_1 = "https://media.soundcloud.test/path/segment1.ts?Policy=dummy-segment-1"
SEGMENT_BYTES_0 = b"SEGMENT-BYTES-0"
SEGMENT_BYTES_1 = b"SEGMENT-BYTES-1"


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


def test_allow_network_false_propagates_network_disabled_and_transport_is_not_called(
    tmp_path: Path,
) -> None:
    captured_requests: list[httpx.Request] = []

    with pytest.raises(NetworkDisabledError):
        run(
            _stage_with_responses(
                tmp_path,
                allow_network=False,
                captured_requests=captured_requests,
            )
        )

    assert captured_requests == []


def test_filesystem_writes_disabled_fails_during_storage_safely(tmp_path: Path) -> None:
    with pytest.raises(HLSSegmentFetchError) as exc_info:
        run(_stage_with_responses(tmp_path, allow_filesystem_writes=False))

    assert MANIFEST_URL not in str(exc_info.value)
    assert SEGMENT_URL_0 not in str(exc_info.value)


def test_successful_fetch_stages_one_segment(tmp_path: Path) -> None:
    result = run(_stage_with_responses(tmp_path, plan=_plan(segment_count=1)))

    assert result.segment_count == 1
    assert result.segments[0].index == 0


def test_successful_fetch_stages_multiple_segments_in_order(tmp_path: Path) -> None:
    result = run(_stage_with_responses(tmp_path))

    assert [segment.index for segment in result.segments] == [0, 1]


def test_staged_artifacts_are_written_under_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    run(_stage_with_responses(tmp_path, storage_root=root))

    for file_path in root.rglob("*"):
        assert file_path.resolve().is_relative_to(root.resolve())


def test_staged_artifact_paths_do_not_contain_segment_url_parts(tmp_path: Path) -> None:
    result = run(_stage_with_responses(tmp_path))
    paths = [segment.artifact.relative_path.value for segment in result.segments]

    assert all("segment0" not in path for path in paths)
    assert all("media.soundcloud.test" not in path for path in paths)


def test_staged_artifact_paths_do_not_contain_query_parameters(tmp_path: Path) -> None:
    result = run(_stage_with_responses(tmp_path))

    assert all("?" not in segment.artifact.relative_path.value for segment in result.segments)
    assert all("Policy" not in segment.artifact.relative_path.value for segment in result.segments)


def test_staged_artifacts_contain_expected_bytes(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    result = run(_stage_with_responses(tmp_path, storage_root=root))
    storage = LocalArtifactStorage(_settings(root, allow_filesystem_writes=False))

    assert storage.read_bytes(relative_path=result.segments[0].artifact.relative_path) == SEGMENT_BYTES_0
    assert storage.read_bytes(relative_path=result.segments[1].artifact.relative_path) == SEGMENT_BYTES_1


def test_staging_result_contains_segment_count(tmp_path: Path) -> None:
    assert run(_stage_with_responses(tmp_path)).segment_count == 2


def test_staging_result_contains_total_bytes(tmp_path: Path) -> None:
    assert run(_stage_with_responses(tmp_path)).total_bytes == len(SEGMENT_BYTES_0) + len(
        SEGMENT_BYTES_1
    )


def test_staging_result_preserves_segment_durations(tmp_path: Path) -> None:
    result = run(_stage_with_responses(tmp_path))

    assert [segment.duration_seconds for segment in result.segments] == [6.0, 6.5]


def test_fetcher_sends_get_requests(tmp_path: Path) -> None:
    captured_requests: list[httpx.Request] = []

    run(_stage_with_responses(tmp_path, captured_requests=captured_requests))

    assert [request.method for request in captured_requests] == ["GET", "GET"]


def test_fetcher_uses_segment_urls_internally(tmp_path: Path) -> None:
    captured_requests: list[httpx.Request] = []

    run(_stage_with_responses(tmp_path, captured_requests=captured_requests))

    assert str(captured_requests[0].url) == SEGMENT_URL_0
    assert str(captured_requests[1].url) == SEGMENT_URL_1


def test_fetcher_sends_accept_any_header(tmp_path: Path) -> None:
    captured_requests: list[httpx.Request] = []

    run(_stage_with_responses(tmp_path, captured_requests=captured_requests))

    assert captured_requests[0].headers["accept"] == "*/*"


def test_fetcher_sends_range_header_for_byte_range_segment_with_offset(tmp_path: Path) -> None:
    captured_requests: list[httpx.Request] = []

    run(
        _stage_with_responses(
            tmp_path,
            plan=_plan(byte_ranges=(HLSByteRange(length=10, offset=5),)),
            captured_requests=captured_requests,
        )
    )

    assert captured_requests[0].headers["range"] == "bytes=5-14"


def test_fetcher_sends_range_header_for_byte_range_segment_without_offset(tmp_path: Path) -> None:
    captured_requests: list[httpx.Request] = []

    run(
        _stage_with_responses(
            tmp_path,
            plan=_plan(byte_ranges=(HLSByteRange(length=10),)),
            captured_requests=captured_requests,
        )
    )

    assert captured_requests[0].headers["range"] == "bytes=0-9"


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500])
def test_error_status_raises_hls_segment_fetch_error(tmp_path: Path, status_code: int) -> None:
    with pytest.raises(HLSSegmentFetchError):
        run(_stage_with_responses(tmp_path, status_code=status_code))


def test_empty_response_body_raises_hls_segment_fetch_error(tmp_path: Path) -> None:
    with pytest.raises(HLSSegmentFetchError):
        run(_stage_with_responses(tmp_path, response_bodies={SEGMENT_URL_0: b""}))


def test_storage_write_failure_raises_hls_segment_fetch_error_safely(tmp_path: Path) -> None:
    class FailingStorage:
        def write_bytes(
            self,
            *,
            relative_path: ArtifactRelativePath,
            data: bytes,
        ) -> ArtifactMetadata:
            raise RuntimeError("storage failed")

        def read_bytes(self, *, relative_path: ArtifactRelativePath) -> bytes:
            raise AssertionError("not used")

        def exists(self, *, relative_path: ArtifactRelativePath) -> bool:
            raise AssertionError("not used")

        def delete(self, *, relative_path: ArtifactRelativePath) -> None:
            raise AssertionError("not used")

    with pytest.raises(HLSSegmentFetchError) as exc_info:
        run(_stage_with_responses(tmp_path, storage=FailingStorage()))

    assert SEGMENT_URL_0 not in str(exc_info.value)
    assert MANIFEST_URL not in str(exc_info.value)


def test_caplog_does_not_contain_segment_url(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    run(_stage_with_responses(tmp_path))

    assert SEGMENT_URL_0 not in caplog.text
    assert SEGMENT_URL_1 not in caplog.text


def test_caplog_does_not_contain_manifest_url(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    run(_stage_with_responses(tmp_path))

    assert MANIFEST_URL not in caplog.text


def test_caplog_does_not_contain_segment_bytes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    run(_stage_with_responses(tmp_path))

    assert SEGMENT_BYTES_0.decode("ascii") not in caplog.text


def test_error_messages_do_not_contain_segment_url(tmp_path: Path) -> None:
    with pytest.raises(HLSSegmentFetchError) as exc_info:
        run(_stage_with_responses(tmp_path, status_code=404))

    assert SEGMENT_URL_0 not in str(exc_info.value)


def test_error_messages_do_not_contain_manifest_url(tmp_path: Path) -> None:
    with pytest.raises(HLSSegmentFetchError) as exc_info:
        run(_stage_with_responses(tmp_path, status_code=404))

    assert MANIFEST_URL not in str(exc_info.value)


def test_error_messages_do_not_contain_segment_bytes(tmp_path: Path) -> None:
    with pytest.raises(HLSSegmentFetchError) as exc_info:
        run(_stage_with_responses(tmp_path, response_bodies={SEGMENT_URL_0: b""}))

    assert SEGMENT_BYTES_0.decode("ascii") not in str(exc_info.value)


def test_redact_hls_segment_request_redacts_url() -> None:
    redacted = redact_hls_segment_request(
        HttpRequest(method=HttpMethod.GET, url=SEGMENT_URL_0, headers={"accept": "*/*"})
    )

    assert redacted["url"] == "[REDACTED]"
    assert SEGMENT_URL_0 not in str(redacted)


def test_redact_hls_segment_request_preserves_safe_range_header() -> None:
    redacted = redact_hls_segment_request(
        HttpRequest(
            method=HttpMethod.GET,
            url=SEGMENT_URL_0,
            headers={"accept": "*/*", "range": "bytes=0-1023"},
        )
    )

    assert redacted == {
        "method": "GET",
        "url": "[REDACTED]",
        "headers": {"accept": "*/*", "range": "bytes=0-1023"},
    }


def test_fetcher_satisfies_no_real_network_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("real network calls are not allowed")

    monkeypatch.setattr(socket, "create_connection", fail_socket)

    assert run(_stage_with_responses(tmp_path)).segment_count == 2


def test_tests_write_only_inside_pytest_tmp_path(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    run(_stage_with_responses(tmp_path, storage_root=root))

    for file_path in root.rglob("*"):
        assert file_path.resolve().is_relative_to(tmp_path.resolve())


async def _stage_with_responses(
    tmp_path: Path,
    *,
    allow_network: bool = True,
    allow_filesystem_writes: bool = True,
    status_code: int = 200,
    response_bodies: dict[str, bytes] | None = None,
    plan: HLSSegmentPlan | None = None,
    storage_root: Path | None = None,
    storage: ArtifactStoragePort | None = None,
    captured_requests: list[httpx.Request] | None = None,
):
    bodies = response_bodies or {
        SEGMENT_URL_0: SEGMENT_BYTES_0,
        SEGMENT_URL_1: SEGMENT_BYTES_1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(request)
        return httpx.Response(
            status_code,
            content=bodies.get(str(request.url), SEGMENT_BYTES_0),
            request=request,
        )

    root = storage_root or tmp_path / "artifacts"
    segment_storage = storage or LocalArtifactStorage(
        _settings(root, allow_filesystem_writes=allow_filesystem_writes)
    )
    async with SafeAsyncHttpClient(
        AppSettings(
            allow_network=allow_network,
            http_max_retries=0,
            http_timeout_seconds=5.0,
            http_backoff_base_seconds=0.0,
        ),
        transport=httpx.MockTransport(handler),
    ) as http_client:
        return await HLSSegmentFetcher(
            http_client=http_client,
            storage=segment_storage,
        ).stage_segments(plan=plan or _plan())


def _settings(root: Path, *, allow_filesystem_writes: bool = True) -> AppSettings:
    return AppSettings(
        allow_filesystem_writes=allow_filesystem_writes,
        artifact_storage_root=root,
    )


def _plan(
    *,
    segment_count: int = 2,
    byte_ranges: tuple[HLSByteRange | None, ...] = (),
) -> HLSSegmentPlan:
    segment_urls = (SEGMENT_URL_0, SEGMENT_URL_1)
    durations = (6.0, 6.5)
    segments = tuple(
        HLSSegmentReference(
            index=index,
            url=HLSSegmentUrl(value=SecretStr(segment_urls[index])),
            duration_seconds=durations[index],
            byte_range=byte_ranges[index] if index < len(byte_ranges) else None,
        )
        for index in range(segment_count)
    )
    return HLSSegmentPlan(
        manifest_url=SoundCloudResolvedStreamUrl(value=SecretStr(MANIFEST_URL)),
        segments=segments,
    )
