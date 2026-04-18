from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Mapping

from job_paths import (
    AUDIO_MIX_FILENAME,
    EDIT_MANIFEST_FILENAME,
    FINAL_VIDEO_FILENAME,
    FINAL_VIDEO_RENDERING_FILENAME,
    SCENE_CLIP_LIST_FILENAME,
    SCENE_CLIPS_DIRNAME,
    SCENES_CONCAT_FILENAME,
    build_scene_clip_filename,
    resolve_job_path,
)
from pipeline.captions import (
    DEFAULT_CAPTION_FONT_NAME,
    DEFAULT_CAPTION_MAX_CHARS_PER_LINE,
    DEFAULT_CAPTION_MAX_DURATION_MS,
    DEFAULT_CAPTION_MAX_LINES,
    DEFAULT_CAPTION_MAX_WORDS_PER_CHUNK,
    DEFAULT_CAPTION_MIN_DURATION_MS,
    DEFAULT_CAPTION_SAFE_BOTTOM_MARGIN,
    DEFAULT_CAPTION_SAFE_SIDE_MARGIN,
    DEFAULT_FALLBACK_SUBTITLE_FONT_SIZE,
)
from pipeline.edit_planner import EditManifest, SceneEditPlan, SFX_PROFILES, load_edit_manifest
from pipeline.scene_planner import (
    SceneManifest,
    SceneSegment,
    compute_overlay_role,
    load_scene_manifest,
    save_scene_manifest,
)
from pipeline.visual_manifest import SceneVisualAsset, VisualManifest, load_visual_manifest
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
DEFAULT_EDIT_VOICE_GAIN_DB = 0.0
DEFAULT_EDIT_DUCKING_THRESHOLD = 0.08
DEFAULT_EDIT_DUCKING_RATIO = 8.0
DEFAULT_EDIT_DUCKING_ATTACK_MS = 15
DEFAULT_EDIT_DUCKING_RELEASE_MS = 180
DEFAULT_SUBTITLE_FONT_NAME = DEFAULT_CAPTION_FONT_NAME
DEFAULT_STORY_FONT_NAME = "Noto Sans Display"
DEFAULT_SYMBOL_FONT_NAME = "DejaVu Sans"
DEFAULT_SUBTITLE_FONT_SIZE = DEFAULT_FALLBACK_SUBTITLE_FONT_SIZE
SUBTITLE_FONT_FALLBACKS = (
    DEFAULT_SUBTITLE_FONT_NAME,
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
    "sans-serif",
)
DISPLAY_FONT_FALLBACKS = (
    DEFAULT_STORY_FONT_NAME,
    DEFAULT_SUBTITLE_FONT_NAME,
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
    "sans-serif",
)
SYMBOL_FONT_FALLBACKS = (
    DEFAULT_SYMBOL_FONT_NAME,
    "Noto Sans",
    DEFAULT_SUBTITLE_FONT_NAME,
    "sans-serif",
)
DEFAULT_SUBTITLE_MARGIN_V = DEFAULT_CAPTION_SAFE_BOTTOM_MARGIN
DEFAULT_SUBTITLE_OUTLINE = 2
DEFAULT_SUBTITLE_SHADOW = 0
DEFAULT_SCENE_MOTION_SCALE = 1.14
DEFAULT_EDIT_BGM_VOLUME = 0.18
DEFAULT_EDIT_SFX_VOLUME = 0.42
DEFAULT_EDIT_MAX_SFX_PER_VIDEO = 6
DEFAULT_EDIT_MIN_SFX_GAP_MS = 500
DEFAULT_EDIT_SFX_STRONG_COOLDOWN_SCENES = 1
DEFAULT_EDIT_SCENE_FADE_SECONDS = 0.18
DEFAULT_EDIT_FLASH_SECONDS = 0.08
DEFAULT_EDIT_BLUR_SIGMA = 18
DEFAULT_EDIT_CAPTION_WRAP = DEFAULT_CAPTION_MAX_CHARS_PER_LINE
DEFAULT_EDIT_CAPTION_MIN_DURATION_MS = DEFAULT_CAPTION_MIN_DURATION_MS
DEFAULT_EDIT_CAPTION_MAX_DURATION_MS = DEFAULT_CAPTION_MAX_DURATION_MS
DEFAULT_EDIT_CAPTION_MAX_WORDS_PER_CHUNK = DEFAULT_CAPTION_MAX_WORDS_PER_CHUNK
DEFAULT_EDIT_CAPTION_MAX_CHARS_PER_LINE = DEFAULT_CAPTION_MAX_CHARS_PER_LINE
DEFAULT_EDIT_CAPTION_MAX_LINES = DEFAULT_CAPTION_MAX_LINES
DEFAULT_EDIT_CAPTION_SAFE_BOTTOM_MARGIN = DEFAULT_CAPTION_SAFE_BOTTOM_MARGIN
DEFAULT_EDIT_CAPTION_SAFE_SIDE_MARGIN = DEFAULT_CAPTION_SAFE_SIDE_MARGIN
EDIT_SCENE_CAPTIONS_DIRNAME = "scene_captions"
DEFAULT_EDIT_AUDIO_ROOT_CANDIDATES = (
    "tiktok/asset_son",
    "assets/audio",
    "config/audio",
    "config/examples/audio",
)
DEFAULT_SFX_ENABLED = True
DEFAULT_SFX_STRATEGY = "scene_role+scene_type+proof_mode+visual_mode+cue_timeline+transitions_v2"
DEFAULT_SFX_TIMING_MODE = "content_weighted_offsets_v1"
DEFAULT_SFX_GAIN_STRATEGY = "profile_base+scene_role+placement+voice_guardrail_v1"
DEFAULT_SFX_ASSET_SELECTION_MODE = "profile_weighted_rotation+recent_history_v1"
DEFAULT_EDIT_SFX_TIMING_SPREAD_MS = 110
DEFAULT_EDIT_SFX_RECENT_WINDOW = 3
DEFAULT_EDIT_SFX_SAME_FAMILY_COOLDOWN_SCENES = 1
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
HOOK_SFX_PROFILES = ("hit_soft", "hit_strong", "glitch_light", "whoosh_fast")
PROOF_SFX_PROFILES = ("click_ui_soft", "click_ui_hard", "pop_light", "hit_soft")
EXPLAIN_SFX_PROFILES = ("whoosh_soft", "pop_short")
CTA_SFX_PROFILES = ("pop_short", "hit_soft", "whoosh_soft", "riser_short")
STRONG_SFX_PROFILES = frozenset({"hit_strong", "whoosh_fast", "glitch_light"})
LEGACY_TIKTOK_SFX_PROFILE_PATHS = {
    "click_ui_soft": ("UI click/little click.mp3",),
    "click_ui_hard": ("UI click/medium click.mp3",),
    "glitch_light": ("error/error basic.mp3", "error/windows error.mp3"),
    "hit_soft": ("hit/body impacting.mp3",),
    "hit_strong": ("hit/pucnh hard.mp3", "error/effondrement.mp3"),
    "pop_light": ("pop/pop1.mp3",),
    "pop_short": ("pop/pop2.mp3", "pop/pop1.mp3"),
    "riser_short": ("riser/riser tension.mp3", "riser/riser tension2.mp3"),
    "whoosh_fast": ("whoosh/whoosh1.mp3", "whoosh/whoosh air.mp3"),
    "whoosh_soft": ("whoosh/whoosh air.mp3", "whoosh/whoosh1.mp3"),
}
UI_FORWARD_VISUAL_MODES = frozenset({"dashboard", "ui", "screenshot"})
UI_REVEAL_TOKENS = (
    "app",
    "click",
    "dashboard",
    "ecran",
    "interface",
    "outil",
    "screen",
    "tool",
    "ui",
)
PROOF_REVEAL_TOKENS = (
    "analytics",
    "chiffre",
    "gain",
    "preuve",
    "proof",
    "result",
    "resultat",
)
HOOK_INTENSITY_TOKENS = (
    "3",
    "comment",
    "erreur",
    "mistake",
    "pourquoi",
    "stop",
)
CAPTION_RENDERER_VERSION = "premium_ass_v2"
CAPTION_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)?%?|[\w]+(?:[.'/:%+-][\w]+)*",
    flags=re.UNICODE,
)
SCENE_STORY_WORD_RE = re.compile(
    r"\d+(?:[.,]\d+)?%?|[\w]+(?:[.'/:%+-][\w]+)*",
    flags=re.UNICODE,
)
CAPTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "au",
        "aux",
        "avec",
        "ce",
        "ces",
        "cette",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "en",
        "est",
        "et",
        "for",
        "il",
        "ils",
        "in",
        "je",
        "la",
        "le",
        "les",
        "mais",
        "my",
        "on",
        "or",
        "par",
        "pas",
        "pour",
        "que",
        "qui",
        "si",
        "sur",
        "ta",
        "te",
        "tes",
        "the",
        "to",
        "ton",
        "tu",
        "un",
        "une",
        "vos",
        "votre",
        "with",
        "your",
    }
)
CAPTION_WEAK_TOKENS = frozenset(
    {
        "avoir",
        "etre",
        "faire",
        "juste",
        "plus",
        "then",
        "thing",
        "truc",
        "use",
        "utilise",
        "utiliser",
        "voir",
    }
)
CAPTION_PROOF_TOKENS = frozenset(
    {
        "analytics",
        "audit",
        "avant",
        "apres",
        "capture",
        "case",
        "check",
        "chiffre",
        "chiffres",
        "concret",
        "concrets",
        "demo",
        "preuve",
        "proof",
        "screen",
        "screenshot",
        "visible",
    }
)
CAPTION_RESULT_TOKENS = frozenset(
    {
        "boost",
        "gain",
        "result",
        "resultat",
        "resultats",
        "retention",
        "save",
        "saved",
        "temps",
        "wins",
    }
)
CAPTION_OPPOSITION_TOKENS = frozenset(
    {
        "contre",
        "mais",
        "instead",
        "minus",
        "pas",
        "plutot",
        "sans",
        "vs",
    }
)
CAPTION_ACTION_TOKENS = frozenset(
    {
        "abonne",
        "abonne-toi",
        "check",
        "commente",
        "copie",
        "demande",
        "garde",
        "like",
        "save",
        "sauve",
        "teste",
    }
)
CAPTION_DURATION_UNITS = frozenset(
    {"h", "heure", "heures", "jour", "jours", "min", "minute", "minutes", "mois", "s", "sec", "seconde", "secondes"}
)
SCENE_STORY_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "au",
        "aux",
        "avec",
        "ce",
        "cet",
        "cette",
        "dans",
        "de",
        "des",
        "du",
        "en",
        "est",
        "et",
        "for",
        "il",
        "la",
        "le",
        "les",
        "mais",
        "on",
        "par",
        "pas",
        "pour",
        "que",
        "qui",
        "sur",
        "ta",
        "tes",
        "the",
        "ton",
        "tu",
        "un",
        "une",
        "vos",
        "votre",
    }
)
IMPACT_TEXT_SFX_PROFILES = frozenset({"click_ui_hard", "glitch_light", "hit_soft", "hit_strong", "whoosh_fast"})


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
class SceneStoryOverlayPlan:
    title_text: str
    keyword_text: str | None
    scene_keywords: tuple[str, ...]
    title_position: str
    keyword_position: str
    context_icon: str | None
    context_icon_color: str | None
    overlay_applied: tuple[str, ...]
    impact_zoom: bool
    pace_role: str


@dataclass(slots=True, frozen=True)
class StoryOverlayRoleProfile:
    pace_role: str
    accent_color: str
    title_center_font_size: int
    title_top_font_size: int
    title_center_y_offset: int
    title_top_y: int
    title_line_spacing: int
    title_border_width: int
    title_shadow_y: int
    title_shadow_alpha: float
    title_alpha_delay_seconds: float
    title_alpha_duration_seconds: float
    title_intro_duration_seconds: float
    title_settle_duration_seconds: float
    title_start_offset_px: int
    title_overshoot_px: int
    keyword_font_size: int
    keyword_side_y: int
    keyword_bottom_y: int
    keyword_border_width: int
    keyword_shadow_y: int
    keyword_alpha_delay_seconds: float
    keyword_alpha_duration_seconds: float
    keyword_intro_duration_seconds: float
    keyword_settle_duration_seconds: float
    keyword_start_offset_px: int
    keyword_overshoot_px: int


@dataclass(slots=True, frozen=True)
class CaptionSettings:
    min_duration_ms: int
    max_duration_ms: int
    max_words_per_chunk: int
    max_chars_per_line: int
    max_lines: int
    safe_bottom_margin: int
    safe_side_margin: int


@dataclass(slots=True, frozen=True)
class VoiceWordTiming:
    index: int
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(slots=True, frozen=True)
class CaptionToken:
    text: str
    normalized: str
    start_seconds: float
    end_seconds: float
    break_after: bool = False


@dataclass(slots=True, frozen=True)
class CaptionChunk:
    text: str
    start_seconds: float
    end_seconds: float
    emphasis_tokens: tuple[str, ...]
    skipped_reason: str | None
    timing_source: str
    line_count: int
    word_count: int

    def to_metadata(self) -> dict[str, str | int | float | list[str] | None]:
        return {
            "text": self.text,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(max(0.01, self.end_seconds - self.start_seconds), 3),
            "emphasis_tokens": list(self.emphasis_tokens),
            "skipped_reason": self.skipped_reason,
            "timing_source": self.timing_source,
            "line_count": self.line_count,
            "word_count": self.word_count,
        }


@dataclass(slots=True, frozen=True)
class CaptionStyleProfile:
    name: str
    font_name: str
    font_size: int
    primary_color: str
    highlight_color: str
    outline_color: str
    back_color: str
    bold: int
    outline: int
    shadow: int
    spacing: float
    alignment: int
    border_style: int
    margin_l: int
    margin_r: int
    margin_v: int
    wrap_chars: int
    max_words: int
    max_duration_ms: int


@dataclass(slots=True, frozen=True)
class RuntimeFontResolution:
    requested_name: str
    resolved_name: str
    used_fallback: bool


@dataclass(slots=True, frozen=True)
class AudioLayerSpec:
    kind: str
    profile: str
    path: Path
    start_seconds: float
    duration_seconds: float
    volume: float
    loop: bool


@dataclass(slots=True, frozen=True)
class AudioEventSpec:
    scene_id: str
    scene_index: int
    scene_role: str
    scene_type: str
    proof_mode: str
    visual_asset_mode: str
    kind: str
    profile: str
    time_seconds: float
    gain_db: float
    mix_volume: float | None
    placement: str
    asset_path: str | None = None
    selected_reason: str | None = None
    fallback_from: str | None = None
    transition_in: str | None = None
    transition_out: str | None = None
    timing_mode: str | None = None
    timing_offset_ms: int | None = None
    gain_strategy: str | None = None
    asset_selection_reason: str | None = None
    profile_family: str | None = None
    sfx_candidates: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, str | int | float | None | list[str]]:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "scene_role": self.scene_role,
            "scene_type": self.scene_type,
            "proof_mode": self.proof_mode,
            "visual_asset_mode": self.visual_asset_mode,
            "kind": self.kind,
            "profile": self.profile,
            "time_seconds": round(self.time_seconds, 3),
            "gain_db": round(self.gain_db, 2),
            "mix_volume": None if self.mix_volume is None else round(self.mix_volume, 3),
            "placement": self.placement,
            "asset_path": self.asset_path,
            "selected_reason": self.selected_reason,
            "fallback_from": self.fallback_from,
            "transition_in": self.transition_in,
            "transition_out": self.transition_out,
            "timing_mode": self.timing_mode,
            "timing_offset_ms": self.timing_offset_ms,
            "gain_strategy": self.gain_strategy,
            "asset_selection_reason": self.asset_selection_reason,
            "profile_family": self.profile_family,
            "sfx_candidates": list(self.sfx_candidates),
        }


@dataclass(slots=True)
class SceneSfxDecision:
    scene_id: str
    scene_index: int
    scene_role: str
    scene_type: str
    proof_mode: str
    visual_asset_mode: str
    transition_in: str
    transition_out: str
    sfx_candidates: list[str]
    placement_candidates: list[str]
    sfx_selected: str | None
    selected_reason: str | None
    event_time_seconds: float | None
    placement: str | None
    skipped_reason: str | None
    kind: str | None = None
    mixed: bool = False
    fallback_from: str | None = None
    resolved_asset_path: str | None = None
    mix_volume: float | None = None
    gain_db: float | None = None
    gain_strategy: str | None = None
    timing_mode: str | None = None
    timing_offset_ms: int | None = None
    asset_selection_reason: str | None = None
    profile_family: str | None = None

    def to_metadata(self) -> dict[str, str | int | float | bool | None | list[str]]:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "scene_role": self.scene_role,
            "scene_type": self.scene_type,
            "proof_mode": self.proof_mode,
            "visual_asset_mode": self.visual_asset_mode,
            "transition_in": self.transition_in,
            "transition_out": self.transition_out,
            "sfx_candidates": list(self.sfx_candidates),
            "placement_candidates": list(self.placement_candidates),
            "sfx_selected": self.sfx_selected,
            "selected_reason": self.selected_reason,
            "event_time_seconds": (
                None if self.event_time_seconds is None else round(self.event_time_seconds, 3)
            ),
            "placement": self.placement,
            "skipped_reason": self.skipped_reason,
            "kind": self.kind,
            "mixed": self.mixed,
            "fallback_from": self.fallback_from,
            "resolved_asset_path": self.resolved_asset_path,
            "mix_volume": None if self.mix_volume is None else round(self.mix_volume, 3),
            "gain_db": None if self.gain_db is None else round(self.gain_db, 2),
            "gain_strategy": self.gain_strategy,
            "timing_mode": self.timing_mode,
            "timing_offset_ms": self.timing_offset_ms,
            "asset_selection_reason": self.asset_selection_reason,
            "profile_family": self.profile_family,
        }


@dataclass(slots=True, frozen=True)
class ResolvedAudioMixPlan:
    layers: list[AudioLayerSpec]
    missing_profiles: list[str]
    sfx_events: list[AudioEventSpec]
    sfx_decisions: list[SceneSfxDecision]
    primary_bgm_profile: str | None
    available_sfx_assets: dict[str, str]
    available_sfx_asset_pool: dict[str, tuple[str, ...]]

    @property
    def bgm_layer_count(self) -> int:
        return sum(layer.kind == "bgm" for layer in self.layers)

    @property
    def sfx_layer_count(self) -> int:
        return sum(layer.kind == "sfx" for layer in self.layers)


