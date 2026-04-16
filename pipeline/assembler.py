from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Mapping

from job_paths import (
    EDIT_MANIFEST_FILENAME,
    FINAL_VIDEO_FILENAME,
    FINAL_VIDEO_RENDERING_FILENAME,
    SCENE_CLIP_LIST_FILENAME,
    SCENE_CLIPS_DIRNAME,
    SCENES_CONCAT_FILENAME,
    build_scene_clip_filename,
    resolve_job_path,
)
from pipeline.edit_planner import EditManifest, SceneEditPlan, load_edit_manifest
from pipeline.scene_planner import SceneManifest, SceneSegment, load_scene_manifest, save_scene_manifest
from pipeline.visual_manifest import VisualManifest, load_visual_manifest
from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30
DEFAULT_STILL_DURATION_SECONDS = 15
TEMP_VIDEO_FILENAME = FINAL_VIDEO_RENDERING_FILENAME
TEMP_SCENE_VIDEO_FILENAME = SCENES_CONCAT_FILENAME
DEFAULT_VIDEO_PRESET = "slow"
DEFAULT_VIDEO_CRF = 20
DEFAULT_AUDIO_TARGET_LUFS = -15.0
DEFAULT_AUDIO_COMPRESSION_RATIO = 2.6
DEFAULT_AUDIO_HIGHPASS_HZ = 80
DEFAULT_AUDIO_LOWPASS_HZ = 9000
DEFAULT_SUBTITLE_FONT_NAME = "DejaVu Sans"
DEFAULT_SUBTITLE_FONT_SIZE = 22
DEFAULT_SUBTITLE_MARGIN_V = 120
DEFAULT_SUBTITLE_OUTLINE = 2
DEFAULT_SUBTITLE_SHADOW = 0
DEFAULT_SCENE_MOTION_SCALE = 1.14
DEFAULT_EDIT_BGM_VOLUME = 0.18
DEFAULT_EDIT_SFX_VOLUME = 0.9
DEFAULT_EDIT_SCENE_FADE_SECONDS = 0.18
DEFAULT_EDIT_FLASH_SECONDS = 0.08
DEFAULT_EDIT_BLUR_SIGMA = 18
DEFAULT_EDIT_CAPTION_WRAP = 20
EDIT_SCENE_CAPTIONS_DIRNAME = "scene_captions"
DEFAULT_EDIT_AUDIO_ROOT_CANDIDATES = (
    "assets/audio",
    "config/audio",
    "config/examples/audio",
)
EDIT_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".ppm", ".webp"}
SOURCE_MEDIA_PATH_KEYS = (
    "source_media",
    "source_media_path",
    "visual_source",
    "visual_source_path",
    "video_source",
    "video_source_path",
    "image_source",
    "image_source_path",
)
AUDIO_PATH_KEYS = (
    "voice_audio",
    "voice_audio_path",
    "voiceover_audio",
    "voiceover_audio_path",
)
SUBTITLE_PATH_KEYS = (
    "subtitles",
    "subtitles_path",
    "subtitle_path",
    "subtitle_file",
    "subtitle_file_path",
    "captions",
    "captions_path",
)
DURATION_METADATA_KEYS = (
    "assembler_duration_seconds",
    "target_duration_seconds",
    "duration_seconds",
    "estimated_duration_seconds",
)


class AssemblerError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class AssemblerInputs:
    source_media: Path | None
    voice_audio: Path | None
    subtitles: Path | None
    output_path: Path


@dataclass(slots=True, frozen=True)
class ProbeResult:
    has_video: bool
    has_audio: bool
    duration_seconds: float | None


@dataclass(slots=True, frozen=True)
class SceneRenderPlan:
    pace_role: str
    camera_pattern: str
    camera_intensity: str
    transition_in: str
    transition_out: str
    text_style: str
    overlay_style: str
    sfx_profile: str
    bgm_profile: str


@dataclass(slots=True, frozen=True)
class AudioLayerSpec:
    kind: str
    profile: str
    path: Path
    start_seconds: float
    duration_seconds: float
    volume: float
    loop: bool


async def assemble_video_step(job: VideoJob, job_dir: Path) -> None:
    _ensure_binary_available("ffmpeg")
    _ensure_binary_available("ffprobe")

    scene_manifest = load_scene_manifest(job)
    is_multi_scene = scene_manifest is not None and len(scene_manifest.scenes) > 1
    inputs = _resolve_inputs(job, job_dir, require_source_media=not is_multi_scene)
    if inputs.voice_audio is not None:
        audio_probe = await _probe_media(inputs.voice_audio)
        if not audio_probe.has_audio:
            raise AssemblerError(f"voice audio does not contain an audio stream: {inputs.voice_audio}")

    commands_run: list[list[str]] = []
    if is_multi_scene:
        visual_manifest = load_visual_manifest(job)
        edit_manifest = load_edit_manifest(job)
        LOGGER.info(
            "job=%s assembler started multi-scene scene_count=%s",
            job.job_id,
            len(scene_manifest.scenes),
        )
        commands_run = await _assemble_multi_scene_video(
            job=job,
            job_dir=job_dir,
            inputs=inputs,
            manifest=scene_manifest,
            visual_manifest=visual_manifest,
            edit_manifest=edit_manifest,
        )
    else:
        if inputs.source_media is None:
            raise AssemblerError("single-scene assembly requires a resolved source_media input")
        source_probe = await _probe_media(inputs.source_media)
        if not source_probe.has_video:
            raise AssemblerError(
                f"source media must contain a video stream or image: {inputs.source_media}"
            )

        command = _build_ffmpeg_command(job, inputs, source_probe)
        LOGGER.info("job=%s assembler started source=%s", job.job_id, inputs.source_media)
        LOGGER.debug("job=%s ffmpeg_command=%s", job.job_id, _format_command(command))

        if inputs.output_path.exists():
            inputs.output_path.unlink()

        temp_output_path = inputs.output_path.with_name(TEMP_VIDEO_FILENAME)
        if temp_output_path.exists():
            temp_output_path.unlink()

        try:
            await _run_command(command)
            temp_output_path.replace(inputs.output_path)
        except Exception:
            if temp_output_path.exists():
                temp_output_path.unlink()
            raise
        commands_run = [command]

    output_probe = await _probe_media(inputs.output_path)
    if not output_probe.has_video:
        raise AssemblerError(f"rendered output is missing a video stream: {inputs.output_path}")

    duration_seconds = _normalize_duration(output_probe.duration_seconds)
    if duration_seconds is not None:
        job.set_metadata("final_video_duration_seconds", duration_seconds)
    job.set_metadata("final_video_encoder_preset", _resolve_video_preset(job))
    job.set_metadata("final_video_crf", _resolve_video_crf(job))
    job.set_metadata("final_video_resolution", f"{TARGET_WIDTH}x{TARGET_HEIGHT}")
    job.set_metadata("final_video_format", "mp4")
    job.set_metadata("final_video_video_codec", "h264")
    job.set_metadata("final_video_audio_mode", "voiceover" if inputs.voice_audio else "silent")
    if commands_run:
        job.set_metadata("ffmpeg_command", _format_command(commands_run[-1]))
        job.set_metadata("ffmpeg_commands", [_format_command(command) for command in commands_run])
    job.set_metadata(
        "video_structure",
        scene_manifest.version if scene_manifest is not None and len(scene_manifest.scenes) > 1 else "single_scene",
    )
    job.set_final_video_path(str(inputs.output_path.resolve()))

    LOGGER.info("job=%s assembler finished output=%s", job.job_id, inputs.output_path)


