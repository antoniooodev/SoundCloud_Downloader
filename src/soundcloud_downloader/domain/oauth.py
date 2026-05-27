import re
from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, SecretStr, field_serializer, field_validator


PKCE_CODE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]+$")


class OAuthCodeChallengeMethod(str, Enum):
    S256 = "S256"


class OAuthResponseType(str, Enum):
    CODE = "code"


class OAuthClientId(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth client ID must not be empty.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthRedirectUri(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value")
    @classmethod
    def validate_absolute_http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("OAuth redirect URI must be a non-empty absolute http or https URL.")
        if parsed.fragment != "":
            raise ValueError("OAuth redirect URI must not contain a fragment.")
        return value


class OAuthCodeVerifier(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_pkce_verifier(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value()
        if not 43 <= len(raw_value) <= 128:
            raise ValueError("OAuth code verifier length must be between 43 and 128 characters.")
        if PKCE_CODE_VERIFIER_PATTERN.fullmatch(raw_value) is None:
            raise ValueError("OAuth code verifier contains characters outside the PKCE allowed set.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthCodeChallenge(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    method: OAuthCodeChallengeMethod = OAuthCodeChallengeMethod.S256

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        if value == "":
            raise ValueError("OAuth code challenge must not be empty.")
        return value


class OAuthState(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth state must not be empty.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthAuthorizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_url: str
    client_id: OAuthClientId
    redirect_uri: OAuthRedirectUri
    response_type: OAuthResponseType = OAuthResponseType.CODE
    code_challenge: OAuthCodeChallenge
    state: OAuthState

    @field_validator("authorization_url")
    @classmethod
    def validate_authorization_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("OAuth authorization URL must be a non-empty absolute URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OAuth authorization URL must not contain userinfo credentials.")
        return value