async def assemble_video_step(job: VideoJob, job_dir: Path) -> None:
    _ensure_binary_available("ffmpeg")
    _ensure_binary_available("ffprobe")
    resolve_runtime_subtitle_font(job)

    scene_manifest = load_scene_manifest(job)
    is_multi_scene = scene_manifest is not None and len(scene_manifest.scenes) > 1
    if is_multi_scene:
        _resolve_runtime_edit_caption_font(job)
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
    final_audio_mode = job.metadata.get("edit_audio_mode")
    if isinstance(final_audio_mode, str) and final_audio_mode.strip():
        job.set_metadata("final_video_audio_mode", final_audio_mode.strip())
    else:
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
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,fps={TARGET_FPS}"
        )
    ]
    if inputs.subtitles is not None:
        filter_chain.append("format=rgba")
        filter_chain.append(_build_subtitle_filter(job, inputs.subtitles))
        filter_chain.append("format=yuv420p")

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
    caption_settings = _resolve_caption_settings(job)
    voice_word_timing = _load_voice_word_timing(job)
    caption_debug_entries: list[dict[str, object]] = []
    scene_storyboard: list[dict[str, object]] = []
    if can_burn_per_scene_captions:
        captions_dir.mkdir(parents=True, exist_ok=True)

    for scene in manifest.scenes:
        scene_visual = visual_manifest.get_scene_asset(scene.scene_id) if visual_manifest is not None else None
        asset_path = _resolve_scene_asset_path(job, scene, visual_manifest)
        output_clip_path = clips_dir / build_scene_clip_filename(scene.index)
        if output_clip_path.exists():
            output_clip_path.unlink()

        render_plan = _build_scene_render_plan(scene, edit_manifest)
        if render_plan is not None:
            caption_path = None
            if can_burn_per_scene_captions:
                caption_path = captions_dir / f"{scene.scene_id}.ass"
                try:
                    caption_document, caption_debug = _build_scene_caption_ass(
                        job=job,
                        scene=scene,
                        plan=render_plan,
                        settings=caption_settings,
                        voice_word_timing=voice_word_timing,
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "job=%s scene=%s caption_planner_fallback=%s",
                        job.job_id,
                        scene.scene_id,
                        exc,
                    )
                    caption_document, caption_debug = _build_fallback_scene_caption_ass(
                        job=job,
                        scene=scene,
                        plan=render_plan,
                        settings=caption_settings,
                        fallback_reason=str(exc),
                    )
                caption_path.write_text(caption_document, encoding="utf-8")
                caption_debug_entries.append(caption_debug)
                LOGGER.debug(
                    "job=%s scene=%s caption_mode=%s style=%s timing=%s chunks=%s",
                    job.job_id,
                    scene.scene_id,
                    caption_debug.get("caption_mode"),
                    caption_debug.get("text_style"),
                    caption_debug.get("timing_source"),
                    len(caption_debug.get("chunks", [])),
                )
            render_command = _build_edited_scene_clip_command(
                job=job,
                scene=scene,
                scene_visual=scene_visual,
                plan=render_plan,
                asset_path=asset_path,
                caption_path=caption_path,
                output_path=output_clip_path,
            )
        else:
            render_command = _build_scene_clip_command(
                job=job,
                scene=scene,
                scene_visual=scene_visual,
                asset_path=asset_path,
                output_path=output_clip_path,
            )
        story_plan = _build_scene_story_overlay_plan(scene=scene, scene_visual=scene_visual, plan=render_plan)
        scene_storyboard.append(
            {
                "scene_id": scene.scene_id,
                "scene_index": scene.index,
                "scene_title": story_plan.title_text,
                "scene_keywords": list(story_plan.scene_keywords),
                "visual_asset_used": (
                    scene_visual.visual_asset_used
                    if scene_visual is not None and scene_visual.visual_asset_used
                    else str(asset_path)
                ),
                "overlay_applied": list(story_plan.overlay_applied),
                "title_position": story_plan.title_position,
                "keyword_position": story_plan.keyword_position,
            }
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
                overlay_role=scene.overlay_role,
                overlay_text=scene.overlay_text,
                subtitle_text=scene.subtitle_text,
                visual_intensity_level=scene.visual_intensity_level,
                primary_focus=scene.primary_focus,
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

    audio_plan = ResolvedAudioMixPlan(
        layers=[],
        missing_profiles=[],
        sfx_events=[],
        sfx_decisions=[],
        primary_bgm_profile=None,
        available_sfx_assets={},
        available_sfx_asset_pool={},
    )
    if edit_manifest is not None:
        audio_plan = await _resolve_audio_layers(
            job=job,
            manifest=manifest,
            edit_manifest=edit_manifest,
        )

    audio_mix_path = (job_dir / AUDIO_MIX_FILENAME).resolve()
    if audio_mix_path.exists():
        audio_mix_path.unlink()

    prepared_audio_path: Path | None = None
    if inputs.voice_audio is not None or audio_plan.layers:
        mix_command = _build_audio_mix_command(
            job=job,
            voice_audio_path=inputs.voice_audio,
            audio_layers=audio_plan.layers,
            output_path=audio_mix_path,
        )
        LOGGER.debug("job=%s audio_mix_command=%s", job.job_id, _format_command(mix_command))
        await _run_command(mix_command)
        commands_run.append(mix_command)
        mix_probe = await _probe_media(audio_mix_path)
        if not mix_probe.has_audio:
            raise AssemblerError(f"rendered audio mix is missing an audio stream: {audio_mix_path}")
        prepared_audio_path = audio_mix_path

    mux_command = _build_prepared_audio_mux_command(
        job=job,
        source_video_path=concat_video_path,
        audio_path=prepared_audio_path,
        subtitles_path=None if can_burn_per_scene_captions else inputs.subtitles,
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
    job.set_metadata(
        "scene_caption_renderer",
        CAPTION_RENDERER_VERSION if can_burn_per_scene_captions else "global_srt",
    )
    job.set_metadata("scene_caption_debug", caption_debug_entries)
    job.set_metadata("scene_caption_debug_count", len(caption_debug_entries))
    job.set_metadata("scene_visual_storyboard", scene_storyboard)
    job.set_metadata("scene_visual_storyboard_count", len(scene_storyboard))
    ducking_active = inputs.voice_audio is not None and audio_plan.primary_bgm_profile is not None
    audio_mode = _resolve_audio_mix_mode(
        has_voice_audio=inputs.voice_audio is not None,
        bgm_layer_count=audio_plan.bgm_layer_count,
        sfx_layer_count=audio_plan.sfx_layer_count,
        ducking_active=ducking_active,
    )
    job.set_metadata("edit_audio_mode", audio_mode)
    job.set_metadata("edit_audio_ducking_active", ducking_active)
    job.set_metadata("edit_audio_bgm_profile", audio_plan.primary_bgm_profile)
    job.set_metadata("edit_audio_layer_count", len(audio_plan.layers))
    job.set_metadata("edit_audio_bgm_layer_count", audio_plan.bgm_layer_count)
    job.set_metadata("edit_audio_sfx_layer_count", audio_plan.sfx_layer_count)
    job.set_metadata("edit_audio_sfx_event_count", len(audio_plan.sfx_events))
    job.set_metadata("edit_audio_sfx_mixed_count", audio_plan.sfx_layer_count)
    job.set_metadata(
        "edit_audio_sfx_selected_count",
        sum(1 for decision in audio_plan.sfx_decisions if decision.sfx_selected is not None),
    )
    job.set_metadata("edit_audio_sfx_decision_count", len(audio_plan.sfx_decisions))
    job.set_metadata("edit_audio_events", [event.to_metadata() for event in audio_plan.sfx_events])
    job.set_metadata(
        "edit_audio_sfx_decisions",
        [decision.to_metadata() for decision in audio_plan.sfx_decisions],
    )
    job.set_metadata("edit_audio_missing_profiles", audio_plan.missing_profiles)
    job.set_metadata("audio_mix_path", str(prepared_audio_path) if prepared_audio_path is not None else None)
    job.set_metadata("audio_mix_format", prepared_audio_path.suffix.lstrip(".") if prepared_audio_path is not None else None)
    job.set_metadata("sfx_enabled", _is_sfx_enabled(job))
    job.set_metadata("sfx_strategy", DEFAULT_SFX_STRATEGY)
    job.set_metadata(
        "sfx_timing_mode",
        sorted({event.timing_mode for event in audio_plan.sfx_events if event.timing_mode}) or [DEFAULT_SFX_TIMING_MODE],
    )
    job.set_metadata(
        "sfx_gain_strategy",
        sorted({event.gain_strategy for event in audio_plan.sfx_events if event.gain_strategy}) or [DEFAULT_SFX_GAIN_STRATEGY],
    )
    job.set_metadata("sfx_asset_selection_mode", DEFAULT_SFX_ASSET_SELECTION_MODE)
    job.set_metadata(
        "sfx_asset_count",
        len(
            {
                asset_path
                for asset_paths in audio_plan.available_sfx_asset_pool.values()
                for asset_path in asset_paths
            }
        ),
    )
    job.set_metadata("sfx_available_profiles", sorted(audio_plan.available_sfx_assets))
    job.set_metadata("sfx_events", [event.to_metadata() for event in audio_plan.sfx_events])
    job.set_metadata("sfx_selection_log", [decision.to_metadata() for decision in audio_plan.sfx_decisions])
    job.set_metadata("sfx_mix_path", str(prepared_audio_path) if prepared_audio_path is not None else None)
    job.set_metadata(
        "sfx_recent_files",
        [str(event.asset_path) for event in audio_plan.sfx_events if event.asset_path][-DEFAULT_EDIT_SFX_RECENT_WINDOW:],
    )
    job.set_metadata(
        "sfx_mix_volume",
        _read_float_metadata(job, "edit_sfx_volume", DEFAULT_EDIT_SFX_VOLUME, minimum=0.0, maximum=1.4),
    )
    LOGGER.info(
        "job=%s audio_mix mode=%s bgm=%s ducking=%s sfx=%s missing=%s",
        job.job_id,
        audio_mode,
        audio_plan.primary_bgm_profile or "none",
        ducking_active,
        audio_plan.sfx_layer_count,
        audio_plan.missing_profiles,
    )
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
    scene_visual: SceneVisualAsset | None,
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
                job=job,
                scene=scene,
                scene_visual=scene_visual,
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
    job: VideoJob,
    scene: SceneSegment,
    scene_visual: SceneVisualAsset | None,
    plan: SceneRenderPlan,
    caption_path: Path | None,
    duration_seconds: float,
) -> str:
    parts: list[str] = []
    current_label = "motion"
    parts.append(
        f"[0:v]{_build_camera_pattern_filter(plan, duration_seconds=duration_seconds)}[{current_label}]"
    )
    next_label = "composite"
    parts.append(f"[{current_label}]format=rgba[{next_label}]")
    current_label = next_label

    overlay_filter = _build_overlay_filter(plan.overlay_style)
    if overlay_filter is not None:
        next_label = "overlay"
        parts.append(f"[{current_label}]{overlay_filter}[{next_label}]")
        current_label = next_label

    current_label = _append_scene_story_overlay_filters(
        job=job,
        parts=parts,
        input_label=current_label,
        scene=scene,
        scene_visual=scene_visual,
        plan=plan,
        duration_seconds=duration_seconds,
    )

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
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos:force_original_aspect_ratio=increase,"
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
        "flags=lanczos:force_original_aspect_ratio=increase:eval=frame,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='{x_expr}':y='(in_h-out_h)/2',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_micro_shake_filter(*, amplitude: int, zoom: float) -> str:
    x_expr = f"(in_w-out_w)/2+sin(t*9.5)*{amplitude}"
    y_expr = f"(in_h-out_h)/2+cos(t*13.0)*{max(2, amplitude - 2)}"
    return (
        f"scale=w='trunc({TARGET_WIDTH}*{zoom:.4f})':"
        f"h='trunc({TARGET_HEIGHT}*{zoom:.4f})':"
        "flags=lanczos:force_original_aspect_ratio=increase:eval=frame,"
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
        "flags=lanczos:force_original_aspect_ratio=increase:eval=frame,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_overlay_filter(overlay_style: str) -> str | None:
    if overlay_style == "subtle_vignette":
        return "vignette=PI/10"
    if overlay_style == "light_glow":
        return "eq=brightness=0.025:saturation=1.08:contrast=1.04"
    if overlay_style == "proof_frame":
        return "drawbox=x=42:y=42:w=iw-84:h=ih-84:color=white@0.15:t=2"
    if overlay_style == "cta_band":
        return "eq=brightness=0.010:saturation=1.02:contrast=1.01,drawbox=x=92:y=1368:w=896:h=2:color=white@0.09:t=fill"
    return None


def _append_scene_story_overlay_filters(
    *,
    job: VideoJob,
    parts: list[str],
    input_label: str,
    scene: SceneSegment,
    scene_visual: SceneVisualAsset | None,
    plan: SceneRenderPlan | None,
    duration_seconds: float,
) -> str:
    story_plan = _build_scene_story_overlay_plan(scene=scene, scene_visual=scene_visual, plan=plan)
    story_filter = _build_scene_story_filter_chain(
        job=job,
        story_plan=story_plan,
    )
    if not story_filter:
        return input_label
    output_label = f"story_{len(parts)}"
    parts.append(f"[{input_label}]{story_filter}[{output_label}]")
    if not story_plan.impact_zoom:
        return output_label

    zoom_output_label = f"story_zoom_{len(parts)}"
    parts.append(
        f"[{output_label}]{_build_story_impact_zoom_filter(duration_seconds=duration_seconds, pace_role=story_plan.pace_role)}[{zoom_output_label}]"
    )
    return zoom_output_label


def _build_scene_story_filter_chain(
    *,
    job: VideoJob,
    story_plan: SceneStoryOverlayPlan,
) -> str:
    if not story_plan.title_text and not story_plan.keyword_text:
        return ""

    filters: list[str] = []
    font_name = _escape_drawtext_value(_resolve_story_text_font_name(job))
    symbol_font_name = _escape_drawtext_value(_resolve_story_symbol_font_name(job))
    profile = _resolve_story_overlay_role_profile(story_plan)
    title_alpha = _escape_drawtext_expr(
        _build_story_alpha_expr(
            delay_seconds=profile.title_alpha_delay_seconds,
            duration_seconds=profile.title_alpha_duration_seconds,
        )
    )
    keyword_alpha = _escape_drawtext_expr(
        _build_story_alpha_expr(
            delay_seconds=profile.keyword_alpha_delay_seconds,
            duration_seconds=profile.keyword_alpha_duration_seconds,
        )
    )

    if story_plan.title_text:
        sanitized_title_text = _sanitize_story_drawtext_text(story_plan.title_text)
        title_lines = _wrap_scene_story_title(sanitized_title_text)
        title_text = _escape_drawtext_text("\\n".join(title_lines))
        title_base_y = (
            f"(h-text_h)/2-{profile.title_center_y_offset}"
            if story_plan.title_position == "center"
            else str(profile.title_top_y)
        )
        title_y = _escape_drawtext_expr(
            _build_story_pop_y_expr(
                final_y_expr=title_base_y,
                start_offset_px=profile.title_start_offset_px,
                overshoot_px=profile.title_overshoot_px,
                intro_duration_seconds=profile.title_intro_duration_seconds,
                settle_duration_seconds=profile.title_settle_duration_seconds,
            )
        )
        title_font_size = (
            str(profile.title_center_font_size)
            if story_plan.title_position == "center"
            else str(profile.title_top_font_size)
        )
        filters.append(
            "drawtext="
            f"font='{font_name}':"
            "expansion=none:"
            f"text='{title_text}':"
            f"fontsize={title_font_size}:"
            "fontcolor=0xFFF9FB:"
            f"borderw={profile.title_border_width}:bordercolor=0x111522@0.88:"
            f"shadowx=0:shadowy={profile.title_shadow_y}:shadowcolor=black@{profile.title_shadow_alpha:.2f}:"
            f"line_spacing={profile.title_line_spacing}:"
            "x='(w-text_w)/2':"
            f"y='{title_y}':"
            f"alpha='{title_alpha}'"
        )

    if story_plan.keyword_text:
        accent_color = story_plan.context_icon_color or profile.accent_color
        if story_plan.keyword_position == "side":
            if "highlight_box" in story_plan.overlay_applied:
                filters.append(
                    f"drawbox=x=692:y={profile.keyword_side_y - 14}:w=6:h=74:color={accent_color}@0.82:t=fill"
                )
            keyword_y_base = str(profile.keyword_side_y)
            icon_x = "716"
            keyword_x = "764" if story_plan.context_icon else "724"
        else:
            if "highlight_box" in story_plan.overlay_applied:
                filters.append(
                    f"drawbox=x=96:y={profile.keyword_bottom_y - 24}:w=132:h=5:color={accent_color}@0.78:t=fill"
                )
            keyword_y_base = str(profile.keyword_bottom_y)
            icon_x = "118"
            keyword_x = "166" if story_plan.context_icon else "126"

        keyword_y = _escape_drawtext_expr(
            _build_story_pop_y_expr(
                final_y_expr=keyword_y_base,
                start_offset_px=profile.keyword_start_offset_px,
                overshoot_px=profile.keyword_overshoot_px,
                intro_duration_seconds=profile.keyword_intro_duration_seconds,
                settle_duration_seconds=profile.keyword_settle_duration_seconds,
            )
        )

        if "context_icon" in story_plan.overlay_applied and story_plan.context_icon:
            icon_color = story_plan.context_icon_color or accent_color
            filters.append(
                "drawtext="
                f"font='{symbol_font_name}':"
                "expansion=none:"
                f"text='{_escape_drawtext_text(story_plan.context_icon)}':"
                "fontsize=42:"
                f"fontcolor={icon_color}:"
                "borderw=1:bordercolor=black@0.68:"
                f"shadowx=0:shadowy={profile.keyword_shadow_y}:shadowcolor=black@0.46:"
                f"x={icon_x}:y='{keyword_y}':"
                f"alpha='{keyword_alpha}'"
            )

        filters.append(
            "drawtext="
            f"font='{font_name}':"
            "expansion=none:"
            f"text='{_escape_drawtext_text(_sanitize_story_drawtext_text(story_plan.keyword_text))}':"
            f"fontsize={profile.keyword_font_size}:"
            "fontcolor=0xFFF9FB:"
            f"borderw={profile.keyword_border_width}:bordercolor=0x111522@0.82:"
            f"shadowx=0:shadowy={profile.keyword_shadow_y}:shadowcolor=black@0.50:"
            f"x={keyword_x}:y='{keyword_y}':"
            f"alpha='{keyword_alpha}'"
        )

    return ",".join(filters)


def _build_scene_story_overlay_plan(
    *,
    scene: SceneSegment,
    scene_visual: SceneVisualAsset | None,
    plan: SceneRenderPlan | None,
) -> SceneStoryOverlayPlan:
    overlay_role = scene.overlay_role or compute_overlay_role(scene)
    pace_role = _resolve_scene_story_pace_role(scene, plan)
    title_text = (
        scene_visual.scene_title
        if scene_visual is not None and scene_visual.scene_title is not None
        else _derive_scene_story_title(scene)
    )
    scene_keywords = (
        scene_visual.scene_keywords
        if scene_visual is not None and scene_visual.scene_keywords
        else tuple(_derive_scene_story_keywords(scene))
    )
    keyword_text = None
    title_position = "center" if pace_role == "hook" else "top"
    keyword_position = (
        "bottom"
        if pace_role == "cta"
        else
        "side"
        if scene.scene_type in {"proof", "interface"} or scene.visual_asset_mode in {"dashboard", "ui", "screenshot"}
        else "bottom"
    )
    has_visual_evidence = _scene_has_visual_evidence(scene, scene_visual)
    if (
        title_text
        and scene.visual_intensity_level is not None
        and scene.primary_focus == "visual_proof"
        and has_visual_evidence
    ):
        title_text = ""
    keyword_text = _derive_scene_context_label(
        scene,
        scene_visual=scene_visual,
        title_text=title_text,
        allow_when_title_is_centered=not bool(title_text and title_position == "center"),
    )
    if pace_role == "cta":
        if keyword_text is None and title_text:
            keyword_text = title_text
        if keyword_text:
            title_text = ""
    context_icon, context_icon_color = _derive_scene_context_icon(
        scene,
        keyword_text=keyword_text,
    )

    overlay_applied: list[str] = []
    if title_text:
        overlay_applied.append("title_text")
    if keyword_text:
        overlay_applied.extend(["keyword_text", "highlight_box"])
    if context_icon and keyword_text:
        overlay_applied.append("context_icon")

    impact_zoom = (
        bool(title_text)
        and (
            pace_role == "hook"
            or (plan is not None and plan.sfx_profile in IMPACT_TEXT_SFX_PROFILES)
            or overlay_role == "hook"
        )
    )
    if impact_zoom and "impact_zoom" not in overlay_applied:
        overlay_applied.append("impact_zoom")

    return SceneStoryOverlayPlan(
        title_text=title_text,
        keyword_text=keyword_text,
        scene_keywords=tuple(scene_keywords),
        title_position=title_position,
        keyword_position=keyword_position,
        context_icon=context_icon,
        context_icon_color=context_icon_color,
        overlay_applied=tuple(overlay_applied),
        impact_zoom=impact_zoom,
        pace_role=pace_role,
    )


def _scene_has_visual_evidence(
    scene: SceneSegment,
    scene_visual: SceneVisualAsset | None,
) -> bool:
    if scene_visual is None:
        return False

    visual_mode = (scene_visual.visual_asset_mode or scene.visual_asset_mode).strip().lower()
    if visual_mode not in {"dashboard", "workflow", "ui", "screenshot"}:
        return False

    if scene_visual.selected_by in {"explicit", "catalog_match"}:
        return True
    if scene_visual.selected_by in {"contextual_video", "contextual_image"}:
        return bool(
            scene_visual.matched_entities
            or scene_visual.matched_tags
            or scene_visual.matched_visual_categories
        )
    return False


def _derive_scene_story_title(scene: SceneSegment) -> str:
    if scene.overlay_text:
        overlay_words = _split_scene_story_words(scene.overlay_text)
        if overlay_words:
            return " ".join(overlay_words[: min(5, len(overlay_words))])
    if scene.visual_intensity_level is not None:
        return ""
    for candidate in (scene.highlight_text, scene.title_text, scene.source_text):
        words = _split_scene_story_words(candidate)
        if 2 <= len(words) <= 6:
            return " ".join(words)
        if len(words) > 6:
            return " ".join(words[:6])

    fallback_words = _split_scene_story_words(scene.source_text or scene.title_text or scene.highlight_text)
    if fallback_words:
        return " ".join(fallback_words[: max(1, min(6, len(fallback_words)))])
    return scene.scene_type.replace("_", " ").strip().title() or "Scene"


def _derive_scene_story_keywords(scene: SceneSegment) -> list[str]:
    raw_candidates: list[str] = []
    raw_candidates.extend(scene.keywords)
    raw_candidates.extend(_split_scene_story_words(scene.highlight_text))
    raw_candidates.extend(_split_scene_story_words(scene.title_text))
    raw_candidates.extend(_split_scene_story_words(scene.source_text))

    keywords: list[str] = []
    seen_normalized: set[str] = set()
    for candidate in raw_candidates:
        normalized_candidate = " ".join(candidate.split()).strip()
        lowered_candidate = normalized_candidate.lower()
        if not normalized_candidate:
            continue
        if len(lowered_candidate) <= 2 and not lowered_candidate.isdigit():
            continue
        if lowered_candidate in SCENE_STORY_STOPWORDS:
            continue
        if lowered_candidate in seen_normalized:
            continue
        seen_normalized.add(lowered_candidate)
        keywords.append(normalized_candidate)
        if len(keywords) == 3:
            break
    return keywords


def _split_scene_story_words(value: str) -> list[str]:
    if not value:
        return []
    return [match.strip() for match in SCENE_STORY_WORD_RE.findall(" ".join(value.split())) if match.strip()]


def _wrap_scene_story_title(title_text: str) -> list[str]:
    words = _split_scene_story_words(title_text)
    if len(words) <= 3:
        return [" ".join(words)] if words else []
    return [" ".join(words[:3]), " ".join(words[3:6])]


def _derive_scene_context_label(
    scene: SceneSegment,
    *,
    scene_visual: SceneVisualAsset | None,
    title_text: str,
    allow_when_title_is_centered: bool,
) -> str | None:
    if not allow_when_title_is_centered:
        return None

    candidates: list[str] = []
    if scene.overlay_text:
        candidates.append(scene.overlay_text)
    if scene.highlight_text:
        candidates.append(scene.highlight_text)
    if scene_visual is not None and scene_visual.scene_keywords:
        candidates.extend(scene_visual.scene_keywords)
    candidates.extend(scene.keywords)

    normalized_title = _normalize_font_identity(title_text)
    for candidate in candidates:
        normalized_candidate = _sanitize_story_drawtext_text(candidate)
        if not normalized_candidate:
            continue
        if _normalize_font_identity(normalized_candidate) == normalized_title:
            continue
        words = _split_scene_story_words(normalized_candidate)
        if not words:
            continue
        compact = " ".join(words[: min(3, len(words))])
        if _normalize_font_identity(compact) == normalized_title:
            continue
        if len(compact) < 3:
            continue
        return compact
    return None


def _derive_scene_context_icon(
    scene: SceneSegment,
    *,
    keyword_text: str | None,
) -> tuple[str | None, str | None]:
    signal_pool = " ".join(
        part
        for part in (
            scene.source_text,
            scene.highlight_text,
            scene.title_text,
            scene.overlay_text or "",
            keyword_text or "",
            scene.proof_mode,
            scene.scene_type,
            *scene.keywords,
        )
        if part
    ).lower()
    if any(token in signal_pool for token in ("alert", "bug", "erreur", "error", "fail", "risque")):
        return "⚠", "0xFFB347"
    if any(token in signal_pool for token in ("gain", "progress", "result", "resultat", "retention")) or any(
        character.isdigit() for character in signal_pool
    ):
        return "↗", "0x8CE99A"
    if any(token in signal_pool for token in ("heure", "minute", "minutes", "rapid", "temps", "time")):
        return "✓", "0x8ED6FF"
    if any(
        token in signal_pool
        for token in (
            "airtable",
            "api",
            "app",
            "chatgpt",
            "dashboard",
            "interface",
            "make",
            "n8n",
            "notion",
            "openai",
            "screen",
            "tool",
            "ui",
            "workflow",
            "zapier",
        )
    ):
        return "⚙", "0xA8D4FF"
    if keyword_text:
        return "✓", "0xA8D4FF"
    return None, None


def _resolve_scene_story_pace_role(scene: SceneSegment, plan: SceneRenderPlan | None) -> str:
    if plan is not None and plan.pace_role in {"hook", "proof", "explain", "cta"}:
        return plan.pace_role
    if scene.role == "hook" or scene.scene_type == "hook":
        return "hook"
    if scene.role == "cta" or scene.scene_type == "cta":
        return "cta"
    if scene.scene_type == "proof":
        return "proof"
    return "explain"


def _resolve_story_overlay_role_profile(story_plan: SceneStoryOverlayPlan) -> StoryOverlayRoleProfile:
    if story_plan.pace_role == "hook":
        return StoryOverlayRoleProfile(
            pace_role="hook",
            accent_color="0xFFD27A",
            title_center_font_size=92,
            title_top_font_size=72,
            title_center_y_offset=92,
            title_top_y=152,
            title_line_spacing=10,
            title_border_width=3,
            title_shadow_y=6,
            title_shadow_alpha=0.72,
            title_alpha_delay_seconds=0.0,
            title_alpha_duration_seconds=0.15,
            title_intro_duration_seconds=0.12,
            title_settle_duration_seconds=0.10,
            title_start_offset_px=104,
            title_overshoot_px=12,
            keyword_font_size=34,
            keyword_side_y=350,
            keyword_bottom_y=1392,
            keyword_border_width=2,
            keyword_shadow_y=3,
            keyword_alpha_delay_seconds=0.05,
            keyword_alpha_duration_seconds=0.14,
            keyword_intro_duration_seconds=0.10,
            keyword_settle_duration_seconds=0.08,
            keyword_start_offset_px=28,
            keyword_overshoot_px=5,
        )
    if story_plan.pace_role == "proof":
        return StoryOverlayRoleProfile(
            pace_role="proof",
            accent_color="0x8CE99A",
            title_center_font_size=84,
            title_top_font_size=62,
            title_center_y_offset=104,
            title_top_y=148,
            title_line_spacing=8,
            title_border_width=2,
            title_shadow_y=4,
            title_shadow_alpha=0.62,
            title_alpha_delay_seconds=0.04,
            title_alpha_duration_seconds=0.18,
            title_intro_duration_seconds=0.16,
            title_settle_duration_seconds=0.10,
            title_start_offset_px=48,
            title_overshoot_px=4,
            keyword_font_size=32,
            keyword_side_y=348,
            keyword_bottom_y=1400,
            keyword_border_width=2,
            keyword_shadow_y=3,
            keyword_alpha_delay_seconds=0.08,
            keyword_alpha_duration_seconds=0.16,
            keyword_intro_duration_seconds=0.14,
            keyword_settle_duration_seconds=0.08,
            keyword_start_offset_px=18,
            keyword_overshoot_px=3,
        )
    if story_plan.pace_role == "cta":
        return StoryOverlayRoleProfile(
            pace_role="cta",
            accent_color="0xA8D4FF",
            title_center_font_size=78,
            title_top_font_size=58,
            title_center_y_offset=110,
            title_top_y=164,
            title_line_spacing=8,
            title_border_width=2,
            title_shadow_y=3,
            title_shadow_alpha=0.54,
            title_alpha_delay_seconds=0.10,
            title_alpha_duration_seconds=0.14,
            title_intro_duration_seconds=0.14,
            title_settle_duration_seconds=0.08,
            title_start_offset_px=24,
            title_overshoot_px=2,
            keyword_font_size=32,
            keyword_side_y=354,
            keyword_bottom_y=1368,
            keyword_border_width=2,
            keyword_shadow_y=2,
            keyword_alpha_delay_seconds=0.12,
            keyword_alpha_duration_seconds=0.14,
            keyword_intro_duration_seconds=0.12,
            keyword_settle_duration_seconds=0.06,
            keyword_start_offset_px=22,
            keyword_overshoot_px=2,
        )
    return StoryOverlayRoleProfile(
        pace_role="explain",
        accent_color="0xA8D4FF",
        title_center_font_size=82,
        title_top_font_size=60,
        title_center_y_offset=106,
        title_top_y=160,
        title_line_spacing=8,
        title_border_width=2,
        title_shadow_y=4,
        title_shadow_alpha=0.60,
        title_alpha_delay_seconds=0.05,
        title_alpha_duration_seconds=0.18,
        title_intro_duration_seconds=0.15,
        title_settle_duration_seconds=0.09,
        title_start_offset_px=36,
        title_overshoot_px=3,
        keyword_font_size=32,
        keyword_side_y=352,
        keyword_bottom_y=1398,
        keyword_border_width=2,
        keyword_shadow_y=3,
        keyword_alpha_delay_seconds=0.10,
        keyword_alpha_duration_seconds=0.16,
        keyword_intro_duration_seconds=0.13,
        keyword_settle_duration_seconds=0.08,
        keyword_start_offset_px=18,
        keyword_overshoot_px=2,
    )


def _build_story_alpha_expr(*, delay_seconds: float, duration_seconds: float) -> str:
    safe_delay = max(0.0, delay_seconds)
    safe_duration = max(0.06, duration_seconds)
    end_seconds = safe_delay + safe_duration
    return (
        f"if(lt(t,{safe_delay:.3f}),0,"
        f"if(lt(t,{end_seconds:.3f}),(t-{safe_delay:.3f})/{safe_duration:.3f},1))"
    )


def _build_story_pop_y_expr(
    *,
    final_y_expr: str,
    start_offset_px: int,
    overshoot_px: int,
    intro_duration_seconds: float,
    settle_duration_seconds: float,
) -> str:
    safe_intro = max(0.08, intro_duration_seconds)
    safe_settle = max(0.05, settle_duration_seconds)
    settle_end = safe_intro + safe_settle
    total_travel = start_offset_px + overshoot_px
    return (
        f"if(lt(t,{safe_intro:.3f}),"
        f"({final_y_expr})+{start_offset_px}-({total_travel})*(t/{safe_intro:.3f}),"
        f"if(lt(t,{settle_end:.3f}),"
        f"({final_y_expr})-{overshoot_px}+{overshoot_px}*((t-{safe_intro:.3f})/{safe_settle:.3f}),"
        f"({final_y_expr})))"
    )


def _build_story_impact_zoom_filter(*, duration_seconds: float, pace_role: str) -> str:
    peak_zoom, settle_zoom = {
        "hook": (1.065, 1.018),
        "proof": (1.040, 1.010),
        "cta": (1.026, 1.000),
        "explain": (1.034, 1.008),
    }.get(pace_role, (1.034, 1.008))
    impact_window = min(0.20, max(0.09, duration_seconds / 5.2))
    settle_end = min(duration_seconds, max(0.36, impact_window + (0.24 if pace_role == "hook" else 0.18)))
    settle_duration = max(0.05, settle_end - impact_window)
    zoom_expr = (
        f"if(lt(t,{impact_window:.3f}),"
        f"1+{peak_zoom - 1.0:.3f}*(t/{impact_window:.3f}),"
        f"if(lt(t,{settle_end:.3f}),"
        f"{peak_zoom:.3f}-{peak_zoom - settle_zoom:.3f}*((t-{impact_window:.3f})/{settle_duration:.3f}),"
        f"{settle_zoom:.3f}))"
    )
    return _build_scale_crop_filter(zoom_expr=zoom_expr.replace(",", r"\,"))


def _resolve_story_text_font_name(job: VideoJob) -> str:
    story_resolution = resolve_runtime_story_font(job)
    if story_resolution.resolved_name:
        return story_resolution.resolved_name
    for key in (
        "story_font_resolved",
        "story_text_font_name",
        "overlay_font_name",
        "edit_caption_font_resolved",
        "edit_caption_font_name",
        "subtitle_font_resolved",
        "subtitle_font_name",
    ):
        value = job.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_STORY_FONT_NAME


def _resolve_story_symbol_font_name(job: VideoJob) -> str:
    resolution = resolve_runtime_story_symbol_font(job)
    return resolution.resolved_name


def _sanitize_story_drawtext_text(value: str) -> str:
    return " ".join(value.replace("'", " ").replace('"', " ").split())


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
        parts.append(
            f"[{base_label}][{blur_overlay_label}]overlay=shortest=1:format=auto:eof_action=pass[{output_label}]"
        )
        return output_label

    return input_label


def _build_progress_expr(duration_seconds: float) -> str:
    safe_duration = max(0.05, duration_seconds)
    return f"min(1,max(0,t/{safe_duration:.3f}))"


def _resolve_transition_window(duration_seconds: float, *, maximum: float) -> float:
    return _clamp(maximum, 0.05, min(maximum, duration_seconds / 3.0))


def _build_scene_caption_ass(
    *,
    job: VideoJob,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    settings: CaptionSettings,
    voice_word_timing: list[VoiceWordTiming],
) -> tuple[str, dict[str, object]]:
    style = _resolve_caption_style_profile(job, scene=scene, plan=plan, settings=settings)
    chunks, timing_source = _plan_scene_caption_chunks(
        scene=scene,
        plan=plan,
        settings=settings,
        style=style,
        voice_word_timing=voice_word_timing,
    )
    document = _render_scene_caption_document(scene=scene, style=style, chunks=chunks)
    debug = {
        "scene_id": scene.scene_id,
        "pace_role": plan.pace_role,
        "text_style": style.name,
        "caption_mode": CAPTION_RENDERER_VERSION,
        "timing_source": timing_source,
        "chunks": [chunk.to_metadata() for chunk in chunks],
    }
    return document, debug


def _build_fallback_scene_caption_ass(
    *,
    job: VideoJob,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    settings: CaptionSettings,
    fallback_reason: str,
) -> tuple[str, dict[str, object]]:
    style = _resolve_caption_style_profile(job, scene=scene, plan=plan, settings=settings)
    chunks = _build_legacy_caption_chunks(scene=scene, plan=plan, style=style)
    document = _render_scene_caption_document(scene=scene, style=style, chunks=chunks)
    debug = {
        "scene_id": scene.scene_id,
        "pace_role": plan.pace_role,
        "text_style": style.name,
        "caption_mode": "legacy_ass_fallback",
        "timing_source": "legacy_scene_cues",
        "fallback_reason": fallback_reason,
        "chunks": [chunk.to_metadata() for chunk in chunks],
    }
    return document, debug


def _render_scene_caption_document(
    *,
    scene: SceneSegment,
    style: CaptionStyleProfile,
    chunks: list[CaptionChunk],
) -> str:
    event_lines: list[str] = []
    for chunk in chunks:
        start_seconds = max(0.0, chunk.start_seconds - scene.start_seconds)
        end_seconds = max(start_seconds + 0.05, chunk.end_seconds - scene.start_seconds)
        event_lines.append(
            "Dialogue: 0,"
            f"{_format_ass_timestamp(start_seconds)},{_format_ass_timestamp(end_seconds)},"
            f"Default,,0,0,0,,{_format_caption_chunk_text(chunk, style=style)}"
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
            f"Style: Default,{_build_caption_style(style)}",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *event_lines,
            "",
        ]
    )
    return header


def _plan_scene_caption_chunks(
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    settings: CaptionSettings,
    style: CaptionStyleProfile,
    voice_word_timing: list[VoiceWordTiming],
) -> tuple[list[CaptionChunk], str]:
    tokens, timing_source = _build_scene_caption_tokens(scene=scene, voice_word_timing=voice_word_timing)
    if not tokens:
        return _build_legacy_caption_chunks(scene=scene, plan=plan, style=style), "scene_duration"

    min_duration_seconds = settings.min_duration_ms / 1000.0
    max_duration_seconds = min(settings.max_duration_ms, style.max_duration_ms) / 1000.0
    max_words = max(2, min(settings.max_words_per_chunk, style.max_words))
    wrap_chars = max(12, min(settings.max_chars_per_line, style.wrap_chars))

    token_groups: list[list[CaptionToken]] = []
    current_group: list[CaptionToken] = []
    for token in tokens:
        candidate_group = [*current_group, token]
        if current_group and _should_split_caption_group(
            current_group=current_group,
            candidate_group=candidate_group,
            pace_role=plan.pace_role,
            min_duration_seconds=min_duration_seconds,
            max_duration_seconds=max_duration_seconds,
            max_words=max_words,
            wrap_chars=wrap_chars,
            max_lines=settings.max_lines,
        ):
            token_groups.append(current_group)
            current_group = [token]
        else:
            current_group = candidate_group

        current_duration = current_group[-1].end_seconds - current_group[0].start_seconds
        if token.break_after and current_duration >= min_duration_seconds * 0.72:
            token_groups.append(current_group)
            current_group = []

    if current_group:
        token_groups.append(current_group)

    token_groups = _rebalance_caption_groups(
        token_groups,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
        max_words=max_words,
        wrap_chars=wrap_chars,
        max_lines=settings.max_lines,
    )
    if not token_groups:
        return _build_legacy_caption_chunks(scene=scene, plan=plan, style=style), "scene_duration"

    chunks = [
        _finalize_caption_group(
            group,
            scene=scene,
            plan=plan,
            wrap_chars=wrap_chars,
            max_lines=settings.max_lines,
            timing_source=timing_source,
        )
        for group in token_groups
    ]
    return chunks, timing_source


def _build_scene_caption_tokens(
    *,
    scene: SceneSegment,
    voice_word_timing: list[VoiceWordTiming],
) -> tuple[list[CaptionToken], str]:
    if scene.cue_timeline:
        scene_voice_words = [
            word
            for word in voice_word_timing
            if word.end_seconds > scene.start_seconds - 0.04
            and word.start_seconds < scene.end_seconds + 0.04
        ]
        timing_sources_seen: set[str] = set()
        voice_cursor = 0
        tokens: list[CaptionToken] = []
        for cue in scene.cue_timeline:
            cue_words = _split_caption_words(cue.text)
            if not cue_words:
                continue
            cue_voice_words, voice_cursor = _consume_cue_voice_words(
                scene_voice_words,
                start_index=voice_cursor,
                cue_start_seconds=cue.start_seconds,
                cue_end_seconds=cue.end_seconds,
            )
            if cue_voice_words:
                cue_tokens = _map_cue_words_to_voice_timing(
                    cue_words,
                    cue_voice_words,
                    cue_start_seconds=cue.start_seconds,
                    cue_end_seconds=cue.end_seconds,
                )
                cue_timing_source = "word_boundaries"
            else:
                cue_tokens = _build_proportional_caption_tokens(
                    cue_words,
                    cue_start_seconds=cue.start_seconds,
                    cue_end_seconds=cue.end_seconds,
                )
                cue_timing_source = cue.timing_source or "subtitle_cues"
            if cue_tokens:
                cue_tokens[-1] = CaptionToken(
                    text=cue_tokens[-1].text,
                    normalized=cue_tokens[-1].normalized,
                    start_seconds=cue_tokens[-1].start_seconds,
                    end_seconds=cue_tokens[-1].end_seconds,
                    break_after=True,
                )
                tokens.extend(cue_tokens)
                timing_sources_seen.add(cue_timing_source)
        if tokens:
            if len(timing_sources_seen) == 1:
                return tokens, next(iter(timing_sources_seen))
            if timing_sources_seen:
                return tokens, "mixed"
            return tokens, "subtitle_cues"

    fallback_text = scene.highlight_text or scene.source_text or scene.title_text
    fallback_words = _split_caption_words(fallback_text)
    if not fallback_words:
        return [], "scene_duration"
    fallback_tokens = _build_proportional_caption_tokens(
        fallback_words,
        cue_start_seconds=scene.start_seconds,
        cue_end_seconds=scene.end_seconds,
    )
    if fallback_tokens:
        fallback_tokens[-1] = CaptionToken(
            text=fallback_tokens[-1].text,
            normalized=fallback_tokens[-1].normalized,
            start_seconds=fallback_tokens[-1].start_seconds,
            end_seconds=fallback_tokens[-1].end_seconds,
            break_after=True,
        )
    return fallback_tokens, "scene_duration"


def _build_legacy_caption_chunks(
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    style: CaptionStyleProfile,
) -> list[CaptionChunk]:
    chunks: list[CaptionChunk] = []
    if scene.cue_timeline:
        for cue in scene.cue_timeline:
            cue_words = _split_caption_words(cue.text)
            if not cue_words:
                continue
            token_group = _build_proportional_caption_tokens(
                cue_words,
                cue_start_seconds=cue.start_seconds,
                cue_end_seconds=cue.end_seconds,
            )
            if token_group:
                chunks.append(
                    _finalize_caption_group(
                        token_group,
                        scene=scene,
                        plan=plan,
                        wrap_chars=style.wrap_chars,
                        max_lines=DEFAULT_EDIT_CAPTION_MAX_LINES,
                        timing_source="legacy_scene_cues",
                    )
                )

    if chunks:
        return chunks

    fallback_text = scene.highlight_text or scene.source_text or scene.title_text
    fallback_words = _split_caption_words(fallback_text)
    if not fallback_words:
        fallback_words = ["caption"]
    token_group = _build_proportional_caption_tokens(
        fallback_words,
        cue_start_seconds=scene.start_seconds,
        cue_end_seconds=min(scene.end_seconds, scene.start_seconds + 1.8),
    )
    return [
        _finalize_caption_group(
            token_group,
            scene=scene,
            plan=plan,
            wrap_chars=style.wrap_chars,
            max_lines=DEFAULT_EDIT_CAPTION_MAX_LINES,
            timing_source="legacy_scene_cues",
        )
    ]


def _should_split_caption_group(
    *,
    current_group: list[CaptionToken],
    candidate_group: list[CaptionToken],
    pace_role: str,
    min_duration_seconds: float,
    max_duration_seconds: float,
    max_words: int,
    wrap_chars: int,
    max_lines: int,
) -> bool:
    if len(candidate_group) > max_words:
        return True
    if _group_duration_seconds(candidate_group) > max_duration_seconds and _group_duration_seconds(current_group) >= min_duration_seconds * 0.66:
        return True
    _, overflow = _wrap_caption_words(
        [token.text for token in candidate_group],
        width=wrap_chars,
        max_lines=max_lines,
    )
    if overflow:
        return True

    pause_seconds = max(0.0, candidate_group[-1].start_seconds - current_group[-1].end_seconds)
    if pause_seconds >= 0.16 and _group_duration_seconds(current_group) >= min_duration_seconds * 0.75:
        return True
    if pace_role == "hook" and len(candidate_group) >= max_words and _group_duration_seconds(current_group) >= min_duration_seconds * 0.6:
        return True
    return False


def _rebalance_caption_groups(
    groups: list[list[CaptionToken]],
    *,
    min_duration_seconds: float,
    max_duration_seconds: float,
    max_words: int,
    wrap_chars: int,
    max_lines: int,
) -> list[list[CaptionToken]]:
    if len(groups) <= 1:
        return groups

    merged_groups: list[list[CaptionToken]] = [groups[0]]
    for group in groups[1:]:
        if merged_groups and _group_duration_seconds(group) < min_duration_seconds * 0.72:
            candidate = [*merged_groups[-1], *group]
            if _caption_group_can_fit(
                candidate,
                max_duration_seconds=max_duration_seconds,
                max_words=max_words + 1,
                wrap_chars=wrap_chars,
                max_lines=max_lines,
            ):
                merged_groups[-1] = candidate
                continue
        merged_groups.append(group)

    if len(merged_groups) >= 2 and _group_duration_seconds(merged_groups[-1]) < min_duration_seconds * 0.72:
        candidate = [*merged_groups[-2], *merged_groups[-1]]
        if _caption_group_can_fit(
            candidate,
            max_duration_seconds=max_duration_seconds,
            max_words=max_words + 1,
            wrap_chars=wrap_chars,
            max_lines=max_lines,
        ):
            merged_groups[-2] = candidate
            merged_groups.pop()
    return merged_groups


def _caption_group_can_fit(
    group: list[CaptionToken],
    *,
    max_duration_seconds: float,
    max_words: int,
    wrap_chars: int,
    max_lines: int,
) -> bool:
    if len(group) > max_words:
        return False
    if _group_duration_seconds(group) > max_duration_seconds * 1.12:
        return False
    _, overflow = _wrap_caption_words([token.text for token in group], width=wrap_chars, max_lines=max_lines)
    return not overflow


def _consume_cue_voice_words(
    voice_words: list[VoiceWordTiming],
    *,
    start_index: int,
    cue_start_seconds: float,
    cue_end_seconds: float,
) -> tuple[list[VoiceWordTiming], int]:
    cursor = start_index
    while cursor < len(voice_words) and voice_words[cursor].end_seconds <= cue_start_seconds - 0.03:
        cursor += 1

    probe = cursor
    consumed: list[VoiceWordTiming] = []
    while probe < len(voice_words) and voice_words[probe].start_seconds < cue_end_seconds + 0.06:
        consumed.append(voice_words[probe])
        probe += 1

    if not consumed:
        return [], cursor
    return consumed, probe


def _map_cue_words_to_voice_timing(
    cue_words: list[str],
    voice_words: list[VoiceWordTiming],
    *,
    cue_start_seconds: float,
    cue_end_seconds: float,
) -> list[CaptionToken]:
    if len(voice_words) < len(cue_words):
        return _build_proportional_caption_tokens(
            cue_words,
            cue_start_seconds=cue_start_seconds,
            cue_end_seconds=cue_end_seconds,
        )

    tokens: list[CaptionToken] = []
    voice_cursor = 0
    for index, word in enumerate(cue_words):
        remaining_words = len(cue_words) - index
        remaining_voice = len(voice_words) - voice_cursor
        if index == len(cue_words) - 1:
            assigned = voice_words[voice_cursor:]
        else:
            assigned_count = max(1, round(remaining_voice / remaining_words))
            assigned_count = min(assigned_count, max(1, remaining_voice - (remaining_words - 1)))
            assigned = voice_words[voice_cursor : voice_cursor + assigned_count]
            voice_cursor += assigned_count
        normalized = _normalize_caption_token(word)
        tokens.append(
            CaptionToken(
                text=word,
                normalized=normalized,
                start_seconds=assigned[0].start_seconds,
                end_seconds=assigned[-1].end_seconds,
            )
        )
    return tokens


def _build_proportional_caption_tokens(
    words: list[str],
    *,
    cue_start_seconds: float,
    cue_end_seconds: float,
) -> list[CaptionToken]:
    if not words:
        return []
    total_duration_seconds = max(0.05, cue_end_seconds - cue_start_seconds)
    weights = [max(1, len(_normalize_caption_token(word)) or len(word)) for word in words]
    total_weight = sum(weights)
    elapsed = cue_start_seconds
    tokens: list[CaptionToken] = []
    for index, (word, weight) in enumerate(zip(words, weights, strict=True)):
        duration_seconds = total_duration_seconds * (weight / total_weight)
        end_seconds = cue_end_seconds if index == len(words) - 1 else elapsed + duration_seconds
        tokens.append(
            CaptionToken(
                text=word,
                normalized=_normalize_caption_token(word),
                start_seconds=round(elapsed, 3),
                end_seconds=round(max(elapsed + 0.01, end_seconds), 3),
            )
        )
        elapsed = end_seconds
    return tokens


def _finalize_caption_group(
    group: list[CaptionToken],
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    wrap_chars: int,
    max_lines: int,
    timing_source: str | None = None,
) -> CaptionChunk:
    lines, _ = _wrap_caption_words([token.text for token in group], width=wrap_chars, max_lines=max_lines)
    caption_text = "\n".join(lines) if lines else " ".join(token.text for token in group)
    emphasis_tokens, skipped_reason = compute_caption_emphasis(caption_text, scene=scene, plan=plan)
    return CaptionChunk(
        text=caption_text,
        start_seconds=round(group[0].start_seconds, 3),
        end_seconds=round(group[-1].end_seconds, 3),
        emphasis_tokens=tuple(emphasis_tokens),
        skipped_reason=skipped_reason,
        timing_source=timing_source or "subtitle_cues",
        line_count=max(1, len(lines) if lines else 1),
        word_count=len(group),
    )


def _resolve_caption_style_profile(
    job: VideoJob,
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
    settings: CaptionSettings,
) -> CaptionStyleProfile:
    font_name = _resolve_runtime_edit_caption_font(job).resolved_name
    style_name = _normalize_caption_text_style(plan.text_style, pace_role=plan.pace_role)
    side_margin = settings.safe_side_margin
    bottom_margin = settings.safe_bottom_margin

    if style_name == "hook_focus":
        return CaptionStyleProfile(
            name=style_name,
            font_name=font_name,
            font_size=32,
            primary_color="&H00FFFFFF",
            highlight_color="&H0027B8FF",
            outline_color="&H62000000",
            back_color="&H24000000",
            bold=1,
            outline=2,
            shadow=0,
            spacing=0.18,
            alignment=2,
            border_style=1,
            margin_l=side_margin,
            margin_r=side_margin,
            margin_v=bottom_margin + 44,
            wrap_chars=17,
            max_words=4,
            max_duration_ms=1800,
        )
    if style_name == "proof_clean":
        return CaptionStyleProfile(
            name=style_name,
            font_name=font_name,
            font_size=26,
            primary_color="&H00F4F7F8",
            highlight_color="&H0067E3B2",
            outline_color="&H60000000",
            back_color="&H14000000",
            bold=1,
            outline=2,
            shadow=0,
            spacing=0.12,
            alignment=2,
            border_style=1,
            margin_l=side_margin + 16,
            margin_r=side_margin + 16,
            margin_v=bottom_margin + 12,
            wrap_chars=20,
            max_words=6,
            max_duration_ms=2300,
        )
    if style_name == "cta_clean":
        return CaptionStyleProfile(
            name=style_name,
            font_name=font_name,
            font_size=30,
            primary_color="&H00FFFFFF",
            highlight_color="&H00A2DBFF",
            outline_color="&H62000000",
            back_color="&H22000000",
            bold=1,
            outline=2,
            shadow=0,
            spacing=0.12,
            alignment=2,
            border_style=1,
            margin_l=side_margin,
            margin_r=side_margin,
            margin_v=bottom_margin + (70 if plan.overlay_style == "cta_band" or scene.role == "cta" else 30),
            wrap_chars=17,
            max_words=5,
            max_duration_ms=2000,
        )
    return CaptionStyleProfile(
        name="explain_readable",
        font_name=font_name,
        font_size=28,
        primary_color="&H00FFFFFF",
        highlight_color="&H0079CCFF",
        outline_color="&H62000000",
        back_color="&H18000000",
        bold=1,
        outline=2,
        shadow=0,
        spacing=0.12,
        alignment=2,
        border_style=1,
        margin_l=side_margin + 8,
        margin_r=side_margin + 8,
        margin_v=bottom_margin + 24,
        wrap_chars=18,
        max_words=6,
        max_duration_ms=2400,
    )


def _build_caption_style(style: CaptionStyleProfile) -> str:
    return ",".join(
        [
            style.font_name,
            str(style.font_size),
            style.primary_color,
            style.highlight_color,
            style.outline_color,
            style.back_color,
            str(style.bold),
            "0",
            "0",
            "0",
            "100",
            "100",
            str(style.spacing),
            "0",
            str(style.border_style),
            str(style.outline),
            str(style.shadow),
            str(style.alignment),
            str(style.margin_l),
            str(style.margin_r),
            str(style.margin_v),
            "1",
        ]
    )


def _format_caption_chunk_text(chunk: CaptionChunk, *, style: CaptionStyleProfile) -> str:
    wrapped_text = chunk.text
    if chunk.emphasis_tokens:
        wrapped_text = _apply_caption_emphasis(wrapped_text, chunk.emphasis_tokens, style=style)
    return wrapped_text.replace("\n", r"\N")


def _resolve_caption_settings(job: VideoJob) -> CaptionSettings:
    legacy_min_duration_ms = int(
        round(
            _read_float_metadata(
                job,
                "subtitle_min_duration_seconds",
                DEFAULT_EDIT_CAPTION_MIN_DURATION_MS / 1000.0,
                minimum=0.2,
                maximum=2.5,
            )
            * 1000
        )
    )
    min_duration_ms = _read_int_metadata(
        job,
        "edit_caption_min_duration_ms",
        legacy_min_duration_ms,
        minimum=250,
        maximum=1800,
    )
    legacy_max_duration_ms = int(
        round(
            _read_float_metadata(
                job,
                "subtitle_max_duration_seconds",
                DEFAULT_EDIT_CAPTION_MAX_DURATION_MS / 1000.0,
                minimum=min_duration_ms / 1000.0,
                maximum=4.0,
            )
            * 1000
        )
    )
    max_duration_ms = _read_int_metadata(
        job,
        "edit_caption_max_duration_ms",
        legacy_max_duration_ms,
        minimum=min_duration_ms,
        maximum=3200,
    )
    return CaptionSettings(
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        max_words_per_chunk=_read_int_metadata(
            job,
            "edit_caption_max_words_per_chunk",
            _read_int_metadata(
                job,
                "subtitle_max_words",
                DEFAULT_EDIT_CAPTION_MAX_WORDS_PER_CHUNK,
                minimum=2,
                maximum=10,
            ),
            minimum=2,
            maximum=10,
        ),
        max_chars_per_line=_read_int_metadata(
            job,
            "edit_caption_max_chars_per_line",
            _read_int_metadata(
                job,
                "subtitle_max_line_chars",
                DEFAULT_EDIT_CAPTION_MAX_CHARS_PER_LINE,
                minimum=12,
                maximum=26,
            ),
            minimum=12,
            maximum=26,
        ),
        max_lines=_read_int_metadata(
            job,
            "edit_caption_max_lines",
            _read_int_metadata(
                job,
                "subtitle_max_lines",
                DEFAULT_EDIT_CAPTION_MAX_LINES,
                minimum=1,
                maximum=2,
            ),
            minimum=1,
            maximum=2,
        ),
        safe_bottom_margin=_read_int_metadata(
            job,
            "edit_caption_safe_bottom_margin",
            _read_int_metadata(
                job,
                "subtitle_margin_v",
                DEFAULT_EDIT_CAPTION_SAFE_BOTTOM_MARGIN,
                minimum=140,
                maximum=420,
            ),
            minimum=140,
            maximum=420,
        ),
        safe_side_margin=_read_int_metadata(
            job,
            "edit_caption_safe_side_margin",
            DEFAULT_EDIT_CAPTION_SAFE_SIDE_MARGIN,
            minimum=40,
            maximum=180,
        ),
    )


def _load_voice_word_timing(job: VideoJob) -> list[VoiceWordTiming]:
    raw_timing = job.metadata.get("voice_word_timing")
    if isinstance(raw_timing, list):
        parsed: list[VoiceWordTiming] = []
        for index, item in enumerate(raw_timing, start=1):
            if not isinstance(item, Mapping):
                continue
            start_seconds = item.get("start_seconds")
            end_seconds = item.get("end_seconds")
            if not isinstance(start_seconds, (int, float)) or not isinstance(end_seconds, (int, float)):
                continue
            parsed.append(
                VoiceWordTiming(
                    index=max(1, int(item.get("index", index))),
                    text=str(item.get("text", "")).strip(),
                    start_seconds=max(0.0, float(start_seconds)),
                    end_seconds=max(float(start_seconds), float(end_seconds)),
                )
            )
        if parsed:
            return parsed
    return []


def select_caption_keywords(scene: SceneSegment) -> list[str]:
    raw_tokens: list[str] = []
    raw_tokens.extend(scene.keywords)
    raw_tokens.extend(_split_caption_words(scene.highlight_text))
    raw_tokens.extend(_split_caption_words(scene.title_text))
    keywords: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        normalized = _normalize_caption_token(token)
        if not normalized or normalized in seen:
            continue
        if normalized in CAPTION_STOPWORDS or normalized in CAPTION_WEAK_TOKENS:
            continue
        if len(normalized) < 3 and not any(character.isdigit() for character in normalized):
            continue
        seen.add(normalized)
        keywords.append(normalized)
    return keywords


def extract_caption_emphasis_tokens(
    text: str,
    *,
    keywords: list[str],
    pace_role: str,
) -> list[dict[str, int | str]]:
    words = _split_caption_words(text)
    keyword_set = set(keywords)
    candidates: list[dict[str, int | str]] = []
    for index, word in enumerate(words):
        normalized = _normalize_caption_token(word)
        if not normalized:
            continue
        if normalized in CAPTION_STOPWORDS or normalized in CAPTION_WEAK_TOKENS:
            continue

        score = 0
        if _is_numeric_caption_token(normalized):
            score += 140
        if normalized in keyword_set:
            score += 36
        if normalized in UI_REVEAL_TOKENS or normalized in {"airtable", "capcut", "chatgpt", "hubspot", "make", "make.com", "n8n", "notion", "openai", "slack", "zapier"}:
            score += 110
        if normalized in CAPTION_PROOF_TOKENS or normalized in PROOF_REVEAL_TOKENS:
            score += 56
        if normalized in CAPTION_RESULT_TOKENS:
            score += 42
        if normalized in CAPTION_OPPOSITION_TOKENS:
            score += 40
        if pace_role == "cta" and normalized in CAPTION_ACTION_TOKENS:
            score += 72
        if index > 0 and _is_numeric_caption_token(_normalize_caption_token(words[index - 1])) and normalized in CAPTION_DURATION_UNITS:
            score += 32
        if len(normalized) >= 6:
            score += 10
        if score <= 0:
            continue
        candidates.append(
            {
                "token": word,
                "normalized": normalized,
                "score": score,
                "position": index,
            }
        )
    return candidates


def compute_caption_emphasis(
    text: str,
    *,
    scene: SceneSegment,
    plan: SceneRenderPlan,
) -> tuple[list[str], str | None]:
    candidates = extract_caption_emphasis_tokens(
        text,
        keywords=select_caption_keywords(scene),
        pace_role=plan.pace_role,
    )
    if not candidates:
        return [], "no_candidate"

    word_count = len(_split_caption_words(text))
    max_tokens = 1 if word_count <= 4 else 2 if word_count <= 7 else 3
    selected: list[dict[str, int | str]] = []
    used_tokens: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: (-int(item["score"]), int(item["position"]))):
        normalized = str(candidate["normalized"])
        score = int(candidate["score"])
        if normalized in used_tokens:
            continue
        if score < 48 and not _is_numeric_caption_token(normalized):
            continue
        used_tokens.add(normalized)
        selected.append(candidate)
        if len(selected) >= max_tokens:
            break
    if not selected:
        return [], "below_threshold"
    selected.sort(key=lambda item: int(item["position"]))
    return [str(item["token"]) for item in selected], None