def _ensure_binary_available(binary_name: str) -> None:
    if shutil.which(binary_name) is None:
        raise AssemblerError(f"required binary '{binary_name}' was not found in PATH")


def _resolve_inputs(
    job: VideoJob,
    job_dir: Path,
    *,
    require_source_media: bool,
) -> AssemblerInputs:
    source_media = (
        _resolve_required_path(job, SOURCE_MEDIA_PATH_KEYS)
        if require_source_media
        else _resolve_optional_path(job, SOURCE_MEDIA_PATH_KEYS)
    )
    voice_audio = _resolve_optional_path(job, AUDIO_PATH_KEYS)
    subtitles = _resolve_optional_path(job, SUBTITLE_PATH_KEYS)
    output_path = job_dir / FINAL_VIDEO_FILENAME
    return AssemblerInputs(
        source_media=source_media,
        voice_audio=voice_audio,
        subtitles=subtitles,
        output_path=output_path,
    )


def _resolve_required_path(job: VideoJob, candidate_keys: tuple[str, ...]) -> Path:
    path = _resolve_optional_path(job, candidate_keys)
    if path is not None:
        return path

    joined_keys = ", ".join(candidate_keys)
    raise AssemblerError(
        "missing source media for assembler; expected one of "
        f"job.file_paths/job.metadata keys: {joined_keys}"
    )


def _resolve_optional_path(job: VideoJob, candidate_keys: tuple[str, ...]) -> Path | None:
    for key in candidate_keys:
        raw_value = job.file_paths.get(key)
        if raw_value is None:
            raw_value = job.metadata.get(key)
        if not isinstance(raw_value, str):
            continue

        normalized_value = raw_value.strip()
        if not normalized_value:
            continue

        candidate_path = resolve_job_path(job, normalized_value)
        if not candidate_path.exists():
            raise AssemblerError(f"input path configured in '{key}' does not exist: {candidate_path}")
        return candidate_path

    return None


async def _probe_media(path: Path) -> ProbeResult:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return ProbeResult(has_video=True, has_audio=False, duration_seconds=None)

    payload = await _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(payload or "{}")
    streams = data.get("streams", [])
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    duration_value = data.get("format", {}).get("duration")
    duration_seconds = None
    if isinstance(duration_value, str) and duration_value.strip() and duration_value != "N/A":
        duration_seconds = float(duration_value)

    return ProbeResult(
        has_video=has_video,
        has_audio=has_audio,
        duration_seconds=duration_seconds,
    )


