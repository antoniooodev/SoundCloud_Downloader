import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, SecretStr

from soundcloud_downloader.application import (
    CreateOAuthAuthorizationSessionRequest,
    InMemoryOAuthAuthorizationSessionStore,
    OAuthAuthorizationCodeExchangeWorkflow,
    OAuthAuthorizationCodeExchangeWorkflowRequest,
    OAuthAuthorizationCodeExchangeWorkflowResult,
    OAuthAuthorizationSessionStore,
    OAuthAuthorizationSessionService,
    OAuthPKCEService,
)
from soundcloud_downloader.config import AppSettings, load_settings
from soundcloud_downloader.domain import (
    OAuthAuthorizationCode,
    OAuthClientId,
    OAuthClientSecret,
    OAuthRedirectUri,
    OAuthSessionId,
    OAuthState,
    OAuthTokenProfileId,
    SoundcloudDownloaderError,
    StoredOAuthTokenSet,
)
from soundcloud_downloader.infrastructure import (
    EncryptedOAuthAuthorizationSessionStore,
    EncryptedOAuthTokenStore,
    SafeAsyncHttpClient,
)
from soundcloud_downloader.infrastructure.soundcloud import OAuthTokenExchangeService


oauth_app = typer.Typer(help="OAuth helper commands.")


def _build_auth_base_url(settings: AppSettings, auth_base_url: str | None) -> str:
    return auth_base_url if auth_base_url is not None else settings.soundcloud_auth_base_url


class OAuthExchangeCodeCliResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    session_consumed: bool
    access_token_received: bool
    refresh_token_received: bool
    token_persisted: bool
    profile_id: str
    expires_in: int | None = None
    scope: str | None = None


