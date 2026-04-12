from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
import wave

from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

VOICE_AUDIO_FILENAME = "voice.wav"
VOICE_AUDIO_TEMP_FILENAME = "voice.rendering.wav"
SUBTITLES_FILENAME = "subtitles.srt"
VOICE_INPUT_FILENAME = "voice_input.txt"
DEFAULT_VOICE_PROFILE = "fr"
DEFAULT_VOICE_DELIVERY_STYLE = "tiktok_clear"
DEFAULT_SPEECH_RATE = 150
DEFAULT_PITCH = 46
DEFAULT_AMPLITUDE = 115
DEFAULT_SUBTITLE_MAX_WORDS = 6
DEFAULT_SUBTITLE_MAX_CHARS = 34
DEFAULT_SUBTITLE_MAX_LINE_CHARS = 18
DEFAULT_SUBTITLE_MAX_LINES = 2
DEFAULT_SUBTITLE_MIN_DURATION_SECONDS = 0.55
DEFAULT_SUBTITLE_MAX_DURATION_SECONDS = 2.2
DEFAULT_SUBTITLE_GAP_SECONDS = 0.03
SCRIPT_METADATA_KEYS = ("script_text", "script_content")
SCRIPT_PATH_KEYS = ("script", "script_path", "script_file", "script_file_path")
VOICE_PROFILE_ALIASES = {
    "fr-default": "fr",
    "fr-female": "fr+f3",
    "fr-male": "fr+m3",
    "en-default": "en",
    "en-female": "en+f3",
    "en-male": "en+m3",
}
VOICE_DELIVERY_PRESETS = {
    "tiktok_clear": {"speed": 150, "pitch": 46, "amplitude": 115},
    "tiktok_punchy": {"speed": 158, "pitch": 50, "amplitude": 120},
    "tiktok_relaxed": {"speed": 142, "pitch": 44, "amplitude": 112},
}
WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+|\n+", flags=re.UNICODE)
CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+|(?<=,)\s+|\n+", flags=re.UNICODE)
FR_NUMBER_WORDS = {
    0: "zero",
    1: "un",
    2: "deux",
    3: "trois",
    4: "quatre",
    5: "cinq",
    6: "six",
    7: "sept",
    8: "huit",
    9: "neuf",
    10: "dix",
    11: "onze",
    12: "douze",
}
TTS_SUBSTITUTIONS = (
    (re.compile(r"\bIA\b", flags=re.IGNORECASE), "intelligence artificielle"),
    (re.compile(r"\bAI\b", flags=re.IGNORECASE), "A I"),
    (re.compile(r"\bAPI\b", flags=re.IGNORECASE), "A P I"),
    (re.compile(r"\bCTA\b", flags=re.IGNORECASE), "appel a l action"),
    (re.compile(r"\bTikTok\b", flags=re.IGNORECASE), "Tik Tok"),
    (re.compile(r"&"), "et"),
    (re.compile(r"/"), " ou "),
)


class VoiceGenerationError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class VoiceDelivery:
    style: str
    speech_rate: int
    pitch: int
    amplitude: int


@dataclass(slots=True, frozen=True)
class SubtitleSettings:
    max_words: int
    max_chars: int
    max_line_chars: int
    max_lines: int
    min_duration_seconds: float
    max_duration_seconds: float
    gap_seconds: float


@dataclass(slots=True, frozen=True)
class ScriptInput:
    text: str
    spoken_text: str
    source_label: str
    temp_path: Path