def _sanitize_ass_text(value: str) -> str:
    return " ".join(value.replace("\\", "/").replace("{", "(").replace("}", ")").split()).strip()


def _split_caption_words(text: str) -> list[str]:
    return CAPTION_TOKEN_RE.findall(_sanitize_ass_text(text))


def _normalize_caption_token(token: str) -> str:
    return token.strip(".,!?;:()[]{}\"'").lower()


def _is_numeric_caption_token(token: str) -> bool:
    return any(character.isdigit() for character in token)


def _wrap_caption_words(words: list[str], *, width: int, max_lines: int) -> tuple[list[str], bool]:
    if not words:
        return [], False

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
        if len(lines) >= max_lines:
            return lines, True

    if current_line:
        lines.append(" ".join(current_line))
    return lines[:max_lines], len(lines) > max_lines


def _apply_caption_emphasis(
    text: str,
    emphasis_tokens: tuple[str, ...],
    *,
    style: CaptionStyleProfile,
) -> str:
    emphasized = text
    for token in emphasis_tokens:
        pattern = re.compile(rf"(?<![\w%])({re.escape(token)})(?![\w%])", flags=re.IGNORECASE)

        def _replace(match: re.Match[str]) -> str:
            return r"{\b1\1c" + style.highlight_color + r"&}" + match.group(1) + r"{\r}"

        emphasized, replaced = pattern.subn(_replace, emphasized, count=1)
        if replaced <= 0:
            continue
    return emphasized


