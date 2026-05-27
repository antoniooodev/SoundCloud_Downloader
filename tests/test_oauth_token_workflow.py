import asyncio
import socket
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from soundcloud_downloader.application import (
    InMemoryOAuthAuthorizationSessionStore,
    OAuthAuthorizationCodeExchangeWorkflow,
    OAuthAuthorizationCodeExchangeWorkflowRequest,
    OAuthAuthorizationCodeExchangeWorkflowResult,
    OAuthAuthorizationSessionService,
)
from soundcloud_downloader.application.ports import OAuthTokenExchangePort
from soundcloud_downloader.domain import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthAuthorizationSession,
    OAuthClientId,
    OAuthClientSecret,
    OAuthCodeChallenge,
    OAuthCodeVerifier,
    OAuthRedirectUri,
    OAuthRefreshToken,
    OAuthSessionId,
    OAuthSessionStatus,
    OAuthState,
    OAuthTokenResponse,
    SoundcloudDownloaderError,
)


RAW_ACCESS_TOKEN = "dummy-access-token"
RAW_REFRESH_TOKEN = "dummy-refresh-token"
RAW_AUTHORIZATION_CODE = "dummy-authorization-code"
RAW_CODE_VERIFIER = "A" * 64
RAW_CLIENT_SECRET = "dummy-client-secret"


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)


class FakeTokenExchange:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def exchange_authorization_code(
        self,
        *,
        client_id: OAuthClientId,
        client_secret: OAuthClientSecret | None,
        redirect_uri: OAuthRedirectUri,
        code: OAuthAuthorizationCode,
        code_verifier: OAuthCodeVerifier,
    ) -> OAuthTokenResponse:
        self.calls.append(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "code_verifier": code_verifier,
            }
        )
        return _token_response()


def test_workflow_exchanges_code_using_session_client_id_redirect_uri_and_code_verifier() -> None:
    context = _workflow_context()

    run(context.workflow.exchange(_request(context.session)))

    call = context.fake.calls[0]
    assert call["client_id"] == context.session.client_id
    assert call["redirect_uri"] == context.session.redirect_uri
    assert call["code_verifier"] == context.session.code_verifier
    assert call["code"] == _authorization_code()


def test_workflow_passes_client_secret_when_provided() -> None:
    context = _workflow_context()
    client_secret = OAuthClientSecret(value=SecretStr(RAW_CLIENT_SECRET))

    run(context.workflow.exchange(_request(context.session, client_secret=client_secret)))

    assert context.fake.calls[0]["client_secret"] == client_secret


def test_workflow_omits_client_secret_when_none() -> None:
    context = _workflow_context()

    run(context.workflow.exchange(_request(context.session, client_secret=None)))

    assert context.fake.calls[0]["client_secret"] is None


def test_workflow_consumes_the_session_on_success() -> None:
    context = _workflow_context()

    run(context.workflow.exchange(_request(context.session)))

    stored = context.store.get(context.session.session_id)
    assert stored is not None
    assert stored.status is OAuthSessionStatus.CONSUMED


def test_workflow_rejects_wrong_state() -> None:
    context = _workflow_context()

    with pytest.raises(SoundcloudDownloaderError, match="state did not match"):
        run(
            context.workflow.exchange(
                _request(context.session, returned_state=OAuthState(value=SecretStr("wrong-state")))
            )
        )


def test_wrong_state_does_not_call_token_exchange() -> None:
    context = _workflow_context()

    with pytest.raises(SoundcloudDownloaderError):
        run(
            context.workflow.exchange(
                _request(context.session, returned_state=OAuthState(value=SecretStr("wrong-state")))
            )
        )

    assert context.fake.calls == []


def test_workflow_rejects_missing_session() -> None:
    context = _workflow_context(save_session=False)

    with pytest.raises(SoundcloudDownloaderError, match="not found"):
        run(context.workflow.exchange(_request(context.session)))


def test_missing_session_does_not_call_token_exchange() -> None:
    context = _workflow_context(save_session=False)

    with pytest.raises(SoundcloudDownloaderError):
        run(context.workflow.exchange(_request(context.session)))

    assert context.fake.calls == []


def test_workflow_rejects_expired_session() -> None:
    context = _workflow_context(session=_session(created_offset=-1200, expires_offset=-600))

    with pytest.raises(SoundcloudDownloaderError, match="expired"):
        run(context.workflow.exchange(_request(context.session)))


def test_expired_session_does_not_call_token_exchange() -> None:
    context = _workflow_context(session=_session(created_offset=-1200, expires_offset=-600))

    with pytest.raises(SoundcloudDownloaderError):
        run(context.workflow.exchange(_request(context.session)))

    assert context.fake.calls == []


def test_workflow_rejects_already_consumed_session() -> None:
    context = _workflow_context(session=_session().mark_consumed())

    with pytest.raises(SoundcloudDownloaderError, match="already been consumed"):
        run(context.workflow.exchange(_request(context.session)))


def test_already_consumed_session_does_not_call_token_exchange() -> None:
    context = _workflow_context(session=_session().mark_consumed())

    with pytest.raises(SoundcloudDownloaderError):
        run(context.workflow.exchange(_request(context.session)))

    assert context.fake.calls == []