async def generate_voice_step(job: VideoJob, job_dir: Path) -> None:
    engine_binary = _resolve_tts_binary()
    delivery = _resolve_voice_delivery(job)
    subtitle_settings = _resolve_subtitle_settings(job)
    script_input = _resolve_script_input(job, job_dir)
    audio_output_path = job_dir / VOICE_AUDIO_FILENAME
    temp_audio_output_path = job_dir / VOICE_AUDIO_TEMP_FILENAME
    subtitles_output_path = job_dir / SUBTITLES_FILENAME
    voice_name = _resolve_voice_name(job)

    if audio_output_path.exists():
        audio_output_path.unlink()
    if temp_audio_output_path.exists():
        temp_audio_output_path.unlink()
    if subtitles_output_path.exists():
        subtitles_output_path.unlink()

    command = _build_tts_command(
        engine_binary=engine_binary,
        script_path=script_input.temp_path,
        voice_name=voice_name,
        speech_rate=delivery.speech_rate,
        pitch=delivery.pitch,
        amplitude=delivery.amplitude,
        output_path=temp_audio_output_path,
    )
    LOGGER.info("job=%s voice_gen started engine=%s voice=%s", job.job_id, engine_binary, voice_name)
    LOGGER.debug("job=%s tts_command=%s", job.job_id, _format_command(command))

    try:
        try:
            await _run_command(command)
            if not temp_audio_output_path.exists() or temp_audio_output_path.stat().st_size == 0:
                raise VoiceGenerationError(
                    f"voice generator produced no audio output: {temp_audio_output_path}"
                )
            temp_audio_output_path.replace(audio_output_path)
        except Exception:
            if temp_audio_output_path.exists():
                temp_audio_output_path.unlink()
            raise

        duration_seconds = _read_wav_duration_seconds(audio_output_path)
        subtitle_cue_count = 0
        subtitles_generated = False
        try:
            subtitle_content = _build_subtitle_content(
                script_input.text,
                duration_seconds,
                settings=subtitle_settings,
            )
            subtitles_output_path.write_text(subtitle_content, encoding="utf-8")
            subtitle_cue_count = subtitle_content.count("\n\n") + 1
            subtitles_generated = True
            job.set_subtitles_path(str(subtitles_output_path.resolve()))
        except Exception as exc:
            LOGGER.warning("job=%s subtitle generation skipped: %s", job.job_id, exc)
            if subtitles_output_path.exists():
                subtitles_output_path.unlink()

        job.set_voice_audio_path(str(audio_output_path.resolve()))
        job.set_metadata("voice_engine", engine_binary)
        job.set_metadata("voice_engine_voice", voice_name)
        job.set_metadata("voice_delivery_style", delivery.style)
        job.set_metadata("voice_duration_seconds", round(duration_seconds, 3))
        job.set_metadata("voice_audio_format", "wav")
        job.set_metadata("voice_script_source", script_input.source_label)
        job.set_metadata("voice_tts_text", script_input.spoken_text)
        job.set_metadata("subtitles_generated", subtitles_generated)
        job.set_metadata("subtitle_cue_count", subtitle_cue_count)
        job.set_metadata(
            "subtitle_layout",
            {
                "max_words": subtitle_settings.max_words,
                "max_chars": subtitle_settings.max_chars,
                "max_line_chars": subtitle_settings.max_line_chars,
                "max_lines": subtitle_settings.max_lines,
            },
        )
        job.set_metadata("tts_command", _format_command(command))

        LOGGER.info(
            "job=%s voice_gen finished output=%s subtitles=%s duration=%.3fs",
            job.job_id,
            audio_output_path,
            subtitles_generated,
            duration_seconds,
        )
    finally:
        if script_input.temp_path.exists():
            script_input.temp_path.unlink()


def _resolve_tts_binary() -> str:
    for candidate in ("espeak-ng", "espeak"):
        if shutil.which(candidate):
            return candidate
    raise VoiceGenerationError(
        "no supported TTS engine found in PATH; install 'espeak-ng' or 'espeak'"
    )


def _resolve_script_input(job: VideoJob, job_dir: Path) -> ScriptInput:
    temp_path = job_dir / VOICE_INPUT_FILENAME
    script_text = _read_script_text_from_metadata(job)
    if script_text is None:
        script_path = _resolve_script_path(job)
        script_text = script_path.read_text(encoding="utf-8").strip()
        source_label = str(script_path)
    else:
        source_label = "metadata"

    normalized_text = _normalize_script_text(script_text)
    if not normalized_text:
        raise VoiceGenerationError("script text is empty after normalization")

    spoken_text = _build_spoken_text(job, normalized_text)
    temp_path.write_text(spoken_text + "\n", encoding="utf-8")
    return ScriptInput(
        text=normalized_text,
        spoken_text=spoken_text,
        source_label=source_label,
        temp_path=temp_path,
    )