def _normalize_caption_text_style(text_style: str, *, pace_role: str) -> str:
    normalized = text_style.strip().lower()
    if normalized in {"hook_focus", "proof_clean", "explain_readable", "cta_clean"}:
        return normalized
    if normalized == "standard_subtitles":
        return "cta_clean" if pace_role == "cta" else "explain_readable"
    if normalized == "keyword_emphasis":
        return "explain_readable"
    return "explain_readable"


def _group_duration_seconds(group: list[CaptionToken]) -> float:
    if not group:
        return 0.0
    return max(0.01, group[-1].end_seconds - group[0].start_seconds)


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
    scene_visual: SceneVisualAsset | None,
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
            _build_scene_video_filter(
                job=job,
                scene=scene,
                scene_visual=scene_visual,
                duration_seconds=duration_seconds,
                source_is_image=source_is_image,
            ),
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
    *,
    job: VideoJob,
    scene: SceneSegment,
    scene_visual: SceneVisualAsset | None,
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
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,fps={TARGET_FPS}"
        )

    story_filter = _build_scene_story_filter_chain(
        job=job,
        story_plan=_build_scene_story_overlay_plan(scene=scene, scene_visual=scene_visual, plan=None),
    )
    needs_rgba = bool(story_filter) or (
        scene.transition_effect == "fade" and scene.transition_duration_seconds > 0
    )
    filter_chain = f"{base_filter},format=rgba" if needs_rgba else base_filter
    if story_filter:
        filter_chain = f"{filter_chain},{story_filter}"

    if scene.transition_effect != "fade" or scene.transition_duration_seconds <= 0:
        return f"{filter_chain},format=yuv420p" if needs_rgba else filter_chain

    fade_duration = _clamp(scene.transition_duration_seconds, 0.08, min(0.35, duration_seconds / 3.0))
    fade_out_start = max(0.0, duration_seconds - fade_duration)
    return (
        f"{filter_chain},"
        f"fade=t=in:st=0:d={fade_duration:.3f},"
        f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f},"
        "format=yuv420p"
    )