@oauth_app.command("authorize-url")
def authorize_url(
    client_id: Annotated[
        str,
        typer.Option("--client-id", help="SoundCloud app client ID."),
    ],
    redirect_uri: Annotated[
        str,
        typer.Option("--redirect-uri", help="OAuth redirect URI."),
    ],
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    auth_base_url: Annotated[
        str | None,
        typer.Option("--auth-base-url", help="Override OAuth authorization base URL."),
    ] = None,
    verifier_length: Annotated[
        int,
        typer.Option("--verifier-length", help="PKCE code verifier length."),
    ] = 64,
    state_length: Annotated[
        int,
        typer.Option("--state-length", help="OAuth state length."),
    ] = 32,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or only the URL."),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    selected_auth_base_url = _build_auth_base_url(settings, auth_base_url)

    service = OAuthPKCEService()
    oauth_client_id = OAuthClientId(value=SecretStr(client_id))
    oauth_redirect_uri = OAuthRedirectUri(value=redirect_uri)
    code_verifier = service.generate_code_verifier(verifier_length)
    code_challenge = service.derive_s256_challenge(code_verifier)
    state = service.generate_state(state_length)
    request = service.build_authorization_request(
        auth_base_url=selected_auth_base_url,
        client_id=oauth_client_id,
        redirect_uri=oauth_redirect_uri,
        code_challenge=code_challenge,
        state=state,
    )

    if not json_output:
        typer.echo(request.authorization_url)
        return

    payload = {
        "authorization_url": request.authorization_url,
        "code_challenge_method": request.code_challenge.method.value,
        "code_verifier_required_for_token_exchange": True,
        "response_type": request.response_type.value,
        "state_present": True,
        "warning": (
            "The PKCE code verifier is not persisted by this command. "
            "Token exchange will be implemented in a later task."
        ),
    }
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@oauth_app.command("create-session")
def create_session(
    client_id: Annotated[
        str,
        typer.Option("--client-id", help="SoundCloud app client ID."),
    ],
    redirect_uri: Annotated[
        str,
        typer.Option("--redirect-uri", help="OAuth redirect URI."),
    ],
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    auth_base_url: Annotated[
        str | None,
        typer.Option("--auth-base-url", help="Override OAuth authorization base URL."),
    ] = None,
    verifier_length: Annotated[
        int,
        typer.Option("--verifier-length", help="PKCE code verifier length."),
    ] = 64,
    state_length: Annotated[
        int,
        typer.Option("--state-length", help="OAuth state length."),
    ] = 32,
    ttl_seconds: Annotated[
        int,
        typer.Option("--ttl-seconds", help="OAuth authorization session TTL in seconds."),
    ] = 600,
    persist: Annotated[
        bool,
        typer.Option("--persist/--memory", help="Store the OAuth session persistently or in memory."),
    ] = False,
    store_path: Annotated[
        Path | None,
        typer.Option("--store-path", help="Override OAuth session store path."),
    ] = None,
    allow_filesystem_writes: Annotated[
        bool | None,
        typer.Option(
            "--allow-filesystem-writes/--no-allow-filesystem-writes",
            help="Override whether persistent OAuth session storage may write files.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or session ID and URL."),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    settings = _apply_create_session_settings_overrides(
        settings,
        store_path=store_path,
        allow_filesystem_writes=allow_filesystem_writes,
    )
    selected_auth_base_url = _build_auth_base_url(settings, auth_base_url)
    request = CreateOAuthAuthorizationSessionRequest(
        client_id=OAuthClientId(value=SecretStr(client_id)),
        redirect_uri=OAuthRedirectUri(value=redirect_uri),
        auth_base_url=selected_auth_base_url,
        verifier_length=verifier_length,
        state_length=state_length,
        ttl_seconds=ttl_seconds,
    )
    store = _build_create_session_store(settings, persist=persist)
    public_session = OAuthAuthorizationSessionService(store=store).create_session(request)

    if not json_output:
        typer.echo(public_session.session_id)
        typer.echo(public_session.authorization_url)
        return

    payload = public_session.model_dump(mode="json")
    payload.pop("code_verifier_required_for_token_exchange", None)
    payload["warning"] = _build_create_session_warning(persist=persist)
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@oauth_app.command("exchange-code")
def exchange_code(
    session_id: Annotated[
        str,
        typer.Option("--session-id", help="OAuth authorization session ID."),
    ],
    code: Annotated[
        str,
        typer.Option("--code", help="Returned OAuth authorization code."),
    ],
    state: Annotated[
        str,
        typer.Option("--state", help="Returned OAuth state."),
    ],
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    session_store_path: Annotated[
        Path | None,
        typer.Option(
            "--session-store-path",
            "--store-path",
            help="Override OAuth authorization session store path.",
        ),
    ] = None,
    token_store_path: Annotated[
        Path | None,
        typer.Option("--token-store-path", help="Override OAuth token store path."),
    ] = None,
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="OAuth token profile ID."),
    ] = "default",
    persist_token: Annotated[
        bool,
        typer.Option("--persist-token/--no-persist-token", help="Persist received OAuth tokens."),
    ] = True,
    allow_network: Annotated[
        bool | None,
        typer.Option(
            "--allow-network/--no-allow-network",
            help="Override whether token exchange may use network access.",
        ),
    ] = None,
    allow_filesystem_writes: Annotated[
        bool | None,
        typer.Option(
            "--allow-filesystem-writes/--no-allow-filesystem-writes",
            help="Override whether OAuth session consumption may write files.",
        ),
    ] = None,
    auth_base_url: Annotated[
        str | None,
        typer.Option("--auth-base-url", help="Override OAuth authorization base URL."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or safe key/value lines."),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    settings = _apply_exchange_code_settings_overrides(
        settings,
        session_store_path=session_store_path,
        token_store_path=token_store_path,
        allow_network=allow_network,
        allow_filesystem_writes=allow_filesystem_writes,
        auth_base_url=auth_base_url,
    )
    _validate_exchange_code_settings(settings)
    token_profile_id = OAuthTokenProfileId(value=profile_id)
    if persist_token:
        _validate_token_persistence_settings(settings)

    try:
        result = asyncio.run(
            _exchange_code_async(
                settings=settings,
                session_id=session_id,
                code=code,
                state=state,
                profile_id=token_profile_id,
                persist_token=persist_token,
            )
        )
    except (SoundcloudDownloaderError, ValueError):
        typer.echo("OAuth session validation failed.", err=True)
        raise typer.Exit(1) from None

    if json_output:
        typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return

    typer.echo(f"session_id={result.session_id}")
    typer.echo(f"session_consumed={str(result.session_consumed).lower()}")
    typer.echo(f"access_token_received={str(result.access_token_received).lower()}")
    typer.echo(f"refresh_token_received={str(result.refresh_token_received).lower()}")
    typer.echo(f"token_persisted={str(result.token_persisted).lower()}")
    typer.echo(f"profile_id={result.profile_id}")
    typer.echo(f"expires_in={result.expires_in or ''}")
    typer.echo(f"scope={result.scope or ''}")


@oauth_app.command("token-status")
def token_status(
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="OAuth token profile ID."),
    ] = "default",
    token_store_path: Annotated[
        Path | None,
        typer.Option("--token-store-path", help="Override OAuth token store path."),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or safe key/value lines."),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    settings = _apply_token_store_overrides(
        settings,
        token_store_path=token_store_path,
        allow_filesystem_writes=None,
    )
    _validate_token_store_configured(settings)
    token_profile_id = OAuthTokenProfileId(value=profile_id)
    try:
        token_set = _build_encrypted_token_store(settings).get(token_profile_id)
    except SoundcloudDownloaderError:
        typer.echo("OAuth token status could not be read safely.", err=True)
        raise typer.Exit(1) from None

    payload = _token_status_payload(token_profile_id, token_set)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"profile_id={payload['profile_id']}")
    typer.echo(f"token_present={str(payload['token_present']).lower()}")
    access_token_expired = payload["access_token_expired"]
    typer.echo(
        "access_token_expired="
        f"{'' if access_token_expired is None else str(access_token_expired).lower()}"
    )
    typer.echo(f"refresh_token_present={str(payload['refresh_token_present']).lower()}")
    typer.echo(f"expires_at={payload['expires_at'] or ''}")
    typer.echo(f"scope={payload['scope'] or ''}")


@oauth_app.command("logout")
def logout(
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="OAuth token profile ID."),
    ] = "default",
    token_store_path: Annotated[
        Path | None,
        typer.Option("--token-store-path", help="Override OAuth token store path."),
    ] = None,
    allow_filesystem_writes: Annotated[
        bool | None,
        typer.Option(
            "--allow-filesystem-writes/--no-allow-filesystem-writes",
            help="Override whether OAuth token deletion may write files.",
        ),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Explicit settings env file."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json/--plain", help="Print structured JSON or safe key/value lines."),
    ] = True,
) -> None:
    settings = load_settings(env_file=env_file)
    settings = _apply_token_store_overrides(
        settings,
        token_store_path=token_store_path,
        allow_filesystem_writes=allow_filesystem_writes,
    )
    _validate_token_persistence_settings(settings)
    token_profile_id = OAuthTokenProfileId(value=profile_id)
    try:
        _build_encrypted_token_store(settings).delete(token_profile_id)
    except SoundcloudDownloaderError:
        typer.echo("OAuth logout could not be completed safely.", err=True)
        raise typer.Exit(1) from None

    payload = {"profile_id": token_profile_id.value, "logged_out": True}
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"profile_id={token_profile_id.value}")
    typer.echo("logged_out=true")


