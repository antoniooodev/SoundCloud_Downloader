from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from soundcloud_downloader.config import AppSettings
from soundcloud_downloader.domain import (
    ErrorCode,
    OAuthAuthorizationSession,
    OAuthSessionId,
    SoundcloudDownloaderError,
)


class EncryptedOAuthAuthorizationSessionStore:
    def __init__(self, settings: AppSettings) -> None:
        if settings.oauth_session_encryption_key is None:
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "OAuth session encryption key is required.",
            )
        self._path = settings.oauth_session_store_path
        self._allow_filesystem_writes = settings.allow_filesystem_writes
        self._fernet = Fernet(
            settings.oauth_session_encryption_key.get_secret_value().encode("ascii")
        )

    def save(self, session: OAuthAuthorizationSession) -> None:
        self._ensure_filesystem_writes_allowed()
        document = self._read_document()
        document["sessions"][session.session_id.value] = self._serialize_session(session)
        self._write_document(document)

    def get(self, session_id: OAuthSessionId) -> OAuthAuthorizationSession | None:
        document = self._read_document()
        raw_session = document["sessions"].get(session_id.value)
        if raw_session is None:
            return None
        try:
            return OAuthAuthorizationSession.model_validate(raw_session)
        except ValidationError:
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Encrypted OAuth session store contains malformed session data.",
            ) from None

    def delete(self, session_id: OAuthSessionId) -> None:
        self._ensure_filesystem_writes_allowed()
        document = self._read_document()
        document["sessions"].pop(session_id.value, None)
        self._write_document(document)

    def _ensure_filesystem_writes_allowed(self) -> None:
        if not self._allow_filesystem_writes:
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Filesystem writes are disabled for OAuth session storage.",
            )

    def _read_document(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "sessions": {}}
        try:
            encrypted_payload = self._path.read_bytes()
            decrypted_payload = self._fernet.decrypt(encrypted_payload)
            document = json.loads(decrypted_payload.decode("utf-8"))
        except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Encrypted OAuth session store could not be read safely.",
            ) from None
        self._validate_document(document)
        return cast("dict[str, Any]", document)

    def _write_document(self, document: dict[str, Any]) -> None:
        self._validate_document(document)
        try:
            encrypted_payload = self._fernet.encrypt(
                json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="wb",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(encrypted_payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
        except OSError:
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Encrypted OAuth session store could not be written safely.",
            ) from None

    def _validate_document(self, document: Any) -> None:
        if not isinstance(document, dict):
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Encrypted OAuth session store contains malformed data.",
            )
        if document.get("version") != 1 or not isinstance(document.get("sessions"), dict):
            raise SoundcloudDownloaderError(
                ErrorCode.STORAGE_FAILED,
                "Encrypted OAuth session store contains malformed data.",
            )

    def _serialize_session(self, session: OAuthAuthorizationSession) -> dict[str, Any]:
        payload = session.model_dump(mode="json")
        payload["client_id"]["value"] = session.client_id.value.get_secret_value()
        payload["code_verifier"]["value"] = session.code_verifier.value.get_secret_value()
        payload["state"]["value"] = session.state.value.get_secret_value()
        return payload