def _build_zoom_filter(duration_seconds: float) -> str:
    frame_count = max(2, int(round(duration_seconds * TARGET_FPS)))
    return (
        f"scale={int(TARGET_WIDTH * DEFAULT_SCENE_MOTION_SCALE)}:"
        f"{int(TARGET_HEIGHT * DEFAULT_SCENE_MOTION_SCALE)}:flags=lanczos:force_original_aspect_ratio=increase,"
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
        f"scale={scaled_width}:{scaled_height}:flags=lanczos:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT}:x='{x_expr}':y='{y_expr}',"
        f"setsar=1,fps={TARGET_FPS}"
    )


def _build_audio_mix_command(
    *,
    job: VideoJob,
    voice_audio_path: Path | None,
    audio_layers: list[AudioLayerSpec] | None,
    output_path: Path,
) -> list[str]:
    layers = list(audio_layers or [])
    command: list[str] = ["ffmpeg", "-hide_banner", "-y"]
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

    for layer in layers:
        if layer.loop:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(layer.path)])

    if layers:
        command.extend(
            [
                "-filter_complex",
                _build_audio_mix_filter_complex(
                    job=job,
                    has_voice_audio=voice_audio_path is not None,
                    audio_layers=layers,
                ),
                "-map",
                "[aout]",
            ]
        )
    else:
        audio_filter = _build_audio_filter(job, has_voice_audio=voice_audio_path is not None)
        if audio_filter is not None:
            command.extend(["-af", audio_filter])
        command.extend(["-map", "0:a:0"])

    command.extend(
        [
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    )
    return command


def _build_prepared_audio_mux_command(
    *,
    job: VideoJob,
    source_video_path: Path,
    audio_path: Path | None,
    subtitles_path: Path | None,
    output_path: Path,
) -> list[str]:
    video_preset = _resolve_video_preset(job)
    video_crf = _resolve_video_crf(job)
    command: list[str] = ["ffmpeg", "-hide_banner", "-y", "-i", str(source_video_path)]

    if audio_path is not None:
        command.extend(["-i", str(audio_path)])
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        )

    video_filters = [f"setsar=1", f"fps={TARGET_FPS}"]
    if subtitles_path is not None:
        video_filters.append("format=rgba")
        video_filters.append(_build_subtitle_filter(job, subtitles_path))
        video_filters.append("format=yuv420p")

    command.extend(["-vf", ",".join(video_filters)])
    command.extend(["-map", "0:v:0", "-map", "1:a:0"])
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
    use_advanced_audio_mix = bool(layers)
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

    if use_advanced_audio_mix:
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
            filter_chain.append("format=rgba")
            filter_chain.append(_build_subtitle_filter(job, subtitles_path))
            filter_chain.append("format=yuv420p")

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


def _build_audio_mix_filter_complex(
    *,
    job: VideoJob,
    has_voice_audio: bool,
    audio_layers: list[AudioLayerSpec],
) -> str:
    parts: list[str] = []
    voice_label = "voice_mix"
    if has_voice_audio:
        audio_filter = _build_audio_filter(job, has_voice_audio=True)
        if audio_filter is None:
            parts.append(f"[0:a]anull[{voice_label}]")
        else:
            parts.append(f"[0:a]{audio_filter}[{voice_label}]")
    else:
        parts.append(f"[0:a]anull[{voice_label}]")

    bgm_labels: list[str] = []
    sfx_labels: list[str] = []
    for input_index, layer in enumerate(audio_layers, start=1):
        layer_label = f"layer_{input_index}"
        parts.append(f"[{input_index}:a]{_build_audio_layer_filter(layer)}[{layer_label}]")
        if layer.kind == "bgm":
            bgm_labels.append(layer_label)
        else:
            sfx_labels.append(layer_label)

    mix_labels: list[str] = []
    voice_mix_label = voice_label
    voice_sidechain_label = voice_label
    if has_voice_audio and bgm_labels:
        voice_mix_label = "voice_main"
        voice_sidechain_label = "voice_sidechain"
        parts.append(f"[{voice_label}]asplit=2[{voice_mix_label}][{voice_sidechain_label}]")

    if has_voice_audio:
        mix_labels.append(f"[{voice_mix_label}]")

    bgm_bus_label = _append_bgm_bus(
        parts,
        job=job,
        bgm_labels=bgm_labels,
        voice_label=voice_sidechain_label,
        ducking_enabled=has_voice_audio and bool(bgm_labels),
    )
    if bgm_bus_label is not None:
        mix_labels.append(f"[{bgm_bus_label}]")

    mix_labels.extend(f"[{label}]" for label in sfx_labels)
    if not mix_labels:
        mix_labels.append(f"[{voice_mix_label}]")

    parts.append(_build_audio_mix_output(mix_labels))
    return ";".join(parts)


