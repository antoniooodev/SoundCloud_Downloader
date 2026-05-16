from enum import Enum


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "auth_required"
    GO_PLUS_REQUIRED = "go_plus_required"
    ENTITLEMENT_DENIED = "entitlement_denied"
    PREVIEW_ONLY = "preview_only"
    DRM_UNSUPPORTED = "drm_unsupported"
    ENCRYPTED_STREAM_UNSUPPORTED = "encrypted_stream_unsupported"
    RIGHTS_RESTRICTED = "rights_restricted"
    MANIFEST_UNSUPPORTED = "manifest_unsupported"
    SOURCE_NOT_DOWNLOADABLE = "source_not_downloadable"
    NETWORK_RETRYABLE = "network_retryable"
    NETWORK_PERMANENT = "network_permanent"
    FFMPEG_FAILED = "ffmpeg_failed"
    STORAGE_FAILED = "storage_failed"
    DATABASE_FAILED = "database_failed"
    UNKNOWN_UNSAFE = "unknown_unsafe"


class SoundcloudDownloaderError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")
