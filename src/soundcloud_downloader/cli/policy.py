import json
from typing import Annotated

import typer

from soundcloud_downloader.application import PolicyEvaluationRequest, PolicyEvaluationService
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    MediaCodec,
    MediaContainer,
    MediaSource,
    OutputProfile,
    SourceProtocol,
)

policy_app = typer.Typer(help="Evaluate reconstruction policy decisions.")


def _parse_optional_bool(value: str) -> bool | None:
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized == "unknown":
        return None
    raise typer.BadParameter("Expected one of: true, false, unknown.")


@policy_app.command("evaluate")
def evaluate_policy(
    access_mode: Annotated[
        AccessMode,
        typer.Option("--access-mode", help="Access mode for the policy evaluation."),
    ],
    requested_profile: Annotated[
        OutputProfile | None,
        typer.Option("--requested-profile", help="Requested output profile."),
    ] = None,
    source_present: Annotated[
        bool,
        typer.Option(
            "--source-present/--no-source-present",
            help="Whether source metadata is present.",
        ),
    ] = False,
    source_id: Annotated[
        str | None,
        typer.Option("--source-id", help="Optional source identifier."),
    ] = None,
    source_protocol: Annotated[
        SourceProtocol,
        typer.Option("--source-protocol", help="Source protocol."),
    ] = SourceProtocol.UNKNOWN,
    source_mime_type: Annotated[
        str | None,
        typer.Option("--source-mime-type", help="Optional source MIME type."),
    ] = None,
    source_codec: Annotated[
        MediaCodec,
        typer.Option("--source-codec", help="Source media codec."),
    ] = MediaCodec.UNKNOWN,
    source_container: Annotated[
        MediaContainer,
        typer.Option("--source-container", help="Source media container."),
    ] = MediaContainer.UNKNOWN,
    source_bitrate_kbps: Annotated[
        int | None,
        typer.Option("--source-bitrate-kbps", min=1, help="Optional source bitrate in kbps."),
    ] = None,
    source_requires_auth: Annotated[
        bool,
        typer.Option(
            "--source-requires-auth/--no-source-requires-auth",
            help="Whether the source requires authentication.",
        ),
    ] = False,
    source_downloadable: Annotated[
        bool,
        typer.Option(
            "--source-downloadable/--no-source-downloadable",
            help="Whether the source is officially downloadable.",
        ),
    ] = False,
    source_drm_status: Annotated[
        DRMStatus,
        typer.Option("--source-drm-status", help="Source DRM status."),
    ] = DRMStatus.UNKNOWN,
    authenticated: Annotated[
        bool,
        typer.Option(
            "--authenticated/--no-authenticated",
            help="Whether the request is authenticated.",
        ),
    ] = False,
    has_go_plus: Annotated[
        bool,
        typer.Option(
            "--has-go-plus/--no-has-go-plus",
            help="Whether the account has Go+ entitlement.",
        ),
    ] = False,
    track_public: Annotated[
        bool,
        typer.Option("--track-public/--no-track-public", help="Whether the track is public."),
    ] = False,
    track_go_plus: Annotated[
        bool,
        typer.Option("--track-go-plus/--no-track-go-plus", help="Whether the track is Go+ gated."),
    ] = False,
    preview_only: Annotated[
        bool,
        typer.Option("--preview-only/--no-preview-only", help="Whether the track is preview-only."),
    ] = False,
    track_downloadable: Annotated[
        bool,
        typer.Option(
            "--track-downloadable/--no-track-downloadable",
            help="Whether the track is officially downloadable.",
        ),
    ] = False,
    own_track: Annotated[
        bool,
        typer.Option("--own-track/--no-own-track", help="Whether the track belongs to the account."),
    ] = False,
    offline_allowed: Annotated[
        str,
        typer.Option("--offline-allowed", help="Offline rights state: true, false, or unknown."),
    ] = "unknown",
) -> None:
    source = None
    if source_present:
        source = MediaSource(
            source_id=source_id,
            protocol=source_protocol,
            mime_type=source_mime_type,
            codec=source_codec,
            container=source_container,
            bitrate_kbps=source_bitrate_kbps,
            requires_auth=source_requires_auth,
            is_downloadable=source_downloadable,
            drm_status=source_drm_status,
        )

    request = PolicyEvaluationRequest(
        access_mode=access_mode,
        requested_profile=requested_profile,
        is_authenticated=authenticated,
        has_go_plus=has_go_plus,
        is_public=track_public,
        is_go_plus_track=track_go_plus,
        is_preview_only=preview_only,
        is_downloadable=track_downloadable,
        is_own_track=own_track,
        offline_allowed=_parse_optional_bool(offline_allowed),
        source=source,
    )
    response = PolicyEvaluationService().evaluate(request)
    payload = response.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