async def _resolve_audio_layers(
    *,
    job: VideoJob,
    manifest: SceneManifest,
    edit_manifest: EditManifest,
) -> ResolvedAudioMixPlan:
    roots = _resolve_edit_audio_roots(job)
    cache: dict[tuple[str, str], tuple[Path, ...]] = {}
    missing_profiles: list[str] = []
    missing_seen: set[str] = set()
    layers: list[AudioLayerSpec] = []
    sfx_events: list[AudioEventSpec] = []
    bgm_volume = _read_float_metadata(job, "edit_bgm_volume", DEFAULT_EDIT_BGM_VOLUME, minimum=0.0, maximum=0.6)
    sfx_volume = _read_float_metadata(job, "edit_sfx_volume", DEFAULT_EDIT_SFX_VOLUME, minimum=0.0, maximum=1.4)
    recent_window = _read_int_metadata(
        job,
        "edit_sfx_recent_window",
        DEFAULT_EDIT_SFX_RECENT_WINDOW,
        minimum=1,
        maximum=8,
    )
    sfx_enabled = _is_sfx_enabled(job)
    primary_bgm_profile = _resolve_primary_bgm_profile(manifest, edit_manifest)
    if primary_bgm_profile is not None:
        bgm_path = await _resolve_audio_profile_path(
            job=job,
            kind="bgm",
            profile=primary_bgm_profile,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
        )
        if bgm_path is not None:
            layers.append(
                AudioLayerSpec(
                    kind="bgm",
                    profile=primary_bgm_profile,
                    path=bgm_path,
                    start_seconds=0.0,
                    duration_seconds=max(0.3, float(manifest.total_duration_seconds)),
                    volume=bgm_volume,
                    loop=True,
                )
            )
            LOGGER.info(
                "job=%s audio_bgm profile=%s path=%s",
                job.job_id,
                primary_bgm_profile,
                bgm_path,
            )
        else:
            LOGGER.info(
                "job=%s audio_bgm profile=%s missing",
                job.job_id,
                primary_bgm_profile,
            )

    available_sfx_assets, available_sfx_asset_pool = await _resolve_available_sfx_assets(
        job=job,
        roots=roots,
    )

    sfx_decisions: list[SceneSfxDecision] = []
    if sfx_enabled:
        sfx_decisions = plan_scene_sfx_events(job=job, manifest=manifest, edit_manifest=edit_manifest)
        LOGGER.info(
            "job=%s audio_sfx planned=%s selected=%s max=%s available=%s",
            job.job_id,
            len(sfx_decisions),
            sum(1 for decision in sfx_decisions if decision.sfx_selected is not None),
            _read_int_metadata(
                job,
                "edit_max_sfx_per_video",
                DEFAULT_EDIT_MAX_SFX_PER_VIDEO,
                minimum=0,
                maximum=24,
            ),
            len(available_sfx_assets),
        )
        for decision in sfx_decisions:
            if decision.sfx_selected is None:
                LOGGER.debug(
                    "job=%s audio_sfx_skip scene=%s role=%s reason=%s candidates=%s",
                    job.job_id,
                    decision.scene_id,
                    decision.scene_role,
                    decision.skipped_reason or "none",
                    decision.sfx_candidates,
                )
                continue

            LOGGER.debug(
                "job=%s audio_sfx_decision scene=%s role=%s selected=%s placement=%s time=%.3f reason=%s candidates=%s",
                job.job_id,
                decision.scene_id,
                decision.scene_role,
                decision.sfx_selected,
                decision.placement or "none",
                decision.event_time_seconds or 0.0,
                decision.selected_reason or "none",
                decision.sfx_candidates,
            )
    else:
        LOGGER.info("job=%s audio_sfx disabled", job.job_id)

    recent_sfx_paths: list[Path] = []
    recent_sfx_profiles: list[str] = []
    for decision in sfx_decisions:
        if decision.sfx_selected is None:
            continue

        resolved_asset = await _resolve_scene_sfx_asset(
            job=job,
            decision=decision,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
            recent_asset_paths=recent_sfx_paths,
            recent_profiles=recent_sfx_profiles,
            recent_window=recent_window,
        )
        if resolved_asset is None:
            decision.skipped_reason = decision.skipped_reason or "asset_missing_profile"
            LOGGER.info(
                "job=%s audio_sfx_missing scene=%s selected=%s candidates=%s",
                job.job_id,
                decision.scene_id,
                decision.sfx_selected,
                decision.sfx_candidates,
            )
            continue

        profile, sfx_path, asset_selection_reason = resolved_asset
        if decision.sfx_selected != profile:
            LOGGER.info(
                "job=%s audio_sfx_fallback scene=%s from=%s to=%s",
                job.job_id,
                decision.scene_id,
                decision.sfx_selected,
                profile,
            )
            decision.fallback_from = decision.sfx_selected
            decision.sfx_selected = profile

        gain_db = _resolve_sfx_gain_db(
            profile=profile,
            scene_role=decision.scene_role,
            placement=decision.placement,
            scene_type=decision.scene_type,
            proof_mode=decision.proof_mode,
            visual_asset_mode=decision.visual_asset_mode,
            scene_id=decision.scene_id,
        )
        resolved_mix_volume = sfx_volume * _db_to_gain_factor(gain_db)
        decision.mixed = True
        decision.skipped_reason = None
        decision.gain_db = gain_db
        decision.gain_strategy = DEFAULT_SFX_GAIN_STRATEGY
        decision.asset_selection_reason = asset_selection_reason
        decision.profile_family = _resolve_sfx_profile_family(profile)
        decision.resolved_asset_path = str(sfx_path)
        decision.mix_volume = resolved_mix_volume
        event = _build_audio_event_from_decision(
            decision,
            asset_path=sfx_path,
            mix_volume=resolved_mix_volume,
        )
        sfx_events.append(event)
        recent_sfx_paths.append(sfx_path)
        recent_sfx_profiles.append(profile)
        LOGGER.info(
            "job=%s audio_sfx scene=%s profile=%s path=%s placement=%s time=%.3f",
            job.job_id,
            event.scene_id,
            event.profile,
            sfx_path,
            event.placement,
            event.time_seconds,
        )
        layers.append(
            AudioLayerSpec(
                kind="sfx",
                profile=event.profile,
                path=sfx_path,
                start_seconds=event.time_seconds,
                duration_seconds=_resolve_sfx_duration_seconds(event.profile),
                volume=resolved_mix_volume,
                loop=False,
            )
        )

    return ResolvedAudioMixPlan(
        layers=_merge_bgm_layers(layers),
        missing_profiles=missing_profiles,
        sfx_events=sfx_events,
        sfx_decisions=sfx_decisions,
        primary_bgm_profile=primary_bgm_profile,
        available_sfx_assets=available_sfx_assets,
        available_sfx_asset_pool=available_sfx_asset_pool,
    )


async def _resolve_audio_profile_paths(
    *,
    job: VideoJob,
    kind: str,
    profile: str,
    roots: list[Path],
    cache: dict[tuple[str, str], tuple[Path, ...]],
    missing_profiles: list[str],
    missing_seen: set[str],
    record_missing: bool = True,
) -> tuple[Path, ...]:
    normalized_profile = profile.strip().lower()
    if normalized_profile == "none":
        return ()

    cache_key = (kind, normalized_profile)
    if cache_key in cache:
        if not cache[cache_key] and record_missing:
            marker = f"{kind}:{normalized_profile}"
            if marker not in missing_seen:
                missing_seen.add(marker)
                missing_profiles.append(marker)
                LOGGER.warning("job=%s audio_profile_missing profile=%s", job.job_id, marker)
        return cache[cache_key]

    resolved_paths: list[Path] = []
    seen_paths: set[str] = set()
    for candidate in _resolve_audio_profile_candidates(
        job=job,
        kind=kind,
        profile=normalized_profile,
        roots=roots,
    ):
        candidate_key = str(candidate).lower()
        if candidate_key in seen_paths:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        probe = await _probe_media(candidate)
        if not probe.has_audio:
            continue
        seen_paths.add(candidate_key)
        resolved_paths.append(candidate)

    if not resolved_paths and record_missing:
        marker = f"{kind}:{normalized_profile}"
        if marker not in missing_seen:
            missing_seen.add(marker)
            missing_profiles.append(marker)
            LOGGER.warning("job=%s audio_profile_missing profile=%s", job.job_id, marker)

    cache[cache_key] = tuple(resolved_paths)
    return cache[cache_key]


async def _resolve_audio_profile_path(
    *,
    job: VideoJob,
    kind: str,
    profile: str,
    roots: list[Path],
    cache: dict[tuple[str, str], tuple[Path, ...]],
    missing_profiles: list[str],
    missing_seen: set[str],
    record_missing: bool = True,
) -> Path | None:
    resolved_paths = await _resolve_audio_profile_paths(
        job=job,
        kind=kind,
        profile=profile,
        roots=roots,
        cache=cache,
        missing_profiles=missing_profiles,
        missing_seen=missing_seen,
        record_missing=record_missing,
    )
    return resolved_paths[0] if resolved_paths else None


async def _resolve_available_sfx_assets(
    *,
    job: VideoJob,
    roots: list[Path],
) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    available: dict[str, str] = {}
    asset_pool: dict[str, tuple[str, ...]] = {}
    probe_cache: dict[tuple[str, str], tuple[Path, ...]] = {}
    for profile in sorted(SFX_PROFILES):
        if profile == "none":
            continue
        resolved_paths = await _resolve_audio_profile_paths(
            job=job,
            kind="sfx",
            profile=profile,
            roots=roots,
            cache=probe_cache,
            missing_profiles=[],
            missing_seen=set(),
            record_missing=False,
        )
        if resolved_paths:
            available[profile] = str(resolved_paths[0])
            asset_pool[profile] = tuple(str(path) for path in resolved_paths)
    return available, asset_pool


async def _resolve_scene_sfx_asset(
    *,
    job: VideoJob,
    decision: SceneSfxDecision,
    roots: list[Path],
    cache: dict[tuple[str, str], tuple[Path, ...]],
    missing_profiles: list[str],
    missing_seen: set[str],
    recent_asset_paths: list[Path],
    recent_profiles: list[str],
    recent_window: int,
) -> tuple[str, Path, str] | None:
    if decision.sfx_selected is None:
        return None

    ordered_profiles: list[str] = []
    selected_index = (
        decision.sfx_candidates.index(decision.sfx_selected)
        if decision.sfx_selected in decision.sfx_candidates
        else 0
    )
    for profile in decision.sfx_candidates[selected_index:]:
        normalized = profile.strip().lower()
        if normalized and normalized != "none" and normalized not in ordered_profiles:
            ordered_profiles.append(normalized)

    best_choice: tuple[int, str, Path, str] | None = None
    for profile_index, profile in enumerate(ordered_profiles):
        resolved_paths = await _resolve_audio_profile_paths(
            job=job,
            kind="sfx",
            profile=profile,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
            record_missing=False,
        )
        if not resolved_paths:
            continue

        chosen_path, choice_reason, score = _select_sfx_asset_candidate(
            profile=profile,
            scene_id=decision.scene_id,
            candidate_paths=resolved_paths,
            recent_asset_paths=recent_asset_paths,
            recent_profiles=recent_profiles,
            recent_window=recent_window,
        )
        combined_score = profile_index * 24 + score
        if best_choice is None or combined_score < best_choice[0]:
            best_choice = (
                combined_score,
                profile,
                chosen_path,
                ("primary_profile" if profile_index == 0 else "alternate_profile") + f":{choice_reason}",
            )

    if best_choice is None:
        await _resolve_audio_profile_paths(
            job=job,
            kind="sfx",
            profile=decision.sfx_selected,
            roots=roots,
            cache=cache,
            missing_profiles=missing_profiles,
            missing_seen=missing_seen,
            record_missing=True,
        )
        return None

    _, profile, chosen_path, choice_reason = best_choice
    return profile, chosen_path, choice_reason


def _build_audio_event_from_decision(
    decision: SceneSfxDecision,
    *,
    asset_path: Path | None = None,
    mix_volume: float | None = None,
) -> AudioEventSpec:
    if (
        decision.sfx_selected is None
        or decision.kind is None
        or decision.placement is None
        or decision.event_time_seconds is None
    ):
        raise AssemblerError(f"cannot build audio event from incomplete decision: {decision.scene_id}")

    return AudioEventSpec(
        scene_id=decision.scene_id,
        scene_index=decision.scene_index,
        scene_role=decision.scene_role,
        scene_type=decision.scene_type,
        proof_mode=decision.proof_mode,
        visual_asset_mode=decision.visual_asset_mode,
        kind=decision.kind,
        profile=decision.sfx_selected,
        time_seconds=decision.event_time_seconds,
        gain_db=decision.gain_db
        if decision.gain_db is not None
        else _resolve_sfx_gain_db(
            profile=decision.sfx_selected,
            scene_role=decision.scene_role,
            placement=decision.placement,
            scene_type=decision.scene_type,
            proof_mode=decision.proof_mode,
            visual_asset_mode=decision.visual_asset_mode,
            scene_id=decision.scene_id,
        ),
        mix_volume=mix_volume,
        placement=decision.placement,
        asset_path=None if asset_path is None else str(asset_path),
        selected_reason=decision.selected_reason,
        fallback_from=decision.fallback_from,
        transition_in=decision.transition_in,
        transition_out=decision.transition_out,
        timing_mode=decision.timing_mode,
        timing_offset_ms=decision.timing_offset_ms,
        gain_strategy=decision.gain_strategy,
        asset_selection_reason=decision.asset_selection_reason,
        profile_family=decision.profile_family,
        sfx_candidates=tuple(decision.sfx_candidates),
    )


def _select_sfx_asset_candidate(
    *,
    profile: str,
    scene_id: str,
    candidate_paths: tuple[Path, ...],
    recent_asset_paths: list[Path],
    recent_profiles: list[str],
    recent_window: int,
) -> tuple[Path, str, int]:
    recent_asset_window = recent_asset_paths[-recent_window:]
    recent_asset_keys = [str(path).lower() for path in recent_asset_window]
    recent_usage_counts: dict[str, int] = {}
    for key in recent_asset_keys:
        recent_usage_counts[key] = recent_usage_counts.get(key, 0) + 1

    previous_asset_key = recent_asset_keys[-1] if recent_asset_keys else None
    previous_profile = recent_profiles[-1] if recent_profiles else None
    profile_family = _resolve_sfx_profile_family(profile)
    previous_family = _resolve_sfx_profile_family(previous_profile) if previous_profile is not None else None

    best_choice: tuple[int, Path, str] | None = None
    for candidate in candidate_paths:
        candidate_key = str(candidate).lower()
        score = 0
        if previous_asset_key == candidate_key:
            score += 80
        if candidate_key in recent_asset_keys:
            score += 28
        score += recent_usage_counts.get(candidate_key, 0) * 18
        if previous_profile == profile:
            score += 10
        if previous_family == profile_family:
            score += 8
        score += _stable_mod(scene_id, profile, candidate.name, modulo=9)

        if previous_asset_key == candidate_key:
            choice_reason = "single_asset_only"
        elif candidate_key not in recent_asset_keys:
            choice_reason = "fresh_asset_rotation"
        elif recent_usage_counts.get(candidate_key, 0) <= 1:
            choice_reason = "recent_history_rotation"
        else:
            choice_reason = "least_reused_asset"

        if best_choice is None or score < best_choice[0]:
            best_choice = (score, candidate, choice_reason)

    if best_choice is None:
        raise AssemblerError(f"cannot select SFX asset candidate for profile '{profile}'")

    score, candidate, choice_reason = best_choice
    return candidate, choice_reason, score


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
        for candidate in _resolve_builtin_audio_profile_candidates(
            kind=kind,
            profile=profile,
            root=root,
        ):
            _register(candidate)
        for extension in EDIT_AUDIO_EXTENSIONS:
            _register((root / kind / f"{profile}{extension}").resolve())
            _register((root / f"{profile}{extension}").resolve())

    return candidates


def _resolve_builtin_audio_profile_candidates(
    *,
    kind: str,
    profile: str,
    root: Path,
) -> list[Path]:
    if kind != "sfx":
        return []
    return [
        (root / relative_path).resolve()
        for relative_path in LEGACY_TIKTOK_SFX_PROFILE_PATHS.get(profile, ())
    ]


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

    auto_discovery_enabled = _read_bool_metadata(
        job,
        "edit_audio_auto_discovery",
        default=True,
    )
    root_candidates = configured_roots
    if not root_candidates and auto_discovery_enabled:
        root_candidates = list(DEFAULT_EDIT_AUDIO_ROOT_CANDIDATES)

    roots: list[Path] = []
    seen: set[str] = set()
    for raw_root in root_candidates:
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


def _resolve_primary_bgm_profile(
    manifest: SceneManifest,
    edit_manifest: EditManifest,
) -> str | None:
    counts: dict[str, int] = {}
    first_scene_index: dict[str, int] = {}
    for scene in manifest.scenes:
        plan = edit_manifest.get_scene_plan(scene.scene_id)
        if plan is None:
            continue
        profile = plan.bgm_profile.strip().lower()
        if not profile or profile == "none":
            continue
        counts[profile] = counts.get(profile, 0) + 1
        first_scene_index.setdefault(profile, scene.index)

    if not counts:
        return None
    return min(counts.items(), key=lambda item: (-item[1], first_scene_index[item[0]], item[0]))[0]


def plan_scene_sfx_events(
    *,
    job: VideoJob,
    manifest: SceneManifest,
    edit_manifest: EditManifest,
) -> list[SceneSfxDecision]:
    max_events = _read_int_metadata(
        job,
        "edit_max_sfx_per_video",
        DEFAULT_EDIT_MAX_SFX_PER_VIDEO,
        minimum=0,
        maximum=24,
    )
    min_gap_seconds = _read_int_metadata(
        job,
        "edit_min_sfx_gap_ms",
        DEFAULT_EDIT_MIN_SFX_GAP_MS,
        minimum=0,
        maximum=5000,
    ) / 1000.0
    strong_cooldown_scenes = _read_int_metadata(
        job,
        "edit_sfx_strong_cooldown_scenes",
        DEFAULT_EDIT_SFX_STRONG_COOLDOWN_SCENES,
        minimum=0,
        maximum=6,
    )
    timing_spread_ms = _read_int_metadata(
        job,
        "edit_sfx_timing_spread_ms",
        DEFAULT_EDIT_SFX_TIMING_SPREAD_MS,
        minimum=0,
        maximum=250,
    )
    same_family_cooldown_scenes = _read_int_metadata(
        job,
        "edit_sfx_same_family_cooldown_scenes",
        DEFAULT_EDIT_SFX_SAME_FAMILY_COOLDOWN_SCENES,
        minimum=0,
        maximum=4,
    )

    accepted_events: list[AudioEventSpec] = []
    decisions: list[SceneSfxDecision] = []
    strong_cooldown_until_scene = -1

    for scene in manifest.scenes:
        plan = edit_manifest.get_scene_plan(scene.scene_id)
        if plan is None:
            continue

        decision = plan_scene_sfx_event(
            scene=scene,
            plan=plan,
            accepted_events=accepted_events,
            max_events=max_events,
            min_gap_seconds=min_gap_seconds,
            strong_cooldown_until_scene=strong_cooldown_until_scene,
            timing_spread_ms=timing_spread_ms,
            same_family_cooldown_scenes=same_family_cooldown_scenes,
        )
        decisions.append(decision)
        if decision.sfx_selected is None:
            continue

        accepted_event = _build_audio_event_from_decision(decision)
        accepted_events.append(accepted_event)
        if _is_strong_sfx_profile(accepted_event.profile):
            strong_cooldown_until_scene = max(
                strong_cooldown_until_scene,
                scene.index + strong_cooldown_scenes,
            )

    return decisions