def _read_script_text_from_metadata(job: VideoJob) -> str | None:
    for key in SCRIPT_METADATA_KEYS:
        value = job.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _resolve_script_path(job: VideoJob) -> Path:
    for key in SCRIPT_PATH_KEYS:
        value = job.file_paths.get(key)
        if value is None:
            value = job.metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            continue

        candidate_path = _resolve_job_path(job, value.strip())
        if not candidate_path.exists():
            raise VoiceGenerationError(f"script path configured in '{key}' does not exist: {candidate_path}")
        return candidate_path

    joined_keys = ", ".join(SCRIPT_PATH_KEYS)
    raise VoiceGenerationError(
        "missing script input for voice generation; expected script text in metadata or "
        f"a script path in one of: {joined_keys}"
    )


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


def _normalize_script_text(value: str) -> str:
    normalized_lines = [" ".join(line.split()) for line in value.splitlines()]
    normalized_text = "\n".join(line for line in normalized_lines if line)
    return normalized_text.strip()


def _build_spoken_text(job: VideoJob, script_text: str) -> str:
    language = _resolve_language(job)
    normalized_sections = [line.strip() for line in script_text.splitlines() if line.strip()]
    spoken_sections: list[str] = []
    for index, line in enumerate(normalized_sections):
        normalized_line = line
        for pattern, replacement in TTS_SUBSTITUTIONS:
            normalized_line = pattern.sub(replacement, normalized_line)
        normalized_line = re.sub(r"\b(\d+)(e|eme)\b", lambda match: _to_ordinal(match.group(1), language), normalized_line)
        normalized_line = re.sub(r"\b\d+\b", lambda match: _to_spoken_number(match.group(0), language), normalized_line)
        normalized_line = re.sub(r"\s*:\s*", ", ", normalized_line)
        normalized_line = re.sub(r"\s*;\s*", ". ", normalized_line)
        normalized_line = re.sub(r"\s*=\s*", " egale ", normalized_line)
        normalized_line = re.sub(r"\s+", " ", normalized_line).strip(" ,")
        if not normalized_line:
            continue
        ending = "..." if index == 0 else "."
        spoken_sections.append(normalized_line.rstrip(".!?") + ending)
    return "\n\n".join(spoken_sections)


def _resolve_language(job: VideoJob) -> str:
    language_value = job.metadata.get("language")
    if isinstance(language_value, str) and language_value.strip():
        return language_value.strip().lower()
    return "fr"


def _to_ordinal(raw_number: str, language: str) -> str:
    if language.startswith("fr"):
        if raw_number == "1":
            return "premier"
        if raw_number == "2":
            return "deuxieme"
    return _to_spoken_number(raw_number, language)


def _to_spoken_number(raw_number: str, language: str) -> str:
    try:
        parsed_number = int(raw_number)
    except ValueError:
        return raw_number

    if language.startswith("fr") and parsed_number in FR_NUMBER_WORDS:
        return FR_NUMBER_WORDS[parsed_number]
    return raw_number


def _resolve_voice_delivery(job: VideoJob) -> VoiceDelivery:
    style_value = job.metadata.get("voice_delivery_style", DEFAULT_VOICE_DELIVERY_STYLE)
    if isinstance(style_value, str) and style_value.strip():
        normalized_style = style_value.strip().lower()
    else:
        normalized_style = DEFAULT_VOICE_DELIVERY_STYLE

    preset = VOICE_DELIVERY_PRESETS.get(normalized_style, VOICE_DELIVERY_PRESETS[DEFAULT_VOICE_DELIVERY_STYLE])
    speech_rate = _read_int_metadata(job, "voice_speed_wpm", preset["speed"], minimum=80, maximum=260)
    pitch = _read_int_metadata(job, "voice_pitch", preset["pitch"], minimum=0, maximum=99)
    amplitude = _read_int_metadata(job, "voice_amplitude", preset["amplitude"], minimum=0, maximum=200)
    return VoiceDelivery(
        style=normalized_style if normalized_style in VOICE_DELIVERY_PRESETS else DEFAULT_VOICE_DELIVERY_STYLE,
        speech_rate=speech_rate,
        pitch=pitch,
        amplitude=amplitude,
    )


