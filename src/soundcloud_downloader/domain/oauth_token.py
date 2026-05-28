from enum import Enum
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, field_validator

from soundcloud_downloader.domain.oauth import OAuthClientId, OAuthCodeVerifier, OAuthRedirectUri


class OAuthGrantType(str, Enum):
    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


class OAuthAuthorizationCode(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth authorization code must not be empty.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthClientSecret(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth client secret must not be empty.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthAccessToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr
    token_type: str = "OAuth"

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth access token must not be empty.")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, value: str) -> str:
        if value != "OAuth":
            raise ValueError("OAuth access token type must be 'OAuth'.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthRefreshToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: SecretStr

    @field_validator("value")
    @classmethod
    def validate_non_empty(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value() == "":
            raise ValueError("OAuth refresh token must not be empty.")
        return value

    @field_serializer("value", when_used="always")
    def serialize_value(self, value: SecretStr) -> str:
        return str(value)


class OAuthTokenExchangeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_url: str
    grant_type: OAuthGrantType = OAuthGrantType.AUTHORIZATION_CODE
    client_id: OAuthClientId
    client_secret: OAuthClientSecret | None = None
    redirect_uri: OAuthRedirectUri
    code: OAuthAuthorizationCode
    code_verifier: OAuthCodeVerifier

    @field_validator("token_url")
    @classmethod
    def validate_token_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("OAuth token URL must be a non-empty absolute http or https URL.")
        if parsed.query != "":
            raise ValueError("OAuth token URL must not contain a query string.")
        if parsed.fragment != "":
            raise ValueError("OAuth token URL must not contain a fragment.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OAuth token URL must not contain userinfo credentials.")
        return value

    @field_validator("grant_type")
    @classmethod
    def validate_authorization_code_grant(cls, value: OAuthGrantType) -> OAuthGrantType:
        if value is not OAuthGrantType.AUTHORIZATION_CODE:
            raise ValueError("Only OAuth authorization_code token exchange is supported.")
        return value

    def to_form_data(self) -> dict[str, str]:
        form_data = {
            "grant_type": self.grant_type.value,
            "client_id": self.client_id.value.get_secret_value(),
            "redirect_uri": self.redirect_uri.value,
            "code": self.code.value.get_secret_value(),
            "code_verifier": self.code_verifier.value.get_secret_value(),
        }
        if self.client_secret is not None:
            form_data["client_secret"] = self.client_secret.value.get_secret_value()
        return form_data


class OAuthRefreshTokenRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_url: str
    grant_type: OAuthGrantType = OAuthGrantType.REFRESH_TOKEN
    client_id: OAuthClientId
    client_secret: OAuthClientSecret
    refresh_token: OAuthRefreshToken

    @field_validator("token_url")
    @classmethod
    def validate_token_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if value == "" or parsed.scheme not in {"http", "https"} or parsed.netloc == "":
            raise ValueError("OAuth token URL must be a non-empty absolute http or https URL.")
        if parsed.query != "":
            raise ValueError("OAuth token URL must not contain a query string.")
        if parsed.fragment != "":
            raise ValueError("OAuth token URL must not contain a fragment.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OAuth token URL must not contain userinfo credentials.")
        return value

    @field_validator("grant_type")
    @classmethod
    def validate_refresh_token_grant(cls, value: OAuthGrantType) -> OAuthGrantType:
        if value is not OAuthGrantType.REFRESH_TOKEN:
            raise ValueError("Only OAuth refresh_token refresh is supported.")
        return value

    def to_form_data(self) -> dict[str, str]:
        return {
            "grant_type": self.grant_type.value,
            "client_id": self.client_id.value.get_secret_value(),
            "client_secret": self.client_secret.value.get_secret_value(),
            "refresh_token": self.refresh_token.value.get_secret_value(),
        }


class OAuthTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: OAuthAccessToken
    refresh_token: OAuthRefreshToken | None = None
    expires_in: int | None = Field(default=None, gt=0)
    scope: str | None = None

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("OAuth token response scope must not be empty when provided.")
        return value
