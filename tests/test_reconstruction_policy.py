import pytest

from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    MediaCodec,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    ReconstructionPolicyEngine,
    SourceProtocol,
    TrackAccessContext,
)


@pytest.fixture
def engine() -> ReconstructionPolicyEngine:
    return ReconstructionPolicyEngine()


def source(
    *,
    protocol: SourceProtocol = SourceProtocol.DOWNLOAD,
    codec: MediaCodec = MediaCodec.MP3,
    requires_auth: bool = False,
    is_downloadable: bool = False,
    drm_status: DRMStatus = DRMStatus.NONE,
) -> MediaSource:
    return MediaSource(
        protocol=protocol,
        codec=codec,
        requires_auth=requires_auth,
        is_downloadable=is_downloadable,
        drm_status=drm_status,
    )


def context(
    *,
    access_mode: AccessMode = AccessMode.PUBLIC,
    media_source: MediaSource | None = None,
    is_authenticated: bool = False,
    has_go_plus: bool = False,
    is_go_plus_track: bool = False,
    is_preview_only: bool = False,
    is_downloadable: bool = False,
    is_own_track: bool = False,
    offline_allowed: bool | None = None,
) -> TrackAccessContext:
    return TrackAccessContext(
        access_mode=access_mode,
        is_authenticated=is_authenticated,
        has_go_plus=has_go_plus,
        is_go_plus_track=is_go_plus_track,
        is_preview_only=is_preview_only,
        is_downloadable=is_downloadable,
        is_own_track=is_own_track,
        offline_allowed=offline_allowed,
        source=media_source,
    )


@pytest.mark.parametrize(
    ("access_context", "requested_profile", "expected_decision", "expected_error"),
    [
        (
            context(media_source=None),
            None,
            OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE,
            ErrorCode.SOURCE_NOT_DOWNLOADABLE,
        ),
        (
            context(media_source=source(drm_status=DRMStatus.ENCRYPTED_HLS)),
            None,
            OfflineDecision.DENY_DRM,
            ErrorCode.ENCRYPTED_STREAM_UNSUPPORTED,
        ),
        (
            context(media_source=source(drm_status=DRMStatus.EME_DRM)),
            None,
            OfflineDecision.DENY_DRM,
            ErrorCode.DRM_UNSUPPORTED,
        ),
        (
            context(media_source=source(drm_status=DRMStatus.UNKNOWN)),
            None,
            OfflineDecision.DENY_UNKNOWN_UNSAFE,
            ErrorCode.UNKNOWN_UNSAFE,
        ),
        (
            context(media_source=source(), is_preview_only=True),
            None,
            OfflineDecision.DENY_PREVIEW_ONLY,
            ErrorCode.PREVIEW_ONLY,
        ),
        (
            context(media_source=source(), is_go_plus_track=True),
            None,
            OfflineDecision.DENY_GO_PLUS_REQUIRED,
            ErrorCode.GO_PLUS_REQUIRED,
        ),
        (
            context(media_source=source(requires_auth=True)),
            None,
            OfflineDecision.DENY_AUTH_REQUIRED,
            ErrorCode.AUTH_REQUIRED,
        ),
        (
            context(media_source=source()),
            OutputProfile.AAC_M4A,
            OfflineDecision.DENY_GO_PLUS_REQUIRED,
            ErrorCode.GO_PLUS_REQUIRED,
        ),
        (
            context(media_source=source(), access_mode=AccessMode.GO_PLUS),
            None,
            OfflineDecision.DENY_AUTH_REQUIRED,
            ErrorCode.AUTH_REQUIRED,
        ),
        (
            context(
                media_source=source(),
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                is_go_plus_track=True,
                has_go_plus=False,
            ),
            None,
            OfflineDecision.DENY_GO_PLUS_REQUIRED,
            ErrorCode.GO_PLUS_REQUIRED,
        ),
        (
            context(
                media_source=source(),
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                has_go_plus=True,
                offline_allowed=False,
            ),
            None,
            OfflineDecision.DENY_RIGHTS_RESTRICTED,
            ErrorCode.RIGHTS_RESTRICTED,
        ),
        (
            context(
                media_source=source(protocol=SourceProtocol.PROGRESSIVE),
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                has_go_plus=True,
                offline_allowed=True,
            ),
            None,
            OfflineDecision.DENY_UNSUPPORTED_FORMAT,
            ErrorCode.MANIFEST_UNSUPPORTED,
        ),
        (
            context(
                media_source=source(protocol=SourceProtocol.PROGRESSIVE),
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                has_go_plus=True,
                offline_allowed=True,
            ),
            OutputProfile.AAC_M4A,
            OfflineDecision.DENY_UNSUPPORTED_FORMAT,
            ErrorCode.MANIFEST_UNSUPPORTED,
        ),
        (
            context(
                media_source=source(protocol=SourceProtocol.PROGRESSIVE),
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                has_go_plus=True,
                offline_allowed=True,
            ),
            OutputProfile.ORIGINAL,
            OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE,
            ErrorCode.SOURCE_NOT_DOWNLOADABLE,
        ),
    ],
)
def test_policy_denials(
    engine: ReconstructionPolicyEngine,
    access_context: TrackAccessContext,
    requested_profile: OutputProfile | None,
    expected_decision: OfflineDecision,
    expected_error: ErrorCode,
) -> None:
    decision = engine.decide(access_context, requested_profile)

    assert decision.allowed is False
    assert decision.decision is expected_decision
    assert decision.error_code is expected_error
    assert decision.reason.strip()