def plan_scene_sfx_event(
    *,
    scene: SceneSegment,
    plan: SceneEditPlan,
    accepted_events: list[AudioEventSpec],
    max_events: int,
    min_gap_seconds: float,
    strong_cooldown_until_scene: int,
    timing_spread_ms: int,
    same_family_cooldown_scenes: int,
) -> SceneSfxDecision:
    candidates, placement_candidates, selected_reason, default_skip_reason = _resolve_scene_sfx_blueprint(
        scene,
        plan,
    )
    decision = SceneSfxDecision(
        scene_id=scene.scene_id,
        scene_index=scene.index,
        scene_role=plan.pace_role,
        scene_type=scene.scene_type,
        proof_mode=scene.proof_mode,
        visual_asset_mode=scene.visual_asset_mode,
        transition_in=plan.transition_in,
        transition_out=plan.transition_out,
        sfx_candidates=candidates,
        placement_candidates=placement_candidates,
        sfx_selected=None,
        selected_reason=None,
        event_time_seconds=None,
        placement=None,
        skipped_reason=default_skip_reason,
    )

    if max_events <= 0:
        decision.skipped_reason = "max_events_reached"
        return decision
    if not candidates:
        decision.skipped_reason = default_skip_reason or "scene_role_prefers_silence"
        return decision
    if len(accepted_events) >= max_events:
        decision.skipped_reason = "max_events_reached"
        return decision
    if not placement_candidates:
        decision.skipped_reason = default_skip_reason or "scene_role_prefers_silence"
        return decision

    rejection_reasons: list[str] = []
    for profile in candidates:
        event_kind = _resolve_sfx_event_kind(profile)
        placement = _select_sfx_placement(
            scene,
            plan=plan,
            profile=profile,
            placement_candidates=placement_candidates,
            accepted_events=accepted_events,
        )
        if placement is None:
            rejection_reasons.append("scene_role_prefers_silence")
            continue

        event_time_seconds, timing_mode, timing_offset_ms = _resolve_sfx_event_timing(
            scene=scene,
            profile=profile,
            event_kind=event_kind,
            placement=placement,
            timing_spread_ms=timing_spread_ms,
        )
        skip_reason = _resolve_sfx_candidate_skip_reason(
            scene=scene,
            profile=profile,
            event_time_seconds=event_time_seconds,
            accepted_events=accepted_events,
            min_gap_seconds=min_gap_seconds,
            strong_cooldown_until_scene=strong_cooldown_until_scene,
            same_family_cooldown_scenes=same_family_cooldown_scenes,
        )
        if skip_reason is not None:
            rejection_reasons.append(skip_reason)
            continue

        decision.sfx_selected = profile
        decision.selected_reason = selected_reason
        decision.event_time_seconds = event_time_seconds
        decision.placement = placement
        decision.kind = event_kind
        decision.timing_mode = timing_mode
        decision.timing_offset_ms = timing_offset_ms
        decision.profile_family = _resolve_sfx_profile_family(profile)
        decision.skipped_reason = None
        return decision

    decision.skipped_reason = rejection_reasons[0] if rejection_reasons else (default_skip_reason or "scene_role_prefers_silence")
    return decision


def _resolve_scene_sfx_blueprint(
    scene: SceneSegment,
    plan: SceneEditPlan,
) -> tuple[list[str], list[str], str | None, str | None]:
    if plan.pace_role == "hook":
        return _resolve_hook_sfx_blueprint(scene, plan)
    if plan.pace_role == "proof":
        return _resolve_proof_sfx_blueprint(scene, plan)
    if plan.pace_role == "cta":
        return _resolve_cta_sfx_blueprint(scene, plan)
    return _resolve_explain_sfx_blueprint(scene, plan)


def _resolve_hook_sfx_blueprint(
    scene: SceneSegment,
    plan: SceneEditPlan,
) -> tuple[list[str], list[str], str, str | None]:
    hint = plan.sfx_profile.strip().lower()
    duration_seconds = max(0.05, float(scene.duration_seconds))
    candidates: list[str] = []
    placements: list[str] = []

    if hint in HOOK_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)
    if scene.index == 0 and duration_seconds <= 2.6 and (
        _scene_has_numeric_proof(scene) or _scene_contains_any_token(scene, HOOK_INTENSITY_TOKENS)
    ):
        _append_unique_sfx_candidate(candidates, "hit_strong")
    _append_unique_sfx_candidate(candidates, "hit_soft")
    if duration_seconds >= 1.45:
        _append_unique_sfx_candidate(candidates, "whoosh_fast")
    if scene.index == 0:
        _append_unique_sfx_candidate(candidates, "glitch_light")
    if hint not in {"", "none"} and hint not in HOOK_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)

    if duration_seconds < 1.0 and hint in {"", "none"}:
        return [], ["scene_start"], "hook_opening_accent", "scene_too_short"

    if _scene_has_numeric_proof(scene) or scene.highlight_text.strip():
        _append_unique_sfx_placement(placements, "first_keyword")
    elif scene.cue_timeline:
        _append_unique_sfx_placement(placements, "early_hook")
    _append_unique_sfx_placement(placements, "scene_start")
    if plan.transition_in != "cut" and scene.index > 0:
        _append_unique_sfx_placement(placements, "transition_in")
    selected_reason = "hook_promise_accent" if "first_keyword" in placements else "hook_opening_accent"
    return candidates, placements, selected_reason, None


def _resolve_proof_sfx_blueprint(
    scene: SceneSegment,
    plan: SceneEditPlan,
) -> tuple[list[str], list[str], str, str | None]:
    hint = plan.sfx_profile.strip().lower()
    duration_seconds = max(0.05, float(scene.duration_seconds))
    visual_mode = scene.visual_asset_mode.strip().lower()
    candidates: list[str] = []
    placements: list[str] = []
    ui_context = visual_mode in UI_FORWARD_VISUAL_MODES or _scene_contains_any_token(scene, UI_REVEAL_TOKENS)
    proof_context = _scene_has_numeric_proof(scene) or _scene_contains_any_token(scene, PROOF_REVEAL_TOKENS) or scene.proof_mode != "generic"

    if hint.startswith("click_ui") or hint.startswith("pop"):
        _append_unique_sfx_candidate(candidates, hint)
    if ui_context:
        _append_unique_sfx_candidate(candidates, "click_ui_soft")
        if duration_seconds <= 2.6 or scene.proof_mode != "generic":
            _append_unique_sfx_candidate(candidates, "click_ui_hard")
    if proof_context:
        _append_unique_sfx_candidate(candidates, "pop_light")
    if hint in PROOF_SFX_PROFILES and hint not in {"click_ui_soft", "click_ui_hard", "pop_light"}:
        _append_unique_sfx_candidate(candidates, hint)
    if proof_context and not ui_context:
        _append_unique_sfx_candidate(candidates, "hit_soft")
    if hint not in {"", "none"} and hint not in PROOF_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)

    if duration_seconds < 1.25 and hint in {"", "none"}:
        return [], ["proof_reveal"], "proof_reveal_accent", "scene_too_short"
    if not candidates:
        return [], ["proof_reveal"], "proof_reveal_accent", "scene_role_prefers_silence"

    _append_unique_sfx_placement(placements, "proof_reveal")
    _append_unique_sfx_placement(placements, "impact_moment")
    if plan.transition_in != "cut":
        _append_unique_sfx_placement(placements, "transition_in")
    selected_reason = "proof_ui_confirmation" if ui_context else "proof_reveal_accent"
    return candidates, placements, selected_reason, None


def _resolve_explain_sfx_blueprint(
    scene: SceneSegment,
    plan: SceneEditPlan,
) -> tuple[list[str], list[str], str, str | None]:
    hint = plan.sfx_profile.strip().lower()
    duration_seconds = max(0.05, float(scene.duration_seconds))
    visual_mode = scene.visual_asset_mode.strip().lower()
    candidates: list[str] = []
    placements: list[str] = []
    has_multiple_ideas = len(scene.cue_timeline) >= 3 or len(scene.keywords) >= 3
    has_workflow_motion = scene.scene_type in {"workflow", "interface"} or visual_mode in {"workflow", "ui"}

    if hint in EXPLAIN_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)
    if duration_seconds >= 3.0 and has_workflow_motion:
        _append_unique_sfx_candidate(candidates, "whoosh_soft")
    if duration_seconds >= 2.8 and has_multiple_ideas and not has_workflow_motion:
        _append_unique_sfx_candidate(candidates, "pop_short")
    if hint not in {"", "none"} and hint not in EXPLAIN_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)

    if duration_seconds < 2.5 and hint in {"", "none"}:
        return [], ["scene_mid"], "explain_separator", "scene_too_short"
    if not candidates:
        return [], ["scene_mid"], "explain_separator", "scene_role_prefers_silence"

    if has_multiple_ideas:
        _append_unique_sfx_placement(placements, "first_keyword")
    if has_workflow_motion and plan.transition_in != "cut":
        _append_unique_sfx_placement(placements, "transition_in")
    _append_unique_sfx_placement(placements, "scene_mid")
    if has_workflow_motion and plan.transition_out != "cut":
        _append_unique_sfx_placement(placements, "transition_out")
    return candidates, placements, ("workflow_motion_support" if has_workflow_motion else "explain_separator"), None


def _resolve_cta_sfx_blueprint(
    scene: SceneSegment,
    plan: SceneEditPlan,
) -> tuple[list[str], list[str], str, str | None]:
    hint = plan.sfx_profile.strip().lower()
    duration_seconds = max(0.05, float(scene.duration_seconds))
    candidates: list[str] = []
    placements: list[str] = []

    if hint in CTA_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)
    _append_unique_sfx_candidate(candidates, "pop_short")
    if duration_seconds >= 1.6:
        _append_unique_sfx_candidate(candidates, "hit_soft")
    if duration_seconds >= 1.9 or plan.transition_out != "cut":
        _append_unique_sfx_candidate(candidates, "whoosh_soft")
    if hint not in {"", "none"} and hint not in CTA_SFX_PROFILES:
        _append_unique_sfx_candidate(candidates, hint)

    if duration_seconds < 1.2 and hint in {"", "none"}:
        return [], ["cta_close"], "cta_clean_close", "scene_too_short"

    _append_unique_sfx_placement(placements, "cta_close" if duration_seconds >= 1.35 else "scene_end")
    if plan.transition_out != "cut":
        _append_unique_sfx_placement(placements, "transition_out")
    _append_unique_sfx_placement(placements, "scene_end")
    return candidates, placements, "cta_clean_close", None


def _append_unique_sfx_candidate(candidates: list[str], profile: str) -> None:
    normalized = profile.strip().lower()
    if normalized and normalized != "none" and normalized not in candidates:
        candidates.append(normalized)


def _append_unique_sfx_placement(candidates: list[str], placement: str) -> None:
    normalized = placement.strip().lower()
    if normalized and normalized not in candidates:
        candidates.append(normalized)


def _scene_has_numeric_proof(scene: SceneSegment) -> bool:
    if any(character.isdigit() for character in scene.source_text):
        return True
    return any(any(character.isdigit() for character in cue.text) for cue in scene.cue_timeline)


def _scene_contains_any_token(scene: SceneSegment, tokens: tuple[str, ...]) -> bool:
    lowered_text = " ".join(
        [scene.source_text, scene.highlight_text, scene.title_text, *scene.keywords]
        + [cue.text for cue in scene.cue_timeline]
    ).lower()
    return any(token in lowered_text for token in tokens)


def _resolve_sfx_candidate_skip_reason(
    *,
    scene: SceneSegment,
    profile: str,
    event_time_seconds: float,
    accepted_events: list[AudioEventSpec],
    min_gap_seconds: float,
    strong_cooldown_until_scene: int,
    same_family_cooldown_scenes: int,
) -> str | None:
    if _is_strong_sfx_profile(profile) and scene.index <= strong_cooldown_until_scene:
        return "strong_accent_cooldown"

    if not accepted_events:
        return None

    previous_event = accepted_events[-1]
    gap_seconds = event_time_seconds - previous_event.time_seconds
    if gap_seconds < min_gap_seconds:
        return "min_gap_guardrail"
    if _is_strong_sfx_profile(profile) and _is_strong_sfx_profile(previous_event.profile):
        return "strong_accent_cooldown"

    profile_family = _resolve_sfx_profile_family(profile)
    previous_family = _resolve_sfx_profile_family(previous_event.profile)
    if (
        same_family_cooldown_scenes > 0
        and profile_family == previous_family
        and (scene.index - previous_event.scene_index) <= same_family_cooldown_scenes
    ):
        return "family_repetition_guardrail"
    if profile_family == "whoosh" and previous_family == "whoosh" and gap_seconds < max(1.2, min_gap_seconds):
        return "whoosh_spacing_guardrail"

    recent_profiles = [event.profile for event in accepted_events[-2:]]
    if profile in recent_profiles:
        return "profile_repetition_guardrail"

    return None


def _is_strong_sfx_profile(profile: str) -> bool:
    return profile in STRONG_SFX_PROFILES


def _resolve_sfx_profile_family(profile: str) -> str:
    if profile.startswith("click_ui"):
        return "ui_click"
    if profile.startswith("pop"):
        return "pop"
    if profile.startswith("whoosh"):
        return "whoosh"
    if profile.startswith("glitch"):
        return "glitch"
    if profile.startswith("riser"):
        return "resolve"
    if profile.startswith("hit"):
        return "hit"
    return "generic"


def _resolve_sfx_event_kind(profile: str) -> str:
    if profile.startswith("whoosh") or profile.startswith("riser"):
        return "transition"
    if profile.startswith("click_ui") or profile.startswith("pop"):
        return "ui"
    return "impact"


def _select_sfx_placement(
    scene: SceneSegment,
    *,
    plan: SceneEditPlan,
    profile: str,
    placement_candidates: list[str],
    accepted_events: list[AudioEventSpec],
) -> str | None:
    if not placement_candidates:
        return None

    last_placement = accepted_events[-1].placement if accepted_events else None
    profile_family = _resolve_sfx_profile_family(profile)
    best_choice: tuple[int, str] | None = None
    for index, placement in enumerate(placement_candidates):
        score = index * 6
        if placement == last_placement:
            score += 8
        if placement == "scene_start" and scene.cue_timeline:
            score += 4
        if placement == "first_keyword" and not scene.cue_timeline:
            score += 10
        if placement == "transition_in" and plan.transition_in == "cut":
            score += 6
        if placement == "transition_out" and plan.transition_out == "cut":
            score += 6
        if profile_family in {"whoosh", "resolve"} and placement in {"transition_in", "transition_out"}:
            score -= 2
        if profile_family in {"ui_click", "pop"} and placement in {"proof_reveal", "impact_moment", "first_keyword"}:
            score -= 1
        score += _stable_mod(scene.scene_id, profile, placement, modulo=5)
        if best_choice is None or score < best_choice[0]:
            best_choice = (score, placement)

    return None if best_choice is None else best_choice[1]


def _resolve_sfx_event_timing(
    *,
    scene: SceneSegment,
    profile: str,
    event_kind: str,
    placement: str,
    timing_spread_ms: int,
) -> tuple[float, str, int]:
    base_time_seconds = _resolve_sfx_event_time_seconds(
        scene,
        event_kind=event_kind,
        placement=placement,
    )
    offset_ms = _resolve_sfx_timing_offset_ms(
        scene=scene,
        profile=profile,
        placement=placement,
        event_kind=event_kind,
        timing_spread_ms=timing_spread_ms,
    )
    event_time_seconds = _clamp_scene_event_time_seconds(
        scene,
        base_time_seconds + (offset_ms / 1000.0),
    )
    return round(event_time_seconds, 3), _resolve_sfx_timing_mode(placement), offset_ms


def _resolve_sfx_event_time_seconds(
    scene: SceneSegment,
    *,
    event_kind: str,
    placement: str,
) -> float:
    scene_start = max(0.0, float(scene.start_seconds))
    scene_end = max(scene_start + 0.05, float(scene.end_seconds))
    duration_seconds = max(0.05, float(scene.duration_seconds))

    if placement == "scene_start":
        return round(min(scene_end - 0.05, scene_start + 0.04), 3)
    if placement == "early_hook":
        return round(_resolve_hook_time_seconds(scene), 3)
    if placement == "first_keyword":
        return round(_resolve_scene_keyword_time_seconds(scene), 3)
    if placement == "proof_reveal":
        return round(_resolve_proof_reveal_time_seconds(scene, event_kind=event_kind), 3)
    if placement == "transition_in":
        return round(min(scene_end - 0.05, scene_start + 0.03), 3)
    if placement == "transition_out":
        return round(max(scene_start, scene_end - min(0.1, duration_seconds * 0.1)), 3)
    if placement == "scene_mid":
        return round(scene_start + duration_seconds * 0.48, 3)
    if placement == "scene_end":
        return round(max(scene_start, scene_end - min(0.18, duration_seconds * 0.18)), 3)
    if placement == "cta_close":
        return round(max(scene_start, scene_end - min(0.14, duration_seconds * 0.12)), 3)
    if placement == "impact_moment":
        return round(_resolve_scene_impact_time_seconds(scene, event_kind=event_kind), 3)
    return round(scene_start + duration_seconds / 2.0, 3)


def _resolve_hook_time_seconds(scene: SceneSegment) -> float:
    scene_start = max(0.0, float(scene.start_seconds))
    scene_end = max(scene_start + 0.05, float(scene.end_seconds))
    duration_seconds = max(0.05, float(scene.duration_seconds))
    if scene.cue_timeline:
        first_cue = scene.cue_timeline[0]
        if _scene_has_numeric_proof(scene) or scene.highlight_text.strip():
            return min(scene_end - 0.05, max(scene_start, first_cue.start_seconds + 0.08))
        return min(scene_end - 0.05, scene_start + min(0.22, duration_seconds * 0.16))
    return min(scene_end - 0.05, scene_start + min(0.18, duration_seconds * 0.14))


def _resolve_scene_keyword_time_seconds(scene: SceneSegment) -> float:
    scene_start = max(0.0, float(scene.start_seconds))
    scene_end = max(scene_start + 0.05, float(scene.end_seconds))
    duration_seconds = max(0.05, float(scene.duration_seconds))
    if scene.cue_timeline:
        highlight = scene.highlight_text.strip().lower()
        keyword_tokens = [keyword.lower() for keyword in scene.keywords if keyword.strip()]
        for cue in scene.cue_timeline:
            lowered_cue = cue.text.lower()
            if highlight and highlight in lowered_cue:
                return min(scene_end - 0.05, max(scene_start, cue.start_seconds + 0.06))
            if any(token in lowered_cue for token in keyword_tokens):
                return min(scene_end - 0.05, max(scene_start, cue.start_seconds + 0.06))
            if any(character.isdigit() for character in cue.text):
                return min(scene_end - 0.05, max(scene_start, cue.start_seconds + 0.06))
        fallback_cue = scene.cue_timeline[min(len(scene.cue_timeline) - 1, 1)]
        return min(scene_end - 0.05, max(scene_start, fallback_cue.start_seconds + 0.06))
    return min(scene_end - 0.05, scene_start + min(0.28, duration_seconds * 0.25))


def _resolve_proof_reveal_time_seconds(scene: SceneSegment, *, event_kind: str) -> float:
    if scene.cue_timeline:
        for cue in scene.cue_timeline:
            lowered_cue = cue.text.lower()
            if any(character.isdigit() for character in cue.text):
                return min(float(scene.end_seconds) - 0.05, max(float(scene.start_seconds), cue.start_seconds + 0.08))
            if any(token in lowered_cue for token in (*PROOF_REVEAL_TOKENS, *UI_REVEAL_TOKENS)):
                return min(float(scene.end_seconds) - 0.05, max(float(scene.start_seconds), cue.start_seconds + 0.08))
    return _resolve_scene_impact_time_seconds(scene, event_kind=event_kind)


