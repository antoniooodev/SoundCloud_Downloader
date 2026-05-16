import json
from typing import Annotated

import typer

from soundcloud_downloader.application import (
    ReconstructionPlanRequest,
    ReconstructionPlanner,
    StreamAnalysisRequest,
)
from soundcloud_downloader.cli.options import parse_optional_bool
from soundcloud_downloader.domain import (
    AccessMode,
    DRMStatus,
    MediaCodec,
    MediaContainer,
    OutputProfile,
    SourceProtocol,
)

plan_app = typer.Typer(help="Build local reconstruction plans.")


@plan_app.command("evaluate")
def evaluate_plan(
    access_mode: Annotated[
        AccessMode,
        typer.Option("--access-mode", help="Access mode for the reconstruction plan."),
    ],
    source_protocol: Annotated[
        SourceProtocol,
        typer.Option("--source-protocol", help="Source protocol."),
    ],
    requested_profile: Annotated[
        OutputProfile | None,
        typer.Option("--requested-profile", help="Requested output profile."),
    ] = None,
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
    source_id: Annotated[
        str | None,
        typer.Option("--source-id", help="Optional source identifier."),
    ] = None,
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
        typer.Option("--source-drm-status", help="Declared source DRM status."),
    ] = DRMStatus.UNKNOWN,
    manifest_text: Annotated[
        str | None,
        typer.Option("--manifest-text", help="Inline HLS manifest text."),
    ] = None,
) -> None:
    stream = StreamAnalysisRequest(
        source_id=source_id,
        protocol=source_protocol,
        mime_type=source_mime_type,
        codec=source_codec,
        container=source_container,
        bitrate_kbps=source_bitrate_kbps,
        requires_auth=source_requires_auth,
        is_downloadable=source_downloadable,
        declared_drm_status=source_drm_status,
        manifest_text=manifest_text,
    )
    request = ReconstructionPlanRequest(
        access_mode=access_mode,
        requested_profile=requested_profile,
        is_authenticated=authenticated,
        has_go_plus=has_go_plus,
        is_public=track_public,
        is_go_plus_track=track_go_plus,
        is_preview_only=preview_only,
        is_downloadable=track_downloadable,
        is_own_track=own_track,
        offline_allowed=parse_optional_bool(offline_allowed),
        stream=stream,
    )
    plan = ReconstructionPlanner().plan(request)
    payload = plan.model_dump(mode="json")
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))