@pytest.mark.parametrize(
    ("requested_profile", "expected_decision", "expected_profile"),
    [
        (
            None,
            OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
            OutputProfile.ORIGINAL,
        ),
        (
            OutputProfile.ORIGINAL,
            OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
            OutputProfile.ORIGINAL,
        ),
        (
            OutputProfile.MP3_128,
            OfflineDecision.ALLOW_MP3_128_RECONSTRUCTION,
            OutputProfile.MP3_128,
        ),
        (
            OutputProfile.WAV_EXPORT,
            OfflineDecision.ALLOW_WAV_EXPORT,
            OutputProfile.WAV_EXPORT,
        ),
    ],
)
def test_public_mode_allows_downloadable_source_profiles(
    engine: ReconstructionPolicyEngine,
    requested_profile: OutputProfile | None,
    expected_decision: OfflineDecision,
    expected_profile: OutputProfile,
) -> None:
    decision = engine.decide(
        context(media_source=source(is_downloadable=True)),
        requested_profile,
    )

    assert decision.allowed is True
    assert decision.decision is expected_decision
    assert decision.output_profile is expected_profile
    assert decision.reason.strip()


def test_public_mode_denies_mp3_128_for_non_downloadable_source(
    engine: ReconstructionPolicyEngine,
) -> None:
    decision = engine.decide(
        context(media_source=source(is_downloadable=False)),
        OutputProfile.MP3_128,
    )

    assert decision.allowed is False
    assert decision.decision is OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE
    assert decision.error_code is ErrorCode.SOURCE_NOT_DOWNLOADABLE
    assert decision.reason.strip()


@pytest.mark.parametrize(
    ("requested_profile", "expected_decision", "expected_profile"),
    [
        (
            OutputProfile.ORIGINAL,
            OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
            OutputProfile.ORIGINAL,
        ),
        (
            OutputProfile.MP3_128,
            OfflineDecision.ALLOW_MP3_128_RECONSTRUCTION,
            OutputProfile.MP3_128,
        ),
        (
            OutputProfile.WAV_EXPORT,
            OfflineDecision.ALLOW_WAV_EXPORT,
            OutputProfile.WAV_EXPORT,
        ),
    ],
)
def test_go_plus_mode_allows_downloadable_source_profiles(
    engine: ReconstructionPolicyEngine,
    requested_profile: OutputProfile,
    expected_decision: OfflineDecision,
    expected_profile: OutputProfile,
) -> None:
    decision = engine.decide(
        context(
            access_mode=AccessMode.GO_PLUS,
            is_authenticated=True,
            has_go_plus=True,
            offline_allowed=True,
            media_source=source(is_downloadable=True),
        ),
        requested_profile,
    )

    assert decision.allowed is True
    assert decision.decision is expected_decision
    assert decision.output_profile is expected_profile
    assert decision.reason.strip()


@pytest.mark.parametrize("requested_profile", [OutputProfile.AAC_M4A, None])
def test_go_plus_mode_allows_aac_m4a_for_hls_aac_non_drm_source(
    engine: ReconstructionPolicyEngine,
    requested_profile: OutputProfile | None,
) -> None:
    decision = engine.decide(
        context(
            access_mode=AccessMode.GO_PLUS,
            is_authenticated=True,
            has_go_plus=True,
            offline_allowed=True,
            media_source=source(protocol=SourceProtocol.HLS, codec=MediaCodec.AAC),
        ),
        requested_profile,
    )

    assert decision.allowed is True
    assert decision.decision is OfflineDecision.ALLOW_AAC_M4A_REMUX
    assert decision.output_profile is OutputProfile.AAC_M4A
    assert decision.reason.strip()


def test_go_plus_mode_falls_back_to_original_for_default_downloadable_source(
    engine: ReconstructionPolicyEngine,
) -> None:
    decision = engine.decide(
        context(
            access_mode=AccessMode.GO_PLUS,
            is_authenticated=True,
            has_go_plus=True,
            offline_allowed=True,
            media_source=source(protocol=SourceProtocol.DOWNLOAD, is_downloadable=True),
        )
    )

    assert decision.allowed is True
    assert decision.decision is OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD
    assert decision.output_profile is OutputProfile.ORIGINAL
    assert decision.reason.strip()


def test_all_decisions_have_non_empty_reasons(engine: ReconstructionPolicyEngine) -> None:
    decisions = [
        engine.decide(context(media_source=None)),
        engine.decide(context(media_source=source(drm_status=DRMStatus.ENCRYPTED_HLS))),
        engine.decide(context(media_source=source(drm_status=DRMStatus.EME_DRM))),
        engine.decide(context(media_source=source(drm_status=DRMStatus.UNKNOWN))),
        engine.decide(context(media_source=source(), is_preview_only=True)),
        engine.decide(context(media_source=source(is_downloadable=True)), OutputProfile.ORIGINAL),
        engine.decide(context(media_source=source(is_downloadable=True)), OutputProfile.MP3_128),
        engine.decide(context(media_source=source(is_downloadable=False)), OutputProfile.MP3_128),
        engine.decide(
            context(
                access_mode=AccessMode.GO_PLUS,
                is_authenticated=True,
                has_go_plus=True,
                offline_allowed=True,
                media_source=source(protocol=SourceProtocol.HLS, codec=MediaCodec.AAC),
            )
        ),
    ]

    assert all(decision.reason.strip() for decision in decisions)
