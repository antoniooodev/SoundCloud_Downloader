from enum import Enum


class AccessMode(str, Enum):
    PUBLIC = "public"
    GO_PLUS = "go_plus"


class SourceProtocol(str, Enum):
    HLS = "hls"
    PROGRESSIVE = "progressive"
    DOWNLOAD = "download"
    UNKNOWN = "unknown"


class MediaCodec(str, Enum):
    AAC = "aac"
    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    UNKNOWN = "unknown"


class MediaContainer(str, Enum):
    M4A = "m4a"
    MP3 = "mp3"
    WAV = "wav"
    FLAC = "flac"
    ORIGINAL = "original"
    UNKNOWN = "unknown"


class DRMStatus(str, Enum):
    NONE = "none"
    ENCRYPTED_HLS = "encrypted_hls"
    EME_DRM = "eme_drm"
    UNKNOWN = "unknown"


class OutputProfile(str, Enum):
    ORIGINAL = "original"
    MP3_128 = "mp3_128"
    AAC_M4A = "aac_m4a"
    WAV_EXPORT = "wav_export"


class OfflineDecision(str, Enum):
    ALLOW_ORIGINAL_DOWNLOAD = "allow_original_download"
    ALLOW_MP3_128_RECONSTRUCTION = "allow_mp3_128_reconstruction"
    ALLOW_AAC_M4A_REMUX = "allow_aac_m4a_remux"
    ALLOW_WAV_EXPORT = "allow_wav_export"
    DENY_AUTH_REQUIRED = "deny_auth_required"
    DENY_GO_PLUS_REQUIRED = "deny_go_plus_required"
    DENY_ENTITLEMENT = "deny_entitlement"
    DENY_PREVIEW_ONLY = "deny_preview_only"
    DENY_DRM = "deny_drm"
    DENY_RIGHTS_RESTRICTED = "deny_rights_restricted"
    DENY_SOURCE_NOT_DOWNLOADABLE = "deny_source_not_downloadable"
    DENY_UNSUPPORTED_FORMAT = "deny_unsupported_format"
    DENY_UNKNOWN_UNSAFE = "deny_unknown_unsafe"
