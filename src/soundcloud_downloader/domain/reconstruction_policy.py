from soundcloud_downloader.domain.enums import (
    AccessMode,
    DRMStatus,
    MediaCodec,
    OfflineDecision,
    OutputProfile,
    SourceProtocol,
)
from soundcloud_downloader.domain.errors import ErrorCode
from soundcloud_downloader.domain.media import MediaSource, TrackAccessContext
from soundcloud_downloader.domain.policy import PolicyDecision


class ReconstructionPolicyEngine:
    def decide(
        self,
        context: TrackAccessContext,
        requested_profile: OutputProfile | None = None,
    ) -> PolicyDecision:
        source = context.source
        if source is None:
            return self._deny(
                OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE,
                ErrorCode.SOURCE_NOT_DOWNLOADABLE,
                "No media source is available for offline reconstruction.",
            )

        drm_decision = self._deny_unsafe_drm(source)
        if drm_decision is not None:
            return drm_decision

        if context.is_preview_only:
            return self._deny(
                OfflineDecision.DENY_PREVIEW_ONLY,
                ErrorCode.PREVIEW_ONLY,
                "Preview-only tracks cannot be reconstructed offline.",
            )

        if context.access_mode is AccessMode.PUBLIC:
            return self._decide_public(context, source, requested_profile)

        if context.access_mode is AccessMode.GO_PLUS:
            return self._decide_go_plus(context, source, requested_profile)

        return self._deny(
            OfflineDecision.DENY_UNKNOWN_UNSAFE,
            ErrorCode.UNKNOWN_UNSAFE,
            "The access mode is unknown, so reconstruction is denied.",
        )

    def _deny_unsafe_drm(self, source: MediaSource) -> PolicyDecision | None:
        if source.drm_status is DRMStatus.ENCRYPTED_HLS:
            return self._deny(
                OfflineDecision.DENY_DRM,
                ErrorCode.ENCRYPTED_STREAM_UNSUPPORTED,
                "Encrypted HLS streams are not supported for offline reconstruction.",
            )
        if source.drm_status is DRMStatus.EME_DRM:
            return self._deny(
                OfflineDecision.DENY_DRM,
                ErrorCode.DRM_UNSUPPORTED,
                "DRM-protected streams are not supported for offline reconstruction.",
            )
        if source.drm_status is DRMStatus.UNKNOWN:
            return self._deny(
                OfflineDecision.DENY_UNKNOWN_UNSAFE,
                ErrorCode.UNKNOWN_UNSAFE,
                "The source DRM state is unknown, so reconstruction is denied.",
            )
        return None

    def _decide_public(
        self,
        context: TrackAccessContext,
        source: MediaSource,
        requested_profile: OutputProfile | None,
    ) -> PolicyDecision:
        if context.is_go_plus_track:
            return self._deny(
                OfflineDecision.DENY_GO_PLUS_REQUIRED,
                ErrorCode.GO_PLUS_REQUIRED,
                "Go+ tracks require Go+ access for offline reconstruction.",
            )
        if source.requires_auth:
            return self._deny(
                OfflineDecision.DENY_AUTH_REQUIRED,
                ErrorCode.AUTH_REQUIRED,
                "This source requires authentication before reconstruction.",
            )
        if requested_profile is OutputProfile.AAC_M4A:
            return self._deny(
                OfflineDecision.DENY_GO_PLUS_REQUIRED,
                ErrorCode.GO_PLUS_REQUIRED,
                "AAC/M4A reconstruction requires authenticated Go+ access.",
            )

        if requested_profile in {
            OutputProfile.ORIGINAL,
            OutputProfile.MP3_128,
            OutputProfile.WAV_EXPORT,
        }:
            return self._allow_downloadable_profile(context, source, requested_profile)

        if requested_profile is None and self._has_downloadable_source(context, source):
            return self._allow(
                OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
                OutputProfile.ORIGINAL,
                "An official downloadable source is available.",
            )

        return self._deny_source_not_downloadable()

    def _decide_go_plus(
        self,
        context: TrackAccessContext,
        source: MediaSource,
        requested_profile: OutputProfile | None,
    ) -> PolicyDecision:
        if not context.is_authenticated:
            return self._deny(
                OfflineDecision.DENY_AUTH_REQUIRED,
                ErrorCode.AUTH_REQUIRED,
                "Go+ reconstruction requires an authenticated context.",
            )
        if context.is_go_plus_track and not context.has_go_plus:
            return self._deny(
                OfflineDecision.DENY_GO_PLUS_REQUIRED,
                ErrorCode.GO_PLUS_REQUIRED,
                "The authenticated account does not have the required Go+ entitlement.",
            )
        if context.offline_allowed is False:
            return self._deny(
                OfflineDecision.DENY_RIGHTS_RESTRICTED,
                ErrorCode.RIGHTS_RESTRICTED,
                "Rights metadata does not allow offline reconstruction.",
            )

        if requested_profile in {
            OutputProfile.ORIGINAL,
            OutputProfile.MP3_128,
            OutputProfile.WAV_EXPORT,
        }:
            return self._allow_downloadable_profile(context, source, requested_profile)

        if requested_profile is OutputProfile.AAC_M4A or requested_profile is None:
            if self._supports_aac_m4a_remux(source):
                return self._allow(
                    OfflineDecision.ALLOW_AAC_M4A_REMUX,
                    OutputProfile.AAC_M4A,
                    "The source is non-DRM HLS AAC and can be remuxed as AAC/M4A.",
                )
            if requested_profile is None and self._has_downloadable_source(context, source):
                return self._allow(
                    OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
                    OutputProfile.ORIGINAL,
                    "AAC/M4A is unavailable, but an original downloadable source is available.",
                )
            return self._deny(
                OfflineDecision.DENY_UNSUPPORTED_FORMAT,
                ErrorCode.MANIFEST_UNSUPPORTED,
                "The source format is not supported for the requested reconstruction profile.",
            )

        return self._deny(
            OfflineDecision.DENY_UNKNOWN_UNSAFE,
            ErrorCode.UNKNOWN_UNSAFE,
            "The requested output profile is unknown, so reconstruction is denied.",
        )

    def _allow_downloadable_profile(
        self,
        context: TrackAccessContext,
        source: MediaSource,
        output_profile: OutputProfile,
    ) -> PolicyDecision:
        if not self._has_downloadable_source(context, source):
            return self._deny_source_not_downloadable()

        decision = {
            OutputProfile.ORIGINAL: OfflineDecision.ALLOW_ORIGINAL_DOWNLOAD,
            OutputProfile.MP3_128: OfflineDecision.ALLOW_MP3_128_RECONSTRUCTION,
            OutputProfile.WAV_EXPORT: OfflineDecision.ALLOW_WAV_EXPORT,
        }[output_profile]
        return self._allow(
            decision,
            output_profile,
            "A downloadable source is available for the requested output profile.",
        )

    def _has_downloadable_source(
        self,
        context: TrackAccessContext,
        source: MediaSource,
    ) -> bool:
        return context.is_downloadable or context.is_own_track or source.is_downloadable

    def _supports_aac_m4a_remux(self, source: MediaSource) -> bool:
        return (
            source.protocol is SourceProtocol.HLS
            and source.codec is MediaCodec.AAC
            and source.drm_status is DRMStatus.NONE
        )

    def _allow(
        self,
        decision: OfflineDecision,
        output_profile: OutputProfile,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision.allow(
            decision=decision,
            output_profile=output_profile,
            reason=reason,
        )

    def _deny(
        self,
        decision: OfflineDecision,
        error_code: ErrorCode,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision.deny(
            decision=decision,
            error_code=error_code,
            reason=reason,
        )

    def _deny_source_not_downloadable(self) -> PolicyDecision:
        return self._deny(
            OfflineDecision.DENY_SOURCE_NOT_DOWNLOADABLE,
            ErrorCode.SOURCE_NOT_DOWNLOADABLE,
            "No allowed downloadable source is available for offline reconstruction.",
        )