def _resolve_subtitle_settings(job: VideoJob) -> SubtitleSettings:
    max_words = _read_int_metadata(
        job,
        "subtitle_max_words",
        DEFAULT_SUBTITLE_MAX_WORDS,
        minimum=3,
        maximum=10,
    )
    max_chars = _read_int_metadata(
        job,
        "subtitle_max_chars",
        DEFAULT_SUBTITLE_MAX_CHARS,
        minimum=18,
        maximum=48,
    )
    max_line_chars = _read_int_metadata(
        job,
        "subtitle_max_line_chars",
        DEFAULT_SUBTITLE_MAX_LINE_CHARS,
        minimum=12,
        maximum=24,
    )
    max_lines = _read_int_metadata(
        job,
        "subtitle_max_lines",
        DEFAULT_SUBTITLE_MAX_LINES,
        minimum=1,
        maximum=2,
    )
    min_duration_seconds = _read_float_metadata(
        job,
        "subtitle_min_duration_seconds",
        DEFAULT_SUBTITLE_MIN_DURATION_SECONDS,
        minimum=0.2,
        maximum=2.0,
    )
    max_duration_seconds = _read_float_metadata(
        job,
        "subtitle_max_duration_seconds",
        DEFAULT_SUBTITLE_MAX_DURATION_SECONDS,
        minimum=min_duration_seconds,
        maximum=4.0,
    )
    gap_seconds = _read_float_metadata(
        job,
        "subtitle_gap_seconds",
        DEFAULT_SUBTITLE_GAP_SECONDS,
        minimum=0.0,
        maximum=0.2,
    )
    return SubtitleSettings(
        max_words=max_words,
        max_chars=max_chars,
        max_line_chars=max_line_chars,
        max_lines=max_lines,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
        gap_seconds=gap_seconds,
    )


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


def _resolve_voice_name(job: VideoJob) -> str:
    explicit_voice = job.metadata.get("espeak_voice")
    if isinstance(explicit_voice, str) and explicit_voice.strip():
        return explicit_voice.strip()

    voice_profile = job.metadata.get("voice_profile", DEFAULT_VOICE_PROFILE)
    if isinstance(voice_profile, str) and voice_profile.strip():
        normalized_profile = voice_profile.strip().lower()
        mapped_voice = VOICE_PROFILE_ALIASES.get(normalized_profile)
        if mapped_voice:
            return mapped_voice
        if re.fullmatch(r"[a-z0-9_+\-]+", normalized_profile):
            return normalized_profile

    return DEFAULT_VOICE_PROFILE


def _build_tts_command(
    *,
    engine_binary: str,
    script_path: Path,
    voice_name: str,
    speech_rate: int,
    pitch: int,
    amplitude: int,
    output_path: Path,
) -> list[str]:
    return [
        engine_binary,
        "-b",
        "1",
        "-v",
        voice_name,
        "-s",
        str(speech_rate),
        "-p",
        str(pitch),
        "-a",
        str(amplitude),
        "-w",
        str(output_path),
        "-f",
        str(script_path),
    ]


def _read_wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frame_count = handle.getnframes()
            sample_rate = handle.getframerate()
    except wave.Error as exc:
        raise VoiceGenerationError(f"generated audio is not a readable WAV file: {path}") from exc

    if sample_rate <= 0:
        raise VoiceGenerationError(f"generated audio has an invalid sample rate: {path}")
    return frame_count / float(sample_rate)


def _build_subtitle_content(
    script_text: str,
    duration_seconds: float,
    *,
    settings: SubtitleSettings,
) -> str:
    if duration_seconds <= 0:
        raise VoiceGenerationError("cannot generate subtitles from a non-positive audio duration")

    cues = _build_subtitle_cues(script_text, settings=settings)
    if not cues:
        raise VoiceGenerationError("script is empty after subtitle segmentation")

    cue_durations = _assign_cue_durations(cues, duration_seconds, settings=settings)
    elapsed = 0.0
    srt_blocks: list[str] = []
    for index, (cue_text, cue_duration) in enumerate(zip(cues, cue_durations, strict=True), start=1):
        start_time = elapsed
        end_time = min(duration_seconds, start_time + cue_duration)
        if index == len(cues):
            end_time = duration_seconds
        srt_blocks.append(
            f"{index}\n{_format_srt_timestamp(start_time)} --> {_format_srt_timestamp(end_time)}\n{cue_text}"
        )
        elapsed = min(duration_seconds, end_time + settings.gap_seconds)

    return "\n\n".join(srt_blocks) + "\n"