def _build_ffmpeg_command(
    job: VideoJob,
    inputs: AssemblerInputs,
    source_probe: ProbeResult,
) -> list[str]:
    temp_output_path = inputs.output_path.with_name(TEMP_VIDEO_FILENAME)
    source_is_image = inputs.source_media.suffix.lower() in IMAGE_SUFFIXES
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
    command: list[str] = ["ffmpeg", "-hide_banner", "-y"]

    if source_is_image:
        command.extend(["-loop", "1", "-framerate", str(TARGET_FPS), "-i", str(inputs.source_media)])
    else:
        if inputs.voice_audio is not None:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(inputs.source_media)])

    if inputs.voice_audio is not None:
        command.extend(["-i", str(inputs.voice_audio)])
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )

    filter_chain = [
        (
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,fps={TARGET_FPS}"
        )
    ]
    if inputs.subtitles is not None:
        filter_chain.append(_build_subtitle_filter(job, inputs.subtitles))

    command.extend(["-vf", ",".join(filter_chain)])
    command.extend(["-map", "0:v:0", "-map", "1:a:0"])

    audio_filter = _build_audio_filter(job, has_voice_audio=inputs.voice_audio is not None)
    if audio_filter is not None:
        command.extend(["-af", audio_filter])

    if source_is_image and inputs.voice_audio is None:
        command.extend(["-t", str(_estimate_output_duration_seconds(job, source_probe.duration_seconds))])

    command.extend(
        [
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            video_preset,
            "-crf",
            str(video_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(temp_output_path),
        ]
    )
    return command


async def _assemble_multi_scene_video(
    *,
    job: VideoJob,
    job_dir: Path,
    inputs: AssemblerInputs,
    manifest: SceneManifest,
    visual_manifest: VisualManifest | None,
    edit_manifest: EditManifest | None,
) -> list[list[str]]:
    if inputs.output_path.exists():
        inputs.output_path.unlink()

    temp_output_path = inputs.output_path.with_name(TEMP_VIDEO_FILENAME)
    if temp_output_path.exists():
        temp_output_path.unlink()

    clips_dir = (job_dir / SCENE_CLIPS_DIRNAME).resolve()
    clips_dir.mkdir(parents=True, exist_ok=True)
    concat_video_path = (job_dir / TEMP_SCENE_VIDEO_FILENAME).resolve()
    if concat_video_path.exists():
        concat_video_path.unlink()

    commands_run: list[list[str]] = []
    rendered_scenes: list[SceneSegment] = []
    captions_dir = (job_dir / EDIT_SCENE_CAPTIONS_DIRNAME).resolve()
    use_editing = edit_manifest is not None
    can_burn_per_scene_captions = use_editing and all(scene.cue_timeline for scene in manifest.scenes)
    if can_burn_per_scene_captions:
        captions_dir.mkdir(parents=True, exist_ok=True)

    for scene in manifest.scenes:
        asset_path = _resolve_scene_asset_path(job, scene, visual_manifest)
        output_clip_path = clips_dir / build_scene_clip_filename(scene.index)
        if output_clip_path.exists():
            output_clip_path.unlink()

        render_plan = _build_scene_render_plan(scene, edit_manifest)
        if render_plan is not None:
            caption_path = None
            if can_burn_per_scene_captions:
                caption_path = captions_dir / f"{scene.scene_id}.ass"
                caption_path.write_text(
                    _build_scene_caption_ass(scene, render_plan),
                    encoding="utf-8",
                )
            render_command = _build_edited_scene_clip_command(
                job=job,
                scene=scene,
                plan=render_plan,
                asset_path=asset_path,
                caption_path=caption_path,
                output_path=output_clip_path,
            )
        else:
            render_command = _build_scene_clip_command(
                job=job,
                scene=scene,
                asset_path=asset_path,
                output_path=output_clip_path,
            )
        LOGGER.debug(
            "job=%s scene=%s clip_command=%s",
            job.job_id,
            scene.scene_id,
            _format_command(render_command),
        )
        await _run_command(render_command)
        commands_run.append(render_command)
        rendered_scenes.append(
            SceneSegment(
                scene_id=scene.scene_id,
                index=scene.index,
                role=scene.role,
                scene_type=scene.scene_type,
                source_text=scene.source_text,
                title_text=scene.title_text,
                highlight_text=scene.highlight_text,
                keywords=list(scene.keywords),
                word_count=scene.word_count,
                proof_mode=scene.proof_mode,
                visual_asset_mode=scene.visual_asset_mode,
                motion_effect=scene.motion_effect,
                transition_effect=scene.transition_effect,
                transition_duration_seconds=scene.transition_duration_seconds,
                start_seconds=scene.start_seconds,
                end_seconds=scene.end_seconds,
                duration_seconds=scene.duration_seconds,
                alignment_source=scene.alignment_source,
                cue_timeline=list(scene.cue_timeline),
                asset_path=scene.asset_path,
                clip_path=str(output_clip_path.resolve()),
                visual_theme=scene.visual_theme,
                visual_layout_variant=scene.visual_layout_variant,
            )
        )

    updated_manifest = SceneManifest(
        version=manifest.version,
        strategy=manifest.strategy,
        total_duration_seconds=manifest.total_duration_seconds,
        primary_visual_asset_mode=manifest.primary_visual_asset_mode,
        scenes=rendered_scenes,
    )
    save_scene_manifest(job, job_dir, updated_manifest)

    concat_list_path = (job_dir / SCENE_CLIP_LIST_FILENAME).resolve()
    concat_list_path.write_text(
        "\n".join(
            _format_concat_file_entry(_resolve_scene_clip_path(scene))
            for scene in rendered_scenes
        )
        + "\n",
        encoding="utf-8",
    )

    concat_command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(concat_video_path),
    ]
    LOGGER.debug("job=%s scene_concat_command=%s", job.job_id, _format_command(concat_command))
    await _run_command(concat_command)
    commands_run.append(concat_command)

    audio_layers: list[AudioLayerSpec] = []
    missing_audio_profiles: list[str] = []
    if edit_manifest is not None:
        audio_layers, missing_audio_profiles = await _resolve_audio_layers(
            job=job,
            manifest=manifest,
            edit_manifest=edit_manifest,
        )

    mux_command = _build_mux_command(
        job=job,
        source_video_path=concat_video_path,
        voice_audio_path=inputs.voice_audio,
        subtitles_path=None if can_burn_per_scene_captions else inputs.subtitles,
        audio_layers=audio_layers,
        output_path=temp_output_path,
    )
    LOGGER.debug("job=%s scene_mux_command=%s", job.job_id, _format_command(mux_command))
    try:
        await _run_command(mux_command)
        temp_output_path.replace(inputs.output_path)
    except Exception:
        if temp_output_path.exists():
            temp_output_path.unlink()
        raise
    commands_run.append(mux_command)

    job.set_metadata("scene_clip_count", len(rendered_scenes))
    job.set_metadata("scene_concat_path", str(concat_video_path))
    job.set_metadata("scene_clip_list_path", str(concat_list_path))
    job.set_metadata("edit_manifest_used", use_editing)
    job.set_metadata("scene_caption_mode", "per_scene_ass" if can_burn_per_scene_captions else "global_srt")
    job.set_metadata("edit_audio_layer_count", len(audio_layers))
    job.set_metadata("edit_audio_missing_profiles", missing_audio_profiles)
    return commands_run


def _resolve_scene_asset_path(
    job: VideoJob,
    scene: SceneSegment,
    visual_manifest: VisualManifest | None,
) -> Path:
    raw_asset_path = scene.asset_path
    if raw_asset_path is None and visual_manifest is not None:
        scene_visual = visual_manifest.get_scene_asset(scene.scene_id)
        if scene_visual is not None:
            raw_asset_path = scene_visual.asset_path
    if raw_asset_path is None:
        raise AssemblerError(f"scene '{scene.scene_id}' is missing an asset_path")
    asset_path = resolve_job_path(job, raw_asset_path)
    if not asset_path.exists():
        raise AssemblerError(f"scene '{scene.scene_id}' asset does not exist: {asset_path}")
    if not asset_path.is_file():
        raise AssemblerError(f"scene '{scene.scene_id}' asset is not a file: {asset_path}")
    return asset_path


def _resolve_scene_clip_path(scene: SceneSegment) -> Path:
    if scene.clip_path is None:
        raise AssemblerError(f"scene '{scene.scene_id}' is missing a clip_path")
    return Path(scene.clip_path).expanduser().resolve()


def _format_concat_file_entry(path: Path) -> str:
    escaped_path = str(path).replace("'", r"'\''")
    return f"file '{escaped_path}'"


def _build_scene_render_plan(
    scene: SceneSegment,
    edit_manifest: EditManifest | None,
) -> SceneRenderPlan | None:
    if edit_manifest is None:
        return None
    decision = edit_manifest.get_scene_plan(scene.scene_id)
    if decision is None:
        return None
    return SceneRenderPlan(
        pace_role=decision.pace_role,
        camera_pattern=decision.camera_pattern,
        camera_intensity=decision.camera_intensity,
        transition_in=decision.transition_in,
        transition_out=decision.transition_out,
        text_style=decision.text_style,
        overlay_style=decision.overlay_style,
        sfx_profile=decision.sfx_profile,
        bgm_profile=decision.bgm_profile,
    )