async def _exchange_code_async(
    *,
    settings: AppSettings,
    session_id: str,
    code: str,
    state: str,
    profile_id: OAuthTokenProfileId,
    persist_token: bool,
) -> OAuthExchangeCodeCliResult:
    token_store = _build_encrypted_token_store(settings) if persist_token else None
    session_store = EncryptedOAuthAuthorizationSessionStore(settings)
    async with SafeAsyncHttpClient(settings) as http_client:
        workflow = build_oauth_token_exchange_workflow(
            settings=settings,
            session_store=session_store,
            http_client=http_client,
        )
        result = await workflow.exchange(
            OAuthAuthorizationCodeExchangeWorkflowRequest(
                session_id=OAuthSessionId(value=session_id),
                returned_state=OAuthState(value=SecretStr(state)),
                authorization_code=OAuthAuthorizationCode(value=SecretStr(code)),
                client_secret=(
                    OAuthClientSecret(value=settings.soundcloud_client_secret)
                    if settings.soundcloud_client_secret is not None
                    else None
                ),
            )
        )
    if token_store is not None:
        token_store.save(
            StoredOAuthTokenSet.from_token_response(
                profile_id=profile_id,
                token_response=result.token_response,
            )
        )
    return _build_exchange_code_cli_result(
        result,
        profile_id=profile_id,
        token_persisted=token_store is not None,
    )


def build_oauth_token_exchange_workflow(
    *,
    settings: AppSettings,
    session_store: OAuthAuthorizationSessionStore,
    http_client: SafeAsyncHttpClient,
) -> OAuthAuthorizationCodeExchangeWorkflow:
    session_service = OAuthAuthorizationSessionService(store=session_store)
    token_exchange = OAuthTokenExchangeService(settings=settings, http_client=http_client)
    return OAuthAuthorizationCodeExchangeWorkflow(
        session_service=session_service,
        token_exchange=token_exchange,
    )


def _build_exchange_code_cli_result(
    result: OAuthAuthorizationCodeExchangeWorkflowResult,
    *,
    profile_id: OAuthTokenProfileId,
    token_persisted: bool,
) -> OAuthExchangeCodeCliResult:
    return OAuthExchangeCodeCliResult(
        session_id=result.session_id,
        session_consumed=result.consumed,
        access_token_received=True,
        refresh_token_received=result.token_response.refresh_token is not None,
        token_persisted=token_persisted,
        profile_id=profile_id.value,
        expires_in=result.token_response.expires_in,
        scope=result.token_response.scope,
    )


