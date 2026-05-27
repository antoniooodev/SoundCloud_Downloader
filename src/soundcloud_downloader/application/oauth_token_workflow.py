from pydantic import BaseModel, ConfigDict

from soundcloud_downloader.application.oauth_session_service import OAuthAuthorizationSessionService
from soundcloud_downloader.application.ports import OAuthTokenExchangePort
from soundcloud_downloader.domain import (
    OAuthAuthorizationCode,
    OAuthClientSecret,
    OAuthSessionId,
    OAuthState,
    OAuthTokenResponse,
)


class OAuthAuthorizationCodeExchangeWorkflowRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: OAuthSessionId
    returned_state: OAuthState
    authorization_code: OAuthAuthorizationCode
    client_secret: OAuthClientSecret | None = None


class OAuthAuthorizationCodeExchangeWorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    consumed: bool
    token_response: OAuthTokenResponse


class OAuthAuthorizationCodeExchangeWorkflow:
    def __init__(
        self,
        session_service: OAuthAuthorizationSessionService,
        token_exchange: OAuthTokenExchangePort,
    ) -> None:
        self._session_service = session_service
        self._token_exchange = token_exchange

    async def exchange(
        self,
        request: OAuthAuthorizationCodeExchangeWorkflowRequest,
    ) -> OAuthAuthorizationCodeExchangeWorkflowResult:
        consumed_session = self._session_service.consume_session(
            request.session_id,
            returned_state=request.returned_state,
        )
        token_response = await self._token_exchange.exchange_authorization_code(
            client_id=consumed_session.client_id,
            client_secret=request.client_secret,
            redirect_uri=consumed_session.redirect_uri,
            code=request.authorization_code,
            code_verifier=consumed_session.code_verifier,
        )
        return OAuthAuthorizationCodeExchangeWorkflowResult(
            session_id=consumed_session.session_id.value,
            consumed=True,
            token_response=token_response,
        )
