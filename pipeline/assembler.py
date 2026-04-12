from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil

from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30
DEFAULT_STILL_DURATION_SECONDS = 15
FINAL_VIDEO_FILENAME = "final.mp4"
TEMP_VIDEO_FILENAME = "final.rendering.mp4"
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
    source_probe = await _probe_media(inputs.source_media)
    if not source_probe.has_video:
        raise AssemblerError(
            f"source media must contain a video stream or image: {inputs.source_media}"
        )

    if inputs.voice_audio is not None:
        audio_probe = await _probe_media(inputs.voice_audio)
        if not audio_probe.has_audio:
            raise AssemblerError(f"voice audio does not contain an audio stream: {inputs.voice_audio}")

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
    job.set_metadata("ffmpeg_command", _format_command(command))
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