def _build_edited_scene_clip_command(
    *,
    job: VideoJob,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    asset_path: Path,
    caption_path: Path | None,
    output_path: Path,
) -> list[str]:
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
    duration_seconds = max(0.3, float(scene.duration_seconds))
    source_is_image = asset_path.suffix.lower() in IMAGE_SUFFIXES

    command: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    if source_is_image:
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(TARGET_FPS),
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                str(asset_path),
            ]
        )
    else:
        command.extend(["-stream_loop", "-1", "-t", f"{duration_seconds:.3f}", "-i", str(asset_path)])

    command.extend(
        [
            "-filter_complex",
            _build_edited_scene_filter_graph(
                scene=scene,
                plan=plan,
                caption_path=caption_path,
                duration_seconds=duration_seconds,
            ),
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            video_preset,
            "-crf",
            str(video_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            str(output_path),
        ]
    )
    return command


def _build_edited_scene_filter_graph(
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    caption_path: Path | None,
    duration_seconds: float,
) -> str:
    parts: list[str] = []
    current_label = "motion"
    parts.append(
        f"[0:v]{_build_camera_pattern_filter(plan, duration_seconds=duration_seconds)}[{current_label}]"
    )

    overlay_filter = _build_overlay_filter(plan.overlay_style)
    if overlay_filter is not None:
        next_label = "overlay"
        parts.append(f"[{current_label}]{overlay_filter}[{next_label}]")
        current_label = next_label

    current_label = _append_transition_filter(
        parts,
        input_label=current_label,
        transition=plan.transition_in,
        mode="in",
        duration_seconds=duration_seconds,
    )
    current_label = _append_transition_filter(
        parts,
        input_label=current_label,
        transition=plan.transition_out,
        mode="out",
        duration_seconds=duration_seconds,
    )

    if caption_path is not None:
        subtitle_filter = f"subtitles='{_escape_filter_path(caption_path)}'"
        parts.append(f"[{current_label}]{subtitle_filter},format=yuv420p[vout]")
    else:
        parts.append(f"[{current_label}]format=yuv420p[vout]")

    return ";".join(parts)


def _build_camera_pattern_filter(plan: SceneRenderPlan, *, duration_seconds: float) -> str:
    intensity = plan.camera_intensity
    if plan.camera_pattern == "push_in_soft":
        end_zoom = {"low": 1.04, "medium": 1.08, "high": 1.12}[intensity]
        return _build_dynamic_zoom_filter(start_zoom=1.0, end_zoom=end_zoom, duration_seconds=duration_seconds)

    if plan.camera_pattern == "push_in_fast":
        end_zoom = {"low": 1.08, "medium": 1.13, "high": 1.18}[intensity]
        return _build_dynamic_zoom_filter(start_zoom=1.0, end_zoom=end_zoom, duration_seconds=duration_seconds)

    if plan.camera_pattern == "pull_out_soft":
        start_zoom = {"low": 1.07, "medium": 1.11, "high": 1.15}[intensity]
        return _build_dynamic_zoom_filter(start_zoom=start_zoom, end_zoom=1.0, duration_seconds=duration_seconds)

    if plan.camera_pattern == "pan_left":
        travel = {"low": 0.32, "medium": 0.50, "high": 0.68}[intensity]
        zoom = {"low": 1.06, "medium": 1.10, "high": 1.15}[intensity]
        return _build_pan_pattern_filter(direction="left", travel=travel, zoom=zoom, duration_seconds=duration_seconds)

    if plan.camera_pattern == "pan_right":
        travel = {"low": 0.32, "medium": 0.50, "high": 0.68}[intensity]
        zoom = {"low": 1.06, "medium": 1.10, "high": 1.15}[intensity]
        return _build_pan_pattern_filter(direction="right", travel=travel, zoom=zoom, duration_seconds=duration_seconds)

    if plan.camera_pattern == "micro_shake_light":
        amplitude = {"low": 4, "medium": 7, "high": 10}[intensity]
        zoom = {"low": 1.04, "medium": 1.06, "high": 1.08}[intensity]
        return _build_micro_shake_filter(amplitude=amplitude, zoom=zoom)

    if plan.camera_pattern == "impact_zoom":
        peak_zoom = {"low": 1.11, "medium": 1.15, "high": 1.20}[intensity]
        settle_zoom = {"low": 1.03, "medium": 1.05, "high": 1.08}[intensity]
        return _build_impact_zoom_filter(
            peak_zoom=peak_zoom,
            settle_zoom=settle_zoom,
            duration_seconds=duration_seconds,
        )

    return (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_dynamic_zoom_filter(*, start_zoom: float, end_zoom: float, duration_seconds: float) -> str:
    progress = _build_progress_expr(duration_seconds)
    zoom_expr = f"{start_zoom:.4f}+({end_zoom - start_zoom:.4f})*{progress}"
    return _build_scale_crop_filter(zoom_expr=zoom_expr)


def _build_pan_pattern_filter(
    *,
    direction: str,
    travel: float,
    zoom: float,
    duration_seconds: float,
) -> str:
    progress = _build_progress_expr(duration_seconds)
    x_expr = (
        f"((in_w-out_w)*(1-{travel:.4f})/2)+((in_w-out_w)*{travel:.4f}*(1-{progress}))"
        if direction == "left"
        else f"((in_w-out_w)*(1-{travel:.4f})/2)+((in_w-out_w)*{travel:.4f}*{progress})"
    )
    return (
        f"scale=w='trunc({TARGET_WIDTH}*{zoom:.4f})':"
        f"h='trunc({TARGET_HEIGHT}*{zoom:.4f})':"
        "force_original_aspect_ratio=increase:eval=frame,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='{x_expr}':y='(in_h-out_h)/2',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_micro_shake_filter(*, amplitude: int, zoom: float) -> str:
    x_expr = f"(in_w-out_w)/2+sin(t*9.5)*{amplitude}"
    y_expr = f"(in_h-out_h)/2+cos(t*13.0)*{max(2, amplitude - 2)}"
    return (
        f"scale=w='trunc({TARGET_WIDTH}*{zoom:.4f})':"
        f"h='trunc({TARGET_HEIGHT}*{zoom:.4f})':"
        "force_original_aspect_ratio=increase:eval=frame,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='{x_expr}':y='{y_expr}',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_impact_zoom_filter(*, peak_zoom: float, settle_zoom: float, duration_seconds: float) -> str:
    impact_window = min(0.18, max(0.08, duration_seconds / 4.0))
    settle_duration = max(0.05, duration_seconds - impact_window)
    zoom_expr = (
        f"if(lt(t,{impact_window:.3f}),"
        f"1+({peak_zoom - 1.0:.4f})*(t/{impact_window:.3f}),"
        f"{peak_zoom:.4f}-({peak_zoom - settle_zoom:.4f})*"
        f"min(1,max(0,(t-{impact_window:.3f})/{settle_duration:.3f})))"
    )
    return _build_scale_crop_filter(zoom_expr=zoom_expr)


def _build_scale_crop_filter(*, zoom_expr: str) -> str:
    return (
        f"scale=w='trunc({TARGET_WIDTH}*({zoom_expr}))':"
        f"h='trunc({TARGET_HEIGHT}*({zoom_expr}))':"
        "force_original_aspect_ratio=increase:eval=frame,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_overlay_filter(overlay_style: str) -> str | None:
    if overlay_style == "subtle_vignette":
        return "vignette=PI/10"
    if overlay_style == "light_glow":
        return "eq=brightness=0.025:saturation=1.08:contrast=1.04"
    if overlay_style == "proof_frame":
        return "drawbox=x=36:y=36:w=iw-72:h=ih-72:color=white@0.22:t=4"
    if overlay_style == "cta_band":
        return "drawbox=x=0:y=1480:w=iw:h=300:color=black@0.38:t=fill"
    return None


def _append_transition_filter(
    parts: list[str],
    *,
    input_label: str,
    transition: str,
    mode: str,
    duration_seconds: float,
) -> str:
    if transition == "cut":
        return input_label

    if transition in {"flash_cut", "dip_to_black_soft"}:
        fade_duration = _resolve_transition_window(
            duration_seconds,
            maximum=DEFAULT_EDIT_SCENE_FADE_SECONDS if transition == "dip_to_black_soft" else DEFAULT_EDIT_FLASH_SECONDS,
        )
        if mode == "in":
            filter_text = f"fade=t=in:st=0:d={fade_duration:.3f}"
        else:
            fade_start = max(0.0, duration_seconds - fade_duration)
            filter_text = f"fade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}"
        if transition == "flash_cut":
            filter_text += ":color=white"
        output_label = f"{mode}_{transition}_{len(parts)}"
        parts.append(f"[{input_label}]{filter_text}[{output_label}]")
        return output_label

    if transition in {"blur_in", "blur_out"}:
        fade_duration = _resolve_transition_window(duration_seconds, maximum=DEFAULT_EDIT_SCENE_FADE_SECONDS)
        base_label = f"{mode}_blur_base_{len(parts)}"
        blur_source_label = f"{mode}_blur_src_{len(parts)}"
        blur_overlay_label = f"{mode}_blur_overlay_{len(parts)}"
        output_label = f"{mode}_blur_out_{len(parts)}"
        parts.append(f"[{input_label}]split[{base_label}][{blur_source_label}]")
        fade_type = "out" if mode == "in" else "in"
        fade_start = 0.0 if mode == "in" else max(0.0, duration_seconds - fade_duration)
        parts.append(
            f"[{blur_source_label}]gblur=sigma={DEFAULT_EDIT_BLUR_SIGMA},format=rgba,"
            f"fade=t={fade_type}:st={fade_start:.3f}:d={fade_duration:.3f}:alpha=1"
            f"[{blur_overlay_label}]"
        )
        parts.append(f"[{base_label}][{blur_overlay_label}]overlay=shortest=1[{output_label}]")
        return output_label

    return input_label


def _build_progress_expr(duration_seconds: float) -> str:
    safe_duration = max(0.05, duration_seconds)
    return f"min(1,max(0,t/{safe_duration:.3f}))"


def _resolve_transition_window(duration_seconds: float, *, maximum: float) -> float:
    return _clamp(maximum, 0.05, min(maximum, duration_seconds / 3.0))


def _build_scene_caption_ass(scene: SceneSegment, plan: SceneRenderPlan) -> str:
    style = _build_caption_style(scene, plan)
    event_lines: list[str] = []
    for cue in scene.cue_timeline:
        start_seconds = max(0.0, cue.start_seconds - scene.start_seconds)
        end_seconds = max(start_seconds + 0.05, cue.end_seconds - scene.start_seconds)
        text = _format_caption_text(cue.text, scene=scene, plan=plan)
        event_lines.append(
            "Dialogue: 0,"
            f"{_format_ass_timestamp(start_seconds)},{_format_ass_timestamp(end_seconds)},"
            f"Default,,0,0,0,,{text}"
        )

    if not event_lines:
        fallback_end = min(max(0.6, scene.duration_seconds), 2.4)
        event_lines.append(
            "Dialogue: 0,"
            f"{_format_ass_timestamp(0.0)},{_format_ass_timestamp(fallback_end)},"
            f"Default,,0,0,0,,{_format_caption_text(scene.highlight_text, scene=scene, plan=plan)}"
        )

    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {TARGET_WIDTH}",
            f"PlayResY: {TARGET_HEIGHT}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{style}",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *event_lines,
            "",
        ]
    )
    return header


def _build_caption_style(scene: SceneSegment, plan: SceneRenderPlan) -> str:
    font_name = DEFAULT_SUBTITLE_FONT_NAME
    if plan.text_style == "hook_focus":
        values = {
            "font_size": 30,
            "primary": "&H00FFFFFF",
            "outline": 3,
            "shadow": 0,
            "bold": 1,
            "margin_v": 180,
            "back_color": "&H54000000",
            "spacing": 0.5,
        }
    elif plan.text_style == "proof_clean":
        values = {
            "font_size": 20,
            "primary": "&H00F5F5F5",
            "outline": 1,
            "shadow": 0,
            "bold": 0,
            "margin_v": 130,
            "back_color": "&H1C000000",
            "spacing": 0.1,
        }
    elif plan.text_style == "keyword_emphasis":
        values = {
            "font_size": 24,
            "primary": "&H00FFFFFF",
            "outline": 2,
            "shadow": 0,
            "bold": 0,
            "margin_v": 120,
            "back_color": "&H42000000",
            "spacing": 0.2,
        }
    else:
        values = {
            "font_size": 23,
            "primary": "&H00FFFFFF",
            "outline": 2,
            "shadow": 0,
            "bold": 1,
            "margin_v": 170 if plan.overlay_style == "cta_band" or scene.role == "cta" else 120,
            "back_color": "&H42000000",
            "spacing": 0.2,
        }

    return ",".join(
        [
            font_name,
            str(values["font_size"]),
            values["primary"],
            "&H000000FF",
            "&H00000000",
            values["back_color"],
            str(values["bold"]),
            "0",
            "0",
            "0",
            "100",
            "100",
            str(values["spacing"]),
            "0",
            "1",
            str(values["outline"]),
            str(values["shadow"]),
            "2",
            "80",
            "80",
            str(values["margin_v"]),
            "1",
        ]
    )


def _format_caption_text(text: str, *, scene: SceneSegment, plan: SceneRenderPlan) -> str:
    cleaned_text = _sanitize_ass_text(text)
    wrapped_text = _wrap_caption_text(cleaned_text, width=_resolve_caption_wrap(plan.text_style))
    if plan.text_style == "keyword_emphasis":
        wrapped_text = _emphasize_caption_keywords(wrapped_text, scene.keywords)
    return wrapped_text.replace("\n", r"\N")


def _resolve_caption_wrap(text_style: str) -> int:
    if text_style == "hook_focus":
        return 16
    if text_style == "proof_clean":
        return 22
    return DEFAULT_EDIT_CAPTION_WRAP


def _sanitize_ass_text(value: str) -> str:
    return " ".join(value.replace("\\", "/").replace("{", "(").replace("}", ")").split()).strip()


def _wrap_caption_text(text: str, *, width: int) -> str:
    words = [word for word in text.split() if word]
    if not words:
        return ""

    lines: list[str] = []
    current_line: list[str] = []
    current_length = 0
    for word in words:
        projected = current_length + len(word) + (1 if current_line else 0)
        if current_line and projected > width:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_length = len(word)
        else:
            current_line.append(word)
            current_length = projected

    if current_line:
        lines.append(" ".join(current_line))
    return "\n".join(lines[:2])


def _emphasize_caption_keywords(text: str, keywords: list[str]) -> str:
    emphasized = text
    highlight_count = 0
    for keyword in sorted(keywords, key=len, reverse=True):
        normalized = keyword.strip()
        if len(normalized) < 4:
            continue
        pattern = re.compile(rf"\b({re.escape(normalized)})\b", flags=re.IGNORECASE)

        def _replace(match: re.Match[str]) -> str:
            return r"{\b1\1c&H0038C8FF&}" + match.group(1) + r"{\r}"

        emphasized, replaced = pattern.subn(_replace, emphasized, count=1)
        highlight_count += replaced
        if highlight_count >= 2:
            break
    return emphasized


def _format_ass_timestamp(value: float) -> str:
    total_centiseconds = max(0, int(round(value * 100)))
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _build_scene_clip_command(
    *,
    job: VideoJob,
    scene: SceneSegment,
    asset_path: Path,
    output_path: Path,
) -> list[str]:
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
    duration_seconds = max(0.3, float(scene.duration_seconds))
    source_is_image = asset_path.suffix.lower() in IMAGE_SUFFIXES

    command: list[str] = ["ffmpeg", "-hide_banner", "-y"]
    if source_is_image:
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(TARGET_FPS),
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                str(asset_path),
            ]
        )
    else:
        command.extend(["-stream_loop", "-1", "-t", f"{duration_seconds:.3f}", "-i", str(asset_path)])

    command.extend(
        [
            "-vf",
            _build_scene_video_filter(scene, duration_seconds=duration_seconds, source_is_image=source_is_image),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            video_preset,
            "-crf",
            str(video_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            str(output_path),
        ]
    )
    return command


def _build_scene_video_filter(
    scene: SceneSegment,
    *,
    duration_seconds: float,
    source_is_image: bool,
) -> str:
    if scene.motion_effect == "zoom_in" and source_is_image:
        base_filter = _build_zoom_filter(duration_seconds)
    elif scene.motion_effect == "pan_left":
        base_filter = _build_pan_filter(duration_seconds, axis="x", direction="reverse")
    elif scene.motion_effect == "pan_up":
        base_filter = _build_pan_filter(duration_seconds, axis="y", direction="reverse")
    elif scene.motion_effect == "pan_right":
        base_filter = _build_pan_filter(duration_seconds, axis="x", direction="forward")
    else:
        base_filter = (
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,fps={TARGET_FPS}"
        )

    if scene.transition_effect != "fade" or scene.transition_duration_seconds <= 0:
        return base_filter

    fade_duration = _clamp(scene.transition_duration_seconds, 0.08, min(0.35, duration_seconds / 3.0))
    fade_out_start = max(0.0, duration_seconds - fade_duration)
    return (
        f"{base_filter},"
        f"fade=t=in:st=0:d={fade_duration:.3f},"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}"
    )


def _build_zoom_filter(duration_seconds: float) -> str:
    frame_count = max(2, int(round(duration_seconds * TARGET_FPS)))
    return (
        f"scale={int(TARGET_WIDTH * DEFAULT_SCENE_MOTION_SCALE)}:"
        f"{int(TARGET_HEIGHT * DEFAULT_SCENE_MOTION_SCALE)}:force_original_aspect_ratio=increase,"
        f"zoompan="
        f"z='min(max(zoom,pzoom)+0.0015,1.12)':"
        f"x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':"
        f"d=1:s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps={TARGET_FPS},"
        f"trim=duration={duration_seconds:.3f},"
        f"setpts=PTS-STARTPTS,"
        f"fps={TARGET_FPS},"
        "setsar=1"
    )


def _build_pan_filter(duration_seconds: float, *, axis: str, direction: str) -> str:
    progress = f"min(1,max(0,t/{duration_seconds:.3f}))"
    travel = progress if direction == "forward" else f"(1-{progress})"
    scaled_width = int(TARGET_WIDTH * DEFAULT_SCENE_MOTION_SCALE)
    scaled_height = int(TARGET_HEIGHT * DEFAULT_SCENE_MOTION_SCALE)
    x_expr = f"(in_w-out_w)*{travel}" if axis == "x" else "(in_w-out_w)/2"
    y_expr = f"(in_h-out_h)*{travel}" if axis == "y" else "(in_h-out_h)/2"
    return (
        f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='{x_expr}':y='{y_expr}',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_mux_command(
    *,
    job: VideoJob,
    source_video_path: Path,
    voice_audio_path: Path | None,
    subtitles_path: Path | None,
    audio_layers: list[AudioLayerSpec] | None,
    output_path: Path,
) -> list[str]:
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
    layers = list(audio_layers or [])
    command: list[str] = ["ffmpeg", "-hide_banner", "-y", "-i", str(source_video_path)]

    if voice_audio_path is not None:
        command.extend(["-i", str(voice_audio_path)])
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )

    if layers:
        for layer in layers:
            if layer.loop:
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(layer.path)])

        command.extend(
            [
                "-filter_complex",
                _build_mux_filter_complex(
                    job=job,
                    subtitles_path=subtitles_path,
                    has_voice_audio=voice_audio_path is not None,
                    audio_layers=layers,
                ),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
            ]
        )
    else:
        filter_chain = [f"setsar=1,fps={TARGET_FPS}"]
        if subtitles_path is not None:
            filter_chain.append(_build_subtitle_filter(job, subtitles_path))

        command.extend(["-vf", ",".join(filter_chain)])
        command.extend(["-map", "0:v:0", "-map", "1:a:0"])

        audio_filter = _build_audio_filter(job, has_voice_audio=voice_audio_path is not None)
        if audio_filter is not None:
            command.extend(["-af", audio_filter])

    command.extend(
        [
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            video_preset,
            "-crf",
            str(video_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    )
    return command


async def _resolve_audio_layers(
    *,
    job: VideoJob,
    manifest: SceneManifest,
    edit_manifest: EditManifest,
) -> tuple[list[AudioLayerSpec], list[str]]:
    roots = _resolve_edit_audio_roots(job)
    cache: dict[tuple[str, str], Path | None] = {}
    missing_profiles: list[str] = []
    missing_seen: set[str] = set()
    layers: list[AudioLayerSpec] = []
    bgm_volume = _read_float_metadata(job, "edit_bgm_volume", DEFAULT_EDIT_BGM_VOLUME, minimum=0.0, maximum=0.6)
    sfx_volume = _read_float_metadata(job, "edit_sfx_volume", DEFAULT_EDIT_SFX_VOLUME, minimum=0.0, maximum=1.4)

    for scene in manifest.scenes:
        plan = edit_manifest.get_scene_plan(scene.scene_id)
        if plan is None:
            continue

        bgm_path = await _resolve_audio_profile_path(
            job=job,
            kind="bgm",
            profile=plan.bgm_profile,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
        )
        if bgm_path is not None:
            layers.append(
                AudioLayerSpec(
                    kind="bgm",
                    profile=plan.bgm_profile,
                    path=bgm_path,
                    start_seconds=max(0.0, float(scene.start_seconds)),
                    duration_seconds=max(0.3, float(scene.duration_seconds)),
                    volume=bgm_volume,
                    loop=True,
                )
            )

        sfx_path = await _resolve_audio_profile_path(
            job=job,
            kind="sfx",
            profile=plan.sfx_profile,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
        )
        if sfx_path is not None:
            sfx_start = max(0.0, float(scene.start_seconds) + _resolve_sfx_offset_seconds(plan))
            sfx_duration = min(max(0.18, float(scene.duration_seconds) * 0.45), 0.9)
            layers.append(
                AudioLayerSpec(
                    kind="sfx",
                    profile=plan.sfx_profile,
                    path=sfx_path,
                    start_seconds=sfx_start,
                    duration_seconds=sfx_duration,
                    volume=sfx_volume,
                    loop=False,
                )
            )

    return _merge_bgm_layers(layers), missing_profiles


async def _resolve_audio_profile_path(
    *,
    job: VideoJob,
    kind: str,
    profile: str,
    roots: list[Path],
    cache: dict[tuple[str, str], Path | None],
    missing_profiles: list[str],
    missing_seen: set[str],
) -> Path | None:
    normalized_profile = profile.strip().lower()
    if normalized_profile == "none":
        return None

    cache_key = (kind, normalized_profile)
    if cache_key in cache:
        return cache[cache_key]

    resolved_path: Path | None = None
    for candidate in _resolve_audio_profile_candidates(
        job=job,
        kind=kind,
        profile=normalized_profile,
        roots=roots,
    ):
        if not candidate.exists() or not candidate.is_file():
            continue
        probe = await _probe_media(candidate)
        if probe.has_audio:
            resolved_path = candidate
            break

    if resolved_path is None:
        marker = f"{kind}:{normalized_profile}"
        if marker not in missing_seen:
            missing_seen.add(marker)
            missing_profiles.append(marker)

    cache[cache_key] = resolved_path
    return resolved_path


def _resolve_audio_profile_candidates(
    *,
    job: VideoJob,
    kind: str,
    profile: str,
    roots: list[Path],
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _register(path: Path) -> None:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    for raw_path in _read_audio_profile_mapping(job, kind=kind, profile=profile):
        _register(resolve_job_path(job, raw_path))

    for root in roots:
        for extension in EDIT_AUDIO_EXTENSIONS:
            _register((root / kind / f"{profile}{extension}").resolve())
            _register((root / f"{profile}{extension}").resolve())

    return candidates


def _read_audio_profile_mapping(job: VideoJob, *, kind: str, profile: str) -> list[str]:
    values: list[str] = []
    specific_mapping = job.metadata.get(f"edit_{kind}_profiles")
    if isinstance(specific_mapping, Mapping):
        values.extend(_read_audio_profile_values(specific_mapping.get(profile)))

    generic_mapping = job.metadata.get("edit_audio_profiles")
    if isinstance(generic_mapping, Mapping):
        nested_mapping = generic_mapping.get(kind)
        if isinstance(nested_mapping, Mapping):
            values.extend(_read_audio_profile_values(nested_mapping.get(profile)))

    return values


def _read_audio_profile_values(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if isinstance(item, str) and str(item).strip()
        ]
    return []


def _resolve_edit_audio_roots(job: VideoJob) -> list[Path]:
    raw_value = job.metadata.get("edit_audio_roots")
    configured_roots: list[str] = []
    if isinstance(raw_value, str) and raw_value.strip():
        configured_roots.extend(
            part.strip()
            for part in re.split(r"[,\n;]+", raw_value)
            if part.strip()
        )
    elif isinstance(raw_value, list):
        configured_roots.extend(
            str(item).strip()
            for item in raw_value
            if isinstance(item, str) and str(item).strip()
        )

    roots: list[Path] = []
    seen: set[str] = set()
    for raw_root in [*configured_roots, *DEFAULT_EDIT_AUDIO_ROOT_CANDIDATES]:
        candidate = resolve_job_path(job, raw_root)
        if candidate.exists() and candidate.is_dir():
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                roots.append(candidate)
    return roots


def _merge_bgm_layers(layers: list[AudioLayerSpec]) -> list[AudioLayerSpec]:
    merged: list[AudioLayerSpec] = []
    for layer in layers:
        if (
            layer.kind == "bgm"
            and merged
            and merged[-1].kind == "bgm"
            and merged[-1].path == layer.path
            and abs((merged[-1].start_seconds + merged[-1].duration_seconds) - layer.start_seconds) <= 0.08
        ):
            previous = merged.pop()
            merged.append(
                AudioLayerSpec(
                    kind=previous.kind,
                    profile=previous.profile,
                    path=previous.path,
                    start_seconds=previous.start_seconds,
                    duration_seconds=round(
                        max(0.3, layer.start_seconds + layer.duration_seconds - previous.start_seconds),
                        3,
                    ),
                    volume=previous.volume,
                    loop=previous.loop,
                )
            )
            continue
        merged.append(layer)
    return merged


def _resolve_sfx_offset_seconds(plan: SceneEditPlan) -> float:
    if plan.transition_in == "flash_cut":
        return 0.02
    if plan.pace_role == "hook":
        return 0.04
    if plan.pace_role == "proof":
        return 0.06
    return 0.08


def _build_mux_filter_complex(
    *,
    job: VideoJob,
    subtitles_path: Path | None,
    has_voice_audio: bool,
    audio_layers: list[AudioLayerSpec],
) -> str:
    parts: list[str] = []
    video_filters = [f"setsar=1", f"fps={TARGET_FPS}"]
    if subtitles_path is not None:
        video_filters.append(_build_subtitle_filter(job, subtitles_path))
    parts.append(f"[0:v]{','.join(video_filters)},format=yuv420p[vout]")

    voice_label = "voice_mix"
    if has_voice_audio:
        audio_filter = _build_audio_filter(job, has_voice_audio=True)
        if audio_filter is None:
            parts.append(f"[1:a]anull[{voice_label}]")
        else:
            parts.append(f"[1:a]{audio_filter}[{voice_label}]")
    else:
        parts.append(f"[1:a]anull[{voice_label}]")

    mix_labels = [f"[{voice_label}]"]
    for input_index, layer in enumerate(audio_layers, start=2):
        layer_label = f"layer_{input_index}"
        parts.append(f"[{input_index}:a]{_build_audio_layer_filter(layer)}[{layer_label}]")
        mix_labels.append(f"[{layer_label}]")

    parts.append(
        "".join(mix_labels)
        + f"amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0,"
        "alimiter=limit=0.92[aout]"
    )
    return ";".join(parts)


def _build_audio_layer_filter(layer: AudioLayerSpec) -> str:
    duration_seconds = max(0.08, layer.duration_seconds)
    fade_seconds = min(0.22, max(0.05, duration_seconds / 4.0))
    filters = [
        f"atrim=0:{duration_seconds:.3f}",
        "asetpts=PTS-STARTPTS",
        f"volume={layer.volume:.3f}",
    ]
    if layer.kind == "bgm":
        filters.append(f"afade=t=in:st=0:d={fade_seconds:.3f}")
        filters.append(
            f"afade=t=out:st={max(0.0, duration_seconds - fade_seconds):.3f}:d={fade_seconds:.3f}"
        )
    else:
        filters.append(
            f"afade=t=out:st={max(0.0, duration_seconds - fade_seconds):.3f}:d={fade_seconds:.3f}"
        )
    delay_ms = max(0, int(round(layer.start_seconds * 1000)))
    filters.append(f"adelay={delay_ms}|{delay_ms}")
    return ",".join(filters)


def _resolve_video_preset(job: VideoJob) -> str:
    preset_value = job.metadata.get("video_preset", DEFAULT_VIDEO_PRESET)
    if isinstance(preset_value, str) and preset_value.strip():
        normalized_preset = preset_value.strip().lower()
        if normalized_preset in {
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        }:
            return normalized_preset
    return DEFAULT_VIDEO_PRESET


def _resolve_video_crf(job: VideoJob) -> int:
    return _read_int_metadata(job, "video_crf", DEFAULT_VIDEO_CRF, minimum=16, maximum=28)


def _build_audio_filter(job: VideoJob, *, has_voice_audio: bool) -> str | None:
    if not has_voice_audio:
        return None

    target_lufs = _read_float_metadata(
        job,
        "audio_target_lufs",
        DEFAULT_AUDIO_TARGET_LUFS,
        minimum=-24.0,
        maximum=-10.0,
    )
    compression_ratio = _read_float_metadata(
        job,
        "audio_compression_ratio",
        DEFAULT_AUDIO_COMPRESSION_RATIO,
        minimum=1.2,
        maximum=6.0,
    )
    highpass_hz = _read_int_metadata(
        job,
        "audio_highpass_hz",
        DEFAULT_AUDIO_HIGHPASS_HZ,
        minimum=20,
        maximum=200,
    )
    lowpass_hz = _read_int_metadata(
        job,
        "audio_lowpass_hz",
        DEFAULT_AUDIO_LOWPASS_HZ,
        minimum=4000,
        maximum=16000,
    )
    return (
        f"highpass=f={highpass_hz},"
        f"lowpass=f={lowpass_hz},"
        f"acompressor=threshold=-18dB:ratio={compression_ratio:.2f}:attack=20:release=140,"
        f"loudnorm=I={target_lufs:.1f}:LRA=7:TP=-1.5"
    )


def _build_subtitle_filter(job: VideoJob, subtitles_path: Path) -> str:
    font_name = _read_str_metadata(job, "subtitle_font_name", DEFAULT_SUBTITLE_FONT_NAME)
    font_size = _read_int_metadata(
        job,
        "subtitle_font_size",
        DEFAULT_SUBTITLE_FONT_SIZE,
        minimum=16,
        maximum=40,
    )
    margin_v = _read_int_metadata(
        job,
        "subtitle_margin_v",
        DEFAULT_SUBTITLE_MARGIN_V,
        minimum=60,
        maximum=260,
    )
    outline = _read_int_metadata(
        job,
        "subtitle_outline",
        DEFAULT_SUBTITLE_OUTLINE,
        minimum=0,
        maximum=6,
    )
    shadow = _read_int_metadata(
        job,
        "subtitle_shadow",
        DEFAULT_SUBTITLE_SHADOW,
        minimum=0,
        maximum=4,
    )
    bold_value = "1" if _read_bool_metadata(job, "subtitle_bold", default=True) else "0"
    style = ",".join(
        [
            f"FontName={font_name}",
            f"FontSize={font_size}",
            "PrimaryColour=&H00FFFFFF",
            "OutlineColour=&H00000000",
            "BackColour=&H64000000",
            "BorderStyle=1",
            f"Outline={outline}",
            f"Shadow={shadow}",
            f"Bold={bold_value}",
            "Alignment=2",
            f"MarginV={margin_v}",
            "Spacing=0.2",
        ]
    )
    return (
        f"subtitles='{_escape_filter_path(subtitles_path)}':"
        f"force_style='{_escape_ass_style(style)}'"
    )


def _estimate_output_duration_seconds(job: VideoJob, source_duration_seconds: float | None) -> int:
    if source_duration_seconds is not None and source_duration_seconds > 0:
        return max(1, int(round(source_duration_seconds)))

    for key in DURATION_METADATA_KEYS:
        value = job.metadata.get(key)
        if isinstance(value, int | float) and value > 0:
            return max(1, int(round(float(value))))

    script_word_count = job.metadata.get("script_word_count")
    if isinstance(script_word_count, int) and script_word_count > 0:
        return max(DEFAULT_STILL_DURATION_SECONDS, min(60, script_word_count // 2))

    return DEFAULT_STILL_DURATION_SECONDS


def _escape_filter_path(path: Path) -> str:
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def _escape_ass_style(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _read_int_metadata(
    job: VideoJob,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = job.metadata.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(minimum, min(maximum, value))
    if isinstance(value, float):
        return max(minimum, min(maximum, int(round(value))))
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError:
            return default
        return max(minimum, min(maximum, parsed))
    return default


def _read_float_metadata(
    job: VideoJob,
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = job.metadata.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return max(minimum, min(maximum, float(value)))
    if isinstance(value, str) and value.strip():
        try:
            parsed = float(value.strip())
        except ValueError:
            return default
        return max(minimum, min(maximum, parsed))
    return default


def _read_str_metadata(job: VideoJob, key: str, default: str) -> str:
    value = job.metadata.get(key, default)
    if isinstance(value, str) and value.strip():
        return " ".join(value.strip().split())
    return default


def _read_bool_metadata(job: VideoJob, key: str, *, default: bool) -> bool:
    value = job.metadata.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        normalized_value = value.strip().lower()
        if normalized_value in {"1", "true", "yes", "on"}:
            return True
        if normalized_value in {"0", "false", "no", "off"}:
            return False
    return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _format_command(command: list[str]) -> str:
    return " ".join(_shell_quote(part) for part in command)


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe_characters = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/:=+")
    if all(character in safe_characters for character in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _normalize_duration(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 3)


async def _run_command(command: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        error_output = stderr_text or stdout_text or "<no output>"
        raise AssemblerError(
            f"command failed with exit code {process.returncode}: "
            f"{_format_command(command)}\n{error_output}"
        )

    return stdout_text


__all__ = ["AssemblerError", "assemble_video_step"]
