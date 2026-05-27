import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import SecretStr

from soundcloud_downloader.application import OAuthPKCEService
from soundcloud_downloader.config import load_settings
from soundcloud_downloader.domain import OAuthClientId, OAuthRedirectUri


oauth_app = typer.Typer(help="OAuth helper commands.")


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
    selected_auth_base_url = auth_base_url if auth_base_url is not None else settings.soundcloud_auth_base_url

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