def _apply_exchange_code_settings_overrides(
    settings: AppSettings,
    *,
    session_store_path: Path | None,
    token_store_path: Path | None,
    allow_network: bool | None,
    allow_filesystem_writes: bool | None,
    auth_base_url: str | None,
) -> AppSettings:
    updates: dict[str, object] = {}
    if session_store_path is not None:
        updates["oauth_session_store_path"] = session_store_path
    if token_store_path is not None:
        updates["oauth_token_store_path"] = token_store_path
    if allow_network is not None:
        updates["allow_network"] = allow_network
    if allow_filesystem_writes is not None:
        updates["allow_filesystem_writes"] = allow_filesystem_writes
    if auth_base_url is not None:
        updates["soundcloud_auth_base_url"] = auth_base_url
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _validate_exchange_code_settings(settings: AppSettings) -> None:
    if settings.oauth_session_encryption_key is None:
        typer.echo("Persistent OAuth session storage is not configured.", err=True)
        raise typer.Exit(1)
    if not settings.allow_filesystem_writes:
        typer.echo("Filesystem writes must be enabled to consume an OAuth session.", err=True)
        raise typer.Exit(1)
    if not settings.allow_network:
        typer.echo("Network access must be enabled for token exchange.", err=True)
        raise typer.Exit(1)


def _validate_token_persistence_settings(settings: AppSettings) -> None:
    _validate_token_store_configured(settings)
    if not settings.allow_filesystem_writes:
        typer.echo("Filesystem writes must be enabled to persist OAuth tokens.", err=True)
        raise typer.Exit(1)


def _validate_token_store_configured(settings: AppSettings) -> None:
    if settings.oauth_token_encryption_key is None:
        typer.echo("Persistent OAuth token storage is not configured.", err=True)
        raise typer.Exit(1)


def _build_encrypted_token_store(settings: AppSettings) -> EncryptedOAuthTokenStore:
    return EncryptedOAuthTokenStore(settings)


def _apply_token_store_overrides(
    settings: AppSettings,
    *,
    token_store_path: Path | None,
    allow_filesystem_writes: bool | None,
) -> AppSettings:
    updates: dict[str, object] = {}
    if token_store_path is not None:
        updates["oauth_token_store_path"] = token_store_path
    if allow_filesystem_writes is not None:
        updates["allow_filesystem_writes"] = allow_filesystem_writes
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _token_status_payload(
    profile_id: OAuthTokenProfileId,
    token_set: StoredOAuthTokenSet | None,
) -> dict[str, object]:
    if token_set is None:
        return {
            "profile_id": profile_id.value,
            "token_present": False,
            "access_token_expired": None,
            "refresh_token_present": False,
            "expires_at": None,
            "scope": None,
        }
    return {
        "profile_id": profile_id.value,
        "token_present": True,
        "access_token_expired": token_set.is_expired(),
        "refresh_token_present": token_set.refresh_token is not None,
        "expires_at": token_set.expires_at.isoformat() if token_set.expires_at is not None else None,
        "scope": token_set.scope,
    }


def _apply_create_session_settings_overrides(
    settings: AppSettings,
    *,
    store_path: Path | None,
    allow_filesystem_writes: bool | None,
) -> AppSettings:
    updates: dict[str, object] = {}
    if store_path is not None:
        updates["oauth_session_store_path"] = store_path
    if allow_filesystem_writes is not None:
        updates["allow_filesystem_writes"] = allow_filesystem_writes
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def _build_create_session_store(
    settings: AppSettings,
    *,
    persist: bool,
) -> OAuthAuthorizationSessionStore:
    if not persist:
        return InMemoryOAuthAuthorizationSessionStore()
    if settings.oauth_session_encryption_key is None:
        typer.echo(
            "Persistent OAuth session storage requires a configured encryption key.",
            err=True,
        )
        raise typer.Exit(1)
    if not settings.allow_filesystem_writes:
        typer.echo(
            "Persistent OAuth session storage requires filesystem writes to be enabled.",
            err=True,
        )
        raise typer.Exit(1)
    return EncryptedOAuthAuthorizationSessionStore(settings)


def _build_create_session_warning(*, persist: bool) -> str:
    if persist:
        return (
            "This session was stored in the encrypted OAuth session store. "
            "Keep the encryption key available for token exchange."
        )
    return (
        "This session is stored in memory only and will not survive process exit. "
        "Persistent secure storage will be implemented in a later task."
    )