def _resolve_scene_impact_time_seconds(scene: SceneSegment, *, event_kind: str) -> float:
    if scene.cue_timeline:
        candidate_cues = [
            cue
            for cue in scene.cue_timeline
            if any(character.isdigit() for character in cue.text)
        ]
        if not candidate_cues and event_kind == "ui":
            candidate_cues = [
                cue
                for cue in scene.cue_timeline
                if any(token in cue.text.lower() for token in ("ui", "outil", "tool", "proof", "preuve"))
            ]
        chosen_cue = candidate_cues[0] if candidate_cues else scene.cue_timeline[min(len(scene.cue_timeline) - 1, 1)]
        return min(float(scene.end_seconds) - 0.05, max(float(scene.start_seconds), chosen_cue.start_seconds + 0.06))

    return float(scene.start_seconds) + min(0.3, float(scene.duration_seconds) * 0.35)


def _resolve_sfx_timing_offset_ms(
    *,
    scene: SceneSegment,
    profile: str,
    placement: str,
    event_kind: str,
    timing_spread_ms: int,
) -> int:
    if timing_spread_ms <= 0:
        return 0

    spread = max(20, timing_spread_ms)
    if placement == "scene_start":
        return _stable_range(18, min(spread, 80), scene.scene_id, profile, placement, event_kind)
    if placement == "early_hook":
        return _stable_range(26, min(spread + 20, 120), scene.scene_id, profile, placement, event_kind)
    if placement == "first_keyword":
        return _stable_range(18, min(spread, 95), scene.scene_id, profile, placement, event_kind)
    if placement == "proof_reveal":
        return _stable_range(26, min(spread + 10, 110), scene.scene_id, profile, placement, event_kind)
    if placement == "impact_moment":
        return _stable_range(12, min(spread, 85), scene.scene_id, profile, placement, event_kind)
    if placement == "transition_in":
        return _stable_range(14, min(spread, 70), scene.scene_id, profile, placement, event_kind)
    if placement == "scene_mid":
        return _stable_range(-min(spread // 2, 60), min(spread // 2, 70), scene.scene_id, profile, placement, event_kind)
    if placement == "scene_end":
        return _stable_range(-min(spread + 20, 140), -35, scene.scene_id, profile, placement, event_kind)
    if placement == "cta_close":
        return _stable_range(-min(spread + 25, 150), -45, scene.scene_id, profile, placement, event_kind)
    if placement == "transition_out":
        return _stable_range(-min(spread + 20, 140), -35, scene.scene_id, profile, placement, event_kind)
    return 0


def _resolve_sfx_timing_mode(placement: str) -> str:
    if placement in {"first_keyword", "proof_reveal", "impact_moment"}:
        return "cue_emphasis_offset_v1"
    if placement in {"transition_in", "transition_out"}:
        return "transition_sync_offset_v1"
    if placement in {"cta_close", "scene_end"}:
        return "tail_close_offset_v1"
    return "scene_anchor_offset_v1"


def _clamp_scene_event_time_seconds(scene: SceneSegment, value: float) -> float:
    scene_start = max(0.0, float(scene.start_seconds))
    scene_end = max(scene_start + 0.05, float(scene.end_seconds))
    return max(scene_start + 0.02, min(scene_end - 0.05, value))


def _resolve_sfx_duration_seconds(profile: str) -> float:
    if profile.startswith("click_ui") or profile.startswith("pop"):
        return 0.22
    if profile.startswith("hit") or profile.startswith("glitch"):
        return 0.32
    if profile.startswith("whoosh"):
        return 0.5
    if profile.startswith("riser"):
        return 0.65
    return 0.3


def _resolve_sfx_gain_db(
    *,
    profile: str,
    scene_role: str,
    placement: str | None,
    scene_type: str,
    proof_mode: str,
    visual_asset_mode: str,
    scene_id: str,
) -> float:
    family = _resolve_sfx_profile_family(profile)
    gain_db = _resolve_sfx_profile_gain_db(profile)

    gain_db += {
        "hook": 0.35,
        "proof": -0.15,
        "explain": -1.0,
        "cta": -0.55,
    }.get(scene_role, -0.4)

    gain_db += {
        "scene_start": -0.2,
        "early_hook": 0.15,
        "first_keyword": 0.2,
        "proof_reveal": 0.1,
        "impact_moment": 0.0,
        "transition_in": -0.35,
        "scene_mid": -0.75,
        "scene_end": -0.8,
        "cta_close": -0.55,
        "transition_out": -0.65,
    }.get(placement or "", -0.25)

    if proof_mode != "generic" and family in {"ui_click", "pop"}:
        gain_db += 0.15
    if visual_asset_mode in UI_FORWARD_VISUAL_MODES and family == "ui_click":
        gain_db += 0.25
    if scene_type in {"workflow", "interface"} and family == "whoosh":
        gain_db += 0.15
    if family in {"hit", "glitch"} and scene_role != "hook":
        gain_db -= 0.45

    gain_db += (-0.35, -0.15, 0.0, 0.15)[_stable_mod(scene_id, profile, placement or "none", modulo=4)]
    return round(max(-7.5, min(-0.35, gain_db)), 2)


def _resolve_sfx_profile_gain_db(profile: str) -> float:
    if profile == "hit_strong":
        return -1.1
    if profile == "hit_soft":
        return -1.7
    if profile == "whoosh_fast":
        return -1.8
    if profile == "glitch_light":
        return -2.0
    if profile == "click_ui_soft":
        return -5.2
    if profile == "click_ui_hard":
        return -4.0
    if profile == "pop_light":
        return -4.1
    if profile == "pop_short":
        return -4.4
    if profile.startswith("riser"):
        return -3.8
    if profile.startswith("whoosh"):
        return -3.1
    return -3.0


def _stable_mod(*parts: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    payload = "||".join(part.strip().lower() for part in parts if part is not None)
    digest = hashlib.sha1(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=False) % modulo


def _stable_range(low: int, high: int, *parts: str) -> int:
    if low == high:
        return low
    if high < low:
        low, high = high, low
    span = (high - low) + 1
    return low + _stable_mod(*parts, modulo=span)


def _resolve_audio_mix_mode(
    *,
    has_voice_audio: bool,
    bgm_layer_count: int,
    sfx_layer_count: int,
    ducking_active: bool,
) -> str:
    if has_voice_audio and bgm_layer_count and sfx_layer_count and ducking_active:
        return "voice_bgm_ducked_sfx"
    if has_voice_audio and bgm_layer_count and ducking_active:
        return "voice_bgm_ducked"
    if has_voice_audio and sfx_layer_count:
        return "voice_sfx"
    if has_voice_audio:
        return "voice_only"
    if bgm_layer_count and sfx_layer_count:
        return "bgm_sfx"
    if bgm_layer_count:
        return "bgm_only"
    if sfx_layer_count:
        return "sfx_only"
    return "legacy"


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

    bgm_labels: list[str] = []
    sfx_labels: list[str] = []
    for input_index, layer in enumerate(audio_layers, start=2):
        layer_label = f"layer_{input_index}"
        parts.append(f"[{input_index}:a]{_build_audio_layer_filter(layer)}[{layer_label}]")
        if layer.kind == "bgm":
            bgm_labels.append(layer_label)
        else:
            sfx_labels.append(layer_label)

    mix_labels: list[str] = []
    voice_mix_label = voice_label
    voice_sidechain_label = voice_label
    if has_voice_audio and bgm_labels:
        voice_mix_label = "voice_main"
        voice_sidechain_label = "voice_sidechain"
        parts.append(f"[{voice_label}]asplit=2[{voice_mix_label}][{voice_sidechain_label}]")

    if has_voice_audio:
        mix_labels.append(f"[{voice_mix_label}]")

    bgm_bus_label = _append_bgm_bus(
        parts,
        job=job,
        bgm_labels=bgm_labels,
        voice_label=voice_sidechain_label,
        ducking_enabled=has_voice_audio and bool(bgm_labels),
    )
    if bgm_bus_label is not None:
        mix_labels.append(f"[{bgm_bus_label}]")

    mix_labels.extend(f"[{label}]" for label in sfx_labels)
    if not mix_labels:
        mix_labels.append(f"[{voice_mix_label}]")

    parts.append(_build_audio_mix_output(mix_labels))
    return ";".join(parts)


def _append_bgm_bus(
    parts: list[str],
    *,
    job: VideoJob,
    bgm_labels: list[str],
    voice_label: str,
    ducking_enabled: bool,
) -> str | None:
    if not bgm_labels:
        return None

    if len(bgm_labels) == 1:
        current_label = bgm_labels[0]
    else:
        current_label = "bgm_bus_pre"
        parts.append(
            "".join(f"[{label}]" for label in bgm_labels)
            + f"amix=inputs={len(bgm_labels)}:normalize=0:dropout_transition=0[{current_label}]"
        )

    if not ducking_enabled:
        return current_label

    ducked_label = "bgm_ducked"
    parts.append(
        f"[{current_label}][{voice_label}]"
        f"{_build_ducking_filter(job)}"
        f"[{ducked_label}]"
    )
    return ducked_label


def _build_ducking_filter(job: VideoJob) -> str:
    threshold = _read_float_metadata(
        job,
        "edit_ducking_threshold",
        DEFAULT_EDIT_DUCKING_THRESHOLD,
        minimum=0.001,
        maximum=1.0,
    )
    ratio = _read_float_metadata(
        job,
        "edit_ducking_ratio",
        DEFAULT_EDIT_DUCKING_RATIO,
        minimum=1.0,
        maximum=20.0,
    )
    attack_ms = _read_int_metadata(
        job,
        "edit_ducking_attack_ms",
        DEFAULT_EDIT_DUCKING_ATTACK_MS,
        minimum=1,
        maximum=2000,
    )
    release_ms = _read_int_metadata(
        job,
        "edit_ducking_release_ms",
        DEFAULT_EDIT_DUCKING_RELEASE_MS,
        minimum=10,
        maximum=9000,
    )
    return (
        "sidechaincompress="
        f"threshold={threshold:.3f}:"
        f"ratio={ratio:.2f}:"
        f"attack={attack_ms}:"
        f"release={release_ms}:"
        "makeup=1:link=average"
    )


def _build_audio_mix_output(mix_labels: list[str]) -> str:
    if len(mix_labels) == 1:
        return f"{mix_labels[0]}alimiter=limit=0.92[aout]"
    return (
        "".join(mix_labels)
        + f"amix=inputs={len(mix_labels)}:normalize=0:dropout_transition=0,"
        "alimiter=limit=0.92[aout]"
    )


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

    voice_gain_db = _read_float_metadata(
        job,
        "edit_voice_gain_db",
        DEFAULT_EDIT_VOICE_GAIN_DB,
        minimum=-12.0,
        maximum=12.0,
    )
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
        f"volume={voice_gain_db:.2f}dB,"
        f"acompressor=threshold=-18dB:ratio={compression_ratio:.2f}:attack=20:release=140,"
        f"loudnorm=I={target_lufs:.1f}:LRA=7:TP=-1.5"
    )


def _build_subtitle_filter(job: VideoJob, subtitles_path: Path) -> str:
    caption_settings = _resolve_caption_settings(job)
    font_name = resolve_runtime_subtitle_font(job).resolved_name
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
        caption_settings.safe_bottom_margin,
        minimum=60,
        maximum=420,
    )
    margin_side = _read_int_metadata(
        job,
        "edit_caption_safe_side_margin",
        caption_settings.safe_side_margin,
        minimum=40,
        maximum=180,
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
            "OutlineColour=&H62000000",
            "BackColour=&H18000000",
            "BorderStyle=1",
            f"Outline={outline}",
            f"Shadow={shadow}",
            f"Bold={bold_value}",
            "Alignment=2",
            f"MarginL={margin_side}",
            f"MarginR={margin_side}",
            f"MarginV={margin_v}",
            "Spacing=0.20",
        ]
    )
    return (
        f"subtitles='{_escape_filter_path(subtitles_path)}':"
        f"force_style='{_escape_ass_style(style)}'"
    )


def resolve_runtime_subtitle_font(job: VideoJob) -> RuntimeFontResolution:
    requested_name = _read_str_metadata(job, "subtitle_font_name", DEFAULT_SUBTITLE_FONT_NAME)
    return _resolve_runtime_font_name(
        job,
        requested_name=requested_name,
        requested_key="subtitle_font_requested",
        resolved_key="subtitle_font_resolved",
        fallbacks=SUBTITLE_FONT_FALLBACKS,
    )


def _resolve_runtime_edit_caption_font(job: VideoJob) -> RuntimeFontResolution:
    requested_name = _read_str_metadata(
        job,
        "edit_caption_font_name",
        _read_str_metadata(job, "subtitle_font_name", DEFAULT_SUBTITLE_FONT_NAME),
    )
    return _resolve_runtime_font_name(
        job,
        requested_name=requested_name,
        requested_key="edit_caption_font_requested",
        resolved_key="edit_caption_font_resolved",
        fallbacks=SUBTITLE_FONT_FALLBACKS,
    )


def resolve_runtime_story_font(job: VideoJob) -> RuntimeFontResolution:
    requested_name = _read_str_metadata(
        job,
        "story_text_font_name",
        _read_str_metadata(job, "overlay_font_name", DEFAULT_STORY_FONT_NAME),
    )
    return _resolve_runtime_font_name(
        job,
        requested_name=requested_name,
        requested_key="story_font_requested",
        resolved_key="story_font_resolved",
        fallbacks=DISPLAY_FONT_FALLBACKS,
    )


def resolve_runtime_story_symbol_font(job: VideoJob) -> RuntimeFontResolution:
    requested_name = _read_str_metadata(
        job,
        "overlay_symbol_font_name",
        DEFAULT_SYMBOL_FONT_NAME,
    )
    return _resolve_runtime_font_name(
        job,
        requested_name=requested_name,
        requested_key="story_symbol_font_requested",
        resolved_key="story_symbol_font_resolved",
        fallbacks=SYMBOL_FONT_FALLBACKS,
    )


def _resolve_runtime_font_name(
    job: VideoJob,
    *,
    requested_name: str,
    requested_key: str,
    resolved_key: str,
    fallbacks: tuple[str, ...],
) -> RuntimeFontResolution:
    cached_requested = job.metadata.get(requested_key)
    cached_resolved = job.metadata.get(resolved_key)
    if (
        isinstance(cached_requested, str)
        and isinstance(cached_resolved, str)
        and cached_requested.strip() == requested_name
        and cached_resolved.strip()
    ):
        used_fallback = _normalize_font_identity(cached_resolved) != _normalize_font_identity(
            requested_name
        )
        return RuntimeFontResolution(
            requested_name=requested_name,
            resolved_name=cached_resolved.strip(),
            used_fallback=used_fallback,
        )

    for candidate_name in _iter_runtime_font_candidates(requested_name, fallbacks=fallbacks):
        resolved_name = _probe_runtime_font_name(candidate_name)
        if resolved_name is None:
            continue
        used_fallback = _normalize_font_identity(resolved_name) != _normalize_font_identity(
            requested_name
        )
        job.set_metadata(requested_key, requested_name)
        job.set_metadata(resolved_key, resolved_name)
        if used_fallback:
            LOGGER.warning(
                "requested font '%s' is unavailable, falling back to '%s'",
                requested_name,
                resolved_name,
            )
        return RuntimeFontResolution(
            requested_name=requested_name,
            resolved_name=resolved_name,
            used_fallback=used_fallback,
        )

    raise AssemblerError(
        "unable to resolve a runtime subtitle font; tried "
        f"{', '.join(_iter_runtime_font_candidates(requested_name, fallbacks=fallbacks))}"
    )


def _iter_runtime_font_candidates(
    requested_name: str,
    *,
    fallbacks: tuple[str, ...],
) -> tuple[str, ...]:
    candidates: list[str] = []
    for candidate in (requested_name, *fallbacks):
        normalized_candidate = candidate.strip()
        if normalized_candidate and normalized_candidate not in candidates:
            candidates.append(normalized_candidate)
    return tuple(candidates)


def _probe_runtime_font_name(font_name: str) -> str | None:
    fc_match_binary = shutil.which("fc-match")
    resolved_name = font_name
    if fc_match_binary is not None:
        probe = subprocess.run(
            [fc_match_binary, "--format=%{family[0]}", font_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            resolved_name = probe.stdout.strip()
    if not _ffmpeg_can_render_font(resolved_name):
        return None
    return resolved_name


def _ffmpeg_can_render_font(font_name: str) -> bool:
    ffmpeg_binary = shutil.which("ffmpeg")
    if ffmpeg_binary is None:
        raise AssemblerError("required binary 'ffmpeg' was not found in PATH")

    with tempfile.TemporaryDirectory(prefix="subtitle_font_") as temp_dir:
        subtitles_path = Path(temp_dir) / "font_check.srt"
        subtitles_path.write_text(
            "1\n00:00:00,000 --> 00:00:00,800\nfont smoke test\n",
            encoding="utf-8",
        )
        command = [
            ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=0.8",
            "-vf",
            (
                f"subtitles='{_escape_filter_value_for_smoke(subtitles_path)}':"
                f"force_style='FontName={_escape_style_value_for_smoke(font_name)},FontSize=24'"
            ),
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    return completed.returncode == 0


def _escape_filter_value_for_smoke(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _escape_style_value_for_smoke(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _normalize_font_identity(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").split())


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


def _escape_drawtext_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def _escape_drawtext_text(value: str) -> str:
    return (
        _escape_drawtext_value(value)
        .replace("%", r"\%")
    )


def _escape_drawtext_expr(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", r"\,").replace(":", r"\:")


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


def _is_sfx_enabled(job: VideoJob) -> bool:
    if "sfx_enabled" in job.metadata:
        return _read_bool_metadata(job, "sfx_enabled", default=DEFAULT_SFX_ENABLED)
    return _read_bool_metadata(job, "edit_sfx_enabled", default=DEFAULT_SFX_ENABLED)


def _db_to_gain_factor(value_db: float) -> float:
    return 10 ** (value_db / 20.0)


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


__all__ = ["AssemblerError", "assemble_video_step", "resolve_runtime_subtitle_font"]
