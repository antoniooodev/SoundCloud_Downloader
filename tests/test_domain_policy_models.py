import pytest
from pydantic import ValidationError

from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    ErrorCode,
    MediaSource,
    OfflineDecision,
    OutputProfile,
    PolicyDecision,
    SourceProtocol,
    TrackAccessContext,
)
from soundcloud_downloader.domain.enums import MediaCodec, MediaContainer


def test_enum_values_are_stable_lowercase_strings() -> None:
    enums = [
        AccessMode,
        SourceProtocol,
        MediaCodec,
        MediaContainer,
        DRMStatus,
        OutputProfile,
        OfflineDecision,
        ErrorCode,
    ]

    for enum_type in enums:
        for item in enum_type:
            assert isinstance(item.value, str)
            assert item.value == item.value.lower()


def test_media_source_rejects_non_positive_bitrate() -> None:
    with pytest.raises(ValidationError):
        MediaSource(protocol=SourceProtocol.HLS, bitrate_kbps=0)


def test_media_source_is_immutable() -> None:
    source = MediaSource(protocol=SourceProtocol.DOWNLOAD)

    with pytest.raises(ValidationError):
        source.is_downloadable = True


def test_track_access_context_is_immutable() -> None:
    context = TrackAccessContext(access_mode=AccessMode.PUBLIC)

    with pytest.raises(ValidationError):
        context.is_public = True


def test_policy_decision_allow_creates_valid_allowed_decision() -> None:
    decision = PolicyDecision.allow(
        decision=OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
        output_profile=OutputProfile.ORIGINAL,
        reason="Original download is allowed by policy.",
    )

    assert decision.allowed is True
    assert decision.error_code is None
    assert decision.output_profile is OutputProfile.ORIGINAL


def test_policy_decision_deny_creates_valid_denied_decision() -> None:
    decision = PolicyDecision.deny(
        decision=OfflineDecision.DENY_AUTH_REQUIRED,
        error_code=ErrorCode.AUTH_REQUIRED,
        reason="Authentication is required.",
    )

    assert decision.allowed is False
    assert decision.error_code is ErrorCode.AUTH_REQUIRED
    assert decision.output_profile is None


def test_allowed_decision_without_output_profile_is_invalid() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(
            decision=OfflineDecision.ALLOW_AAC_M4A_REMUX,
            allowed=True,
            reason="Missing output profile.",
        )


def test_denied_decision_with_output_profile_is_invalid() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision(
            decision=OfflineDecision.DENY_DRM,
            allowed=False,
            reason="DRM is unsupported.",
            error_code=ErrorCode.DRM_UNSUPPORTED,
            output_profile=OutputProfile.AAC_M4A,
        )


def test_denied_decision_can_carry_unknown_unsafe_error_code() -> None:
    decision = PolicyDecision.deny(
        decision=OfflineDecision.DENY_UNKNOWN_UNSAFE,
        error_code=ErrorCode.UNKNOWN_UNSAFE,
        reason="The source state is ambiguous.",
    )

    assert decision.error_code is ErrorCode.UNKNOWN_UNSAFE