def _build_subtitle_cues(script_text: str, *, settings: SubtitleSettings) -> list[str]:
    normalized_text = _normalize_script_text(script_text)
    if not normalized_text:
        return []

    parts = [part.strip(" ,") for part in CLAUSE_SPLIT_RE.split(normalized_text) if part.strip()]
    if not parts:
        return [_wrap_subtitle_lines(_chunk_text(normalized_text), settings=settings)]

    cues: list[str] = []
    for part in parts:
        chunks = _chunk_words(part, max_words=settings.max_words, max_chars=settings.max_chars)
        for chunk in chunks:
            wrapped_chunk = _wrap_subtitle_lines(chunk, settings=settings)
            if wrapped_chunk:
                cues.append(wrapped_chunk)

    cleaned_cues = [cue for cue in (cue.strip() for cue in cues) if cue]
    return cleaned_cues or [_wrap_subtitle_lines(_chunk_text(normalized_text), settings=settings)]


def _chunk_words(text: str, *, max_words: int, max_chars: int) -> list[str]:
    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    current_words: list[str] = []
    for word in words:
        tentative_words = current_words + [word]
        tentative_text = " ".join(tentative_words)
        if current_words and (len(tentative_words) > max_words or len(tentative_text) > max_chars):
            chunks.append(" ".join(current_words))
            current_words = [word]
        else:
            current_words = tentative_words

    if current_words:
        chunks.append(" ".join(current_words))
    return chunks


def _wrap_subtitle_lines(text: str, *, settings: SubtitleSettings) -> str:
    words = text.split()
    if not words:
        return ""
    if settings.max_lines == 1 or len(text) <= settings.max_line_chars:
        return " ".join(words)

    best_break_index: int | None = None
    best_score: tuple[int, int] | None = None
    for break_index in range(1, len(words)):
        first_line = " ".join(words[:break_index])
        second_line = " ".join(words[break_index:])
        if len(first_line) > settings.max_line_chars or len(second_line) > settings.max_line_chars:
            continue
        score = (abs(len(first_line) - len(second_line)), max(len(first_line), len(second_line)))
        if best_score is None or score < best_score:
            best_score = score
            best_break_index = break_index

    if best_break_index is None:
        return " ".join(words)

    first_line = " ".join(words[:best_break_index])
    second_line = " ".join(words[best_break_index:])
    return f"{first_line}\n{second_line}"


def _assign_cue_durations(
    cues: list[str],
    total_duration_seconds: float,
    *,
    settings: SubtitleSettings,
) -> list[float]:
    if not cues:
        return []

    gap_total = settings.gap_seconds * max(0, len(cues) - 1)
    available_duration = max(0.1, total_duration_seconds - gap_total)
    weights = [max(1, _count_words(cue)) for cue in cues]
    total_weight = sum(weights)
    durations = [available_duration * (weight / total_weight) for weight in weights]

    effective_min_duration = min(
        settings.min_duration_seconds,
        max(0.22, available_duration / max(1, len(cues)) * 0.9),
    )
    effective_max_duration = max(effective_min_duration, settings.max_duration_seconds)
    durations = [_clamp(duration, effective_min_duration, effective_max_duration) for duration in durations]

    difference = available_duration - sum(durations)
    for _ in range(12):
        if abs(difference) <= 0.001:
            break
        adjustable_indexes = [
            index
            for index, duration in enumerate(durations)
            if (difference > 0 and duration < effective_max_duration - 0.001)
            or (difference < 0 and duration > effective_min_duration + 0.001)
        ]
        if not adjustable_indexes:
            break
        adjustable_weight_total = sum(weights[index] for index in adjustable_indexes)
        for index in adjustable_indexes:
            delta = difference * (weights[index] / adjustable_weight_total)
            durations[index] = _clamp(durations[index] + delta, effective_min_duration, effective_max_duration)
        difference = available_duration - sum(durations)

    if durations:
        durations[-1] += available_duration - sum(durations)
    return durations


def _chunk_text(text: str) -> str:
    return " ".join(text.split())


def _count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _format_srt_timestamp(value: float) -> str:
    total_milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _format_command(command: list[str]) -> str:
    return " ".join(_shell_quote(part) for part in command)


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe_characters = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/:=+")
    if all(character in safe_characters for character in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


async def _run_command(command: list[str]) -> None:
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
        raise VoiceGenerationError(
            f"command failed with exit code {process.returncode}: "
            f"{_format_command(command)}\n{error_output}"
        )


__all__ = ["VoiceGenerationError", "generate_voice_step"]