def test_workflow_result_contains_session_id() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)
    assert result.session_id == context.session.session_id.value


def test_workflow_result_consumed_true_on_success() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)
    assert result.consumed is True


def test_workflow_result_repr_does_not_expose_access_token() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert RAW_ACCESS_TOKEN not in repr(result)


def test_workflow_result_repr_does_not_expose_refresh_token() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert RAW_REFRESH_TOKEN not in repr(result)


def test_workflow_result_model_dump_masks_raw_access_token() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))
    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)
    dumped = result.model_dump()

    assert dumped["token_response"]["access_token"]["value"] == "**********"
    assert dumped["token_response"]["refresh_token"]["value"] == "**********"
    assert RAW_ACCESS_TOKEN not in str(dumped)


def test_workflow_result_does_not_include_authorization_code() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)
    assert "authorization_code" not in result.model_dump()


def test_workflow_result_does_not_include_code_verifier() -> None:
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)
    assert "code_verifier" not in result.model_dump()


def test_workflow_request_model_is_immutable() -> None:
    context = _workflow_context()
    request = _request(context.session)

    with pytest.raises(Exception, match="frozen"):
        request.authorization_code = _authorization_code()


def test_workflow_result_model_is_immutable() -> None:
    context = _workflow_context()
    result = run(context.workflow.exchange(_request(context.session)))

    with pytest.raises(Exception, match="frozen"):
        result.consumed = False


def test_fake_token_exchange_satisfies_oauth_token_exchange_port() -> None:
    assert isinstance(FakeTokenExchange(), OAuthTokenExchangePort)


def test_tests_perform_no_real_network_calls(monkeypatch: Any) -> None:
    def fail_network_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("Real network calls are not allowed in OAuth workflow tests.")

    monkeypatch.setattr(socket, "create_connection", fail_network_call)
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)


def test_tests_write_no_files(monkeypatch: Any) -> None:
    def fail_file_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("File writes are not allowed in OAuth workflow tests.")

    monkeypatch.setattr(Path, "write_text", fail_file_write)
    monkeypatch.setattr(Path, "write_bytes", fail_file_write)
    context = _workflow_context()

    result = run(context.workflow.exchange(_request(context.session)))

    assert isinstance(result, OAuthAuthorizationCodeExchangeWorkflowResult)


class _WorkflowContext:
    def __init__(
        self,
        *,
        workflow: OAuthAuthorizationCodeExchangeWorkflow,
        fake: FakeTokenExchange,
        store: InMemoryOAuthAuthorizationSessionStore,
        session: OAuthAuthorizationSession,
    ) -> None:
        self.workflow = workflow
        self.fake = fake
        self.store = store
        self.session = session


def _workflow_context(
    *,
    session: OAuthAuthorizationSession | None = None,
    save_session: bool = True,
) -> _WorkflowContext:
    selected_session = session if session is not None else _session()
    store = InMemoryOAuthAuthorizationSessionStore()
    if save_session:
        store.save(selected_session)
    fake = FakeTokenExchange()
    workflow = OAuthAuthorizationCodeExchangeWorkflow(
        session_service=OAuthAuthorizationSessionService(store=store),
        token_exchange=fake,
    )
    return _WorkflowContext(
        workflow=workflow,
        fake=fake,
        store=store,
        session=selected_session,
    )


def _session(
    *,
    created_offset: int = 0,
    expires_offset: int = 600,
) -> OAuthAuthorizationSession:
    now = datetime.now(timezone.utc)
    created_at = now + timedelta(seconds=created_offset)
    expires_at = now + timedelta(seconds=expires_offset)
    return OAuthAuthorizationSession(
        session_id=OAuthSessionId(value="session-id"),
        client_id=OAuthClientId(value=SecretStr("client-id")),
        redirect_uri=OAuthRedirectUri(value="http://localhost:8765/callback"),
        authorization_url="https://secure.soundcloud.com/authorize?state=session-state&code_challenge=challenge",
        code_verifier=OAuthCodeVerifier(value=SecretStr(RAW_CODE_VERIFIER)),
        state=OAuthState(value=SecretStr("session-state")),
        code_challenge=OAuthCodeChallenge(value="challenge"),
        created_at=created_at,
        expires_at=expires_at,
    )


def _request(
    session: OAuthAuthorizationSession,
    *,
    returned_state: OAuthState | None = None,
    client_secret: OAuthClientSecret | None = None,
) -> OAuthAuthorizationCodeExchangeWorkflowRequest:
    return OAuthAuthorizationCodeExchangeWorkflowRequest(
        session_id=session.session_id,
        returned_state=returned_state if returned_state is not None else session.state,
        authorization_code=_authorization_code(),
        client_secret=client_secret,
    )


def _authorization_code() -> OAuthAuthorizationCode:
    return OAuthAuthorizationCode(value=SecretStr(RAW_AUTHORIZATION_CODE))


def _token_response() -> OAuthTokenResponse:
    return OAuthTokenResponse(
        access_token=OAuthAccessToken(value=SecretStr(RAW_ACCESS_TOKEN)),
        refresh_token=OAuthRefreshToken(value=SecretStr(RAW_REFRESH_TOKEN)),
        expires_in=3600,
        scope="non-expiring",
    )
