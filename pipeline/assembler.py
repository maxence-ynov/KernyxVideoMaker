from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil

from pipeline.scene_planner import SceneManifest, SceneSegment, load_scene_manifest, save_scene_manifest
from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30
DEFAULT_STILL_DURATION_SECONDS = 15
FINAL_VIDEO_FILENAME = "final.mp4"
TEMP_VIDEO_FILENAME = "final.rendering.mp4"
TEMP_SCENE_VIDEO_FILENAME = "scenes.concat.mp4"
SCENE_CLIPS_DIRNAME = "scene_clips"
SCENE_CLIP_LIST_FILENAME = "scene_clips.txt"
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
    source_media: Path
    voice_audio: Path | None
    subtitles: Path | None
    output_path: Path


@dataclass(slots=True, frozen=True)
class ProbeResult:
    has_video: bool
    has_audio: bool
    duration_seconds: float | None


async def assemble_video_step(job: VideoJob, job_dir: Path) -> None:
    _ensure_binary_available("ffmpeg")
    _ensure_binary_available("ffprobe")

    inputs = _resolve_inputs(job, job_dir)
    if inputs.voice_audio is not None:
        audio_probe = await _probe_media(inputs.voice_audio)
        if not audio_probe.has_audio:
            raise AssemblerError(f"voice audio does not contain an audio stream: {inputs.voice_audio}")

    commands_run: list[list[str]] = []
    scene_manifest = load_scene_manifest(job)
    if scene_manifest is not None and len(scene_manifest.scenes) > 1:
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
        )
    else:
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


def _resolve_inputs(job: VideoJob, job_dir: Path) -> AssemblerInputs:
    source_media = _resolve_required_path(job, SOURCE_MEDIA_PATH_KEYS)
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

        candidate_path = _resolve_job_path(job, normalized_value)
        if not candidate_path.exists():
            raise AssemblerError(f"input path configured in '{key}' does not exist: {candidate_path}")
        return candidate_path

    return None


def _resolve_job_path(job: VideoJob, raw_value: str) -> Path:
    candidate_path = Path(raw_value).expanduser()
    if candidate_path.is_absolute():
        return candidate_path

    resolved_from_cwd = candidate_path.resolve()
    if resolved_from_cwd.exists():
        return resolved_from_cwd

    config_path = job.metadata.get("config_path")
    if isinstance(config_path, str) and config_path.strip():
        return (Path(config_path).expanduser().resolve().parent / candidate_path).resolve()

    return resolved_from_cwd


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

    for scene in manifest.scenes:
        asset_path = _resolve_scene_asset_path(job, scene)
        output_clip_path = clips_dir / f"{scene.scene_id}.mp4"
        if output_clip_path.exists():
            output_clip_path.unlink()

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

    mux_command = _build_mux_command(
        job=job,
        source_video_path=concat_video_path,
        voice_audio_path=inputs.voice_audio,
        subtitles_path=inputs.subtitles,
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
    return commands_run


def _resolve_scene_asset_path(job: VideoJob, scene: SceneSegment) -> Path:
    if scene.asset_path is None:
        raise AssemblerError(f"scene '{scene.scene_id}' is missing an asset_path")
    asset_path = _resolve_job_path(job, scene.asset_path)
    if not asset_path.exists():
        raise AssemblerError(f"scene '{scene.scene_id}' asset does not exist: {asset_path}")
    return asset_path


def _resolve_scene_clip_path(scene: SceneSegment) -> Path:
    if scene.clip_path is None:
        raise AssemblerError(f"scene '{scene.scene_id}' is missing a clip_path")
    return Path(scene.clip_path).expanduser().resolve()


def _format_concat_file_entry(path: Path) -> str:
    escaped_path = str(path).replace("'", r"'\''")
    return f"file '{escaped_path}'"


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
    output_path: Path,
) -> list[str]:
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
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
