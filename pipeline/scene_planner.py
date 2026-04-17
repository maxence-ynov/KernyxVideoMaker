from __future__ import annotations

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from job_paths import SCENE_MANIFEST_FILENAME, resolve_job_path
from schemas import JSONValue, VideoJob

SCENE_PIPELINE_VERSION = "multi_scene_v2"
DEFAULT_SCENE_TRANSITION = "cut"
DEFAULT_SCENE_TRANSITION_DURATION_SECONDS = 0.0
DEFAULT_AUDIO_FALLBACK_SECONDS = 12.0
DEFAULT_SCENE_MIN_DURATION_SECONDS = 1.1
DEFAULT_SCENE_MAX_DURATION_SECONDS = 6.0
DEFAULT_SCENE_MAX_CUES = 4
DEFAULT_OVERLAY_SIMILARITY_THRESHOLD = 0.60
DEFAULT_OVERLAY_TARGET_RATIO = 0.40
DEFAULT_OVERLAY_MAX_WORDS = 6
WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
OVERLAY_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?%?|[\w'-]+", flags=re.UNICODE)
PROOF_PHRASE_RE = re.compile(
    r"\d+(?:[.,]\d+)?(?:\s?(?:%|x|k|m|ms|s|min|mn|h|j))?(?:\s+[A-Za-zÀ-ÿ0-9%+-]+){0,2}",
    flags=re.IGNORECASE,
)
STRUCTURE_STEP_RE = re.compile(r"\b(?:step|etape)\s*\d+\b", flags=re.IGNORECASE)
STRUCTURE_COUNT_RE = re.compile(
    r"\b\d+\s+(?:erreur|erreurs|mistake|mistakes|etape|etapes|step|steps|cause|causes)\b",
    flags=re.IGNORECASE,
)
SRT_TIMESTAMP_RE = re.compile(
    r"(?P<hours>\d{2}):(?P<minutes>\d{2}):(?P<seconds>\d{2}),(?P<milliseconds>\d{3})"
)
VISUAL_MODE_ALIASES = {
    "auto": "auto",
    "dashboard": "dashboard",
    "dash": "dashboard",
    "workflow": "workflow",
    "flow": "workflow",
    "ui": "ui",
    "app": "ui",
    "product_ui": "ui",
    "screenshot": "screenshot",
    "pseudo_screenshot": "screenshot",
    "browser": "screenshot",
}
TOOL_KEYWORDS = (
    "zapier",
    "make",
    "make.com",
    "n8n",
    "notion",
    "airtable",
    "hubspot",
    "slack",
    "capcut",
    "openai",
    "chatgpt",
)
WORKFLOW_KEYWORDS = (
    "workflow",
    "pipeline",
    "automatisation",
    "automation",
    "trigger",
    "agent",
    "sync",
    "route",
)
INTERFACE_KEYWORDS = (
    "subtitle",
    "caption",
    "hook",
    "screen",
    "screenshot",
    "dashboard",
    "ui",
    "interface",
    "app",
)
PROOF_KEYWORDS = (
    "resultat",
    "result",
    "preuve",
    "proof",
    "gain",
    "retention",
    "%",
    "seconde",
    "secondes",
    "minute",
    "minutes",
)
OVERLAY_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "au",
        "aux",
        "avec",
        "ce",
        "ces",
        "comment",
        "dans",
        "de",
        "des",
        "du",
        "en",
        "et",
        "for",
        "il",
        "la",
        "le",
        "les",
        "mais",
        "ne",
        "pas",
        "pour",
        "que",
        "qui",
        "sur",
        "the",
        "this",
        "ton",
        "tu",
        "un",
        "une",
        "your",
    }
)
OVERLAY_LEADING_KEEP_WORDS = frozenset({"comment", "pourquoi", "stop"})
OVERLAY_WEAK_KEYWORDS = frozenset(
    {
        "automation",
        "automatisation",
        "business",
        "content",
        "creator",
        "dashboard",
        "erreur",
        "hook",
        "idea",
        "idee",
        "proof",
        "result",
        "resultat",
        "scene",
        "system",
        "systeme",
        "ui",
        "video",
        "workflow",
    }
)
OVERLAY_ACTION_WORDS = frozenset({"abonne", "abonne-toi", "check", "commente", "copie", "save", "teste"})


class ScenePlanningError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SubtitleCue:
    index: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    text: str
    word_count: int
    timing_source: str = "srt_file"

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "index": self.index,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "text": self.text,
            "word_count": self.word_count,
            "timing_source": self.timing_source,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SubtitleCue:
        text = str(payload.get("text", "")).strip()
        start_seconds = max(0.0, float(payload.get("start_seconds", 0.0)))
        end_seconds = max(start_seconds, float(payload.get("end_seconds", start_seconds)))
        duration_seconds = max(0.01, float(payload.get("duration_seconds", end_seconds - start_seconds)))
        word_count = max(1, int(payload.get("word_count", _count_words(text) or 1)))
        return cls(
            index=max(1, int(payload.get("index", 1))),
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            duration_seconds=duration_seconds,
            text=text,
            word_count=word_count,
            timing_source=str(payload.get("timing_source", "srt_file")).strip() or "srt_file",
        )


@dataclass(slots=True, frozen=True)
class SceneSeed:
    role: str
    source_text: str
    scene_type: str
    proof_mode: str
    visual_asset_mode: str
    keywords: list[str]
    word_count: int
    title_text: str
    highlight_text: str


@dataclass(slots=True, frozen=True)
class SceneSegment:
    scene_id: str
    index: int
    role: str
    scene_type: str
    source_text: str
    title_text: str
    highlight_text: str
    keywords: list[str]
    word_count: int
    proof_mode: str
    visual_asset_mode: str
    motion_effect: str
    transition_effect: str
    transition_duration_seconds: float
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    alignment_source: str = "weighted"
    cue_timeline: list[SubtitleCue] = field(default_factory=list)
    asset_path: str | None = None
    clip_path: str | None = None
    visual_theme: str | None = None
    visual_layout_variant: str | None = None
    overlay_role: str | None = None
    overlay_text: str | None = None
    subtitle_text: str | None = None
    visual_intensity_level: str | None = None
    primary_focus: str | None = None

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "scene_id": self.scene_id,
            "index": self.index,
            "role": self.role,
            "scene_type": self.scene_type,
            "source_text": self.source_text,
            "title_text": self.title_text,
            "highlight_text": self.highlight_text,
            "keywords": list(self.keywords),
            "word_count": self.word_count,
            "proof_mode": self.proof_mode,
            "visual_asset_mode": self.visual_asset_mode,
            "motion_effect": self.motion_effect,
            "transition_effect": self.transition_effect,
            "transition_duration_seconds": round(self.transition_duration_seconds, 3),
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "alignment_source": self.alignment_source,
            "cue_indices": [cue.index for cue in self.cue_timeline],
            "cue_timeline": [cue.to_metadata() for cue in self.cue_timeline],
            "asset_path": self.asset_path,
            "clip_path": self.clip_path,
            "visual_theme": self.visual_theme,
            "visual_layout_variant": self.visual_layout_variant,
            "overlay_role": self.overlay_role,
            "overlay_text": self.overlay_text,
            "subtitle_text": self.subtitle_text,
            "visual_intensity_level": self.visual_intensity_level,
            "primary_focus": self.primary_focus,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SceneSegment:
        raw_keywords = payload.get("keywords", [])
        raw_cues = payload.get("cue_timeline", [])
        cue_timeline = [
            SubtitleCue.from_mapping(item)
            for item in raw_cues
            if isinstance(item, Mapping)
        ]
        return cls(
            scene_id=str(payload.get("scene_id", "")),
            index=int(payload.get("index", 0)),
            role=str(payload.get("role", "body")),
            scene_type=str(payload.get("scene_type", "explain")),
            source_text=str(payload.get("source_text", "")).strip(),
            title_text=str(payload.get("title_text", "")).strip(),
            highlight_text=str(payload.get("highlight_text", "")).strip(),
            keywords=[
                str(item).strip()
                for item in raw_keywords
                if isinstance(item, str) and str(item).strip()
            ],
            word_count=max(1, int(payload.get("word_count", 1))),
            proof_mode=str(payload.get("proof_mode", "generic")).strip() or "generic",
            visual_asset_mode=str(payload.get("visual_asset_mode", "dashboard")).strip() or "dashboard",
            motion_effect=str(payload.get("motion_effect", "static")).strip() or "static",
            transition_effect=str(payload.get("transition_effect", DEFAULT_SCENE_TRANSITION)).strip()
            or DEFAULT_SCENE_TRANSITION,
            transition_duration_seconds=max(
                0.0,
                float(payload.get("transition_duration_seconds", DEFAULT_SCENE_TRANSITION_DURATION_SECONDS)),
            ),
            start_seconds=max(0.0, float(payload.get("start_seconds", 0.0))),
            end_seconds=max(0.0, float(payload.get("end_seconds", 0.0))),
            duration_seconds=max(0.05, float(payload.get("duration_seconds", 0.05))),
            alignment_source=str(payload.get("alignment_source", "weighted")).strip() or "weighted",
            cue_timeline=cue_timeline,
            asset_path=_read_optional_text(payload.get("asset_path")),
            clip_path=_read_optional_text(payload.get("clip_path")),
            visual_theme=_read_optional_text(payload.get("visual_theme")),
            visual_layout_variant=_read_optional_text(payload.get("visual_layout_variant")),
            overlay_role=_read_optional_text(payload.get("overlay_role")),
            overlay_text=_read_optional_text(payload.get("overlay_text")),
            subtitle_text=_read_optional_text(payload.get("subtitle_text")),
            visual_intensity_level=_read_optional_text(payload.get("visual_intensity_level")),
            primary_focus=_read_optional_text(payload.get("primary_focus")),
        )


@dataclass(slots=True, frozen=True)
class SceneManifest:
    version: str
    strategy: str
    total_duration_seconds: float
    primary_visual_asset_mode: str
    scenes: list[SceneSegment]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "version": self.version,
            "strategy": self.strategy,
            "total_duration_seconds": round(self.total_duration_seconds, 3),
            "primary_visual_asset_mode": self.primary_visual_asset_mode,
            "scene_count": len(self.scenes),
            "scenes": [scene.to_metadata() for scene in self.scenes],
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SceneManifest:
        raw_scenes = payload.get("scenes", [])
        if not isinstance(raw_scenes, list):
            raise ScenePlanningError("scene manifest must contain a 'scenes' array")
        scenes = [
            SceneSegment.from_mapping(item)
            for item in raw_scenes
            if isinstance(item, Mapping)
        ]
        if not scenes:
            raise ScenePlanningError("scene manifest is empty")
        return cls(
            version=str(payload.get("version", SCENE_PIPELINE_VERSION)).strip() or SCENE_PIPELINE_VERSION,
            strategy=str(payload.get("strategy", "script_sections")).strip() or "script_sections",
            total_duration_seconds=max(0.05, float(payload.get("total_duration_seconds", 0.05))),
            primary_visual_asset_mode=str(
                payload.get("primary_visual_asset_mode", scenes[0].visual_asset_mode)
            ).strip()
            or scenes[0].visual_asset_mode,
            scenes=scenes,
        )


async def plan_scenes_step(job: VideoJob, job_dir: Path) -> None:
    manifest = build_scene_manifest(job)
    save_scene_manifest(job, job_dir, manifest)


def build_scene_manifest(job: VideoJob) -> SceneManifest:
    raw_segments, strategy = _resolve_raw_segments(job)
    if not raw_segments:
        raise ScenePlanningError("scene planner could not resolve any script segments")

    scene_seeds = _build_scene_seeds(job, raw_segments)
    subtitle_cues = _load_subtitle_cues(job)
    if subtitle_cues and len(subtitle_cues) >= len(scene_seeds):
        draft_scenes = _build_cue_aligned_scenes(job, scene_seeds, subtitle_cues)
        strategy = f"{strategy}+subtitle_cues"
    else:
        total_duration_seconds = _resolve_total_duration_seconds(job, subtitle_cues)
        draft_scenes = _build_weighted_scenes(job, scene_seeds, total_duration_seconds)
        strategy = f"{strategy}+weighted"

    draft_scenes = _split_long_scenes(job, draft_scenes)
    draft_scenes = _merge_short_scenes(job, draft_scenes)
    finalized_scenes = _apply_scene_presentation_policy(_finalize_scene_sequence(job, draft_scenes))
    total_duration_seconds = _resolve_manifest_duration(job, finalized_scenes, subtitle_cues)
    primary_visual_asset_mode = _resolve_primary_visual_asset_mode(finalized_scenes)
    return SceneManifest(
        version=SCENE_PIPELINE_VERSION,
        strategy=strategy,
        total_duration_seconds=round(total_duration_seconds, 3),
        primary_visual_asset_mode=primary_visual_asset_mode,
        scenes=finalized_scenes,
    )


def load_scene_manifest(job: VideoJob) -> SceneManifest | None:
    raw_manifest = job.metadata.get("scene_manifest")
    if isinstance(raw_manifest, Mapping):
        return SceneManifest.from_mapping(raw_manifest)

    raw_path = job.metadata.get("scene_manifest_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    manifest_path = resolve_job_path(job, raw_path)
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ScenePlanningError(f"scene manifest is not a JSON object: {manifest_path}")
    return SceneManifest.from_mapping(payload)


def save_scene_manifest(job: VideoJob, job_dir: Path, manifest: SceneManifest) -> Path:
    manifest_path = (job_dir / SCENE_MANIFEST_FILENAME).resolve()
    manifest_path.write_text(
        json.dumps(manifest.to_metadata(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    job.set_metadata("scene_pipeline_version", manifest.version)
    job.set_metadata("scene_strategy", manifest.strategy)
    job.set_metadata("scene_count", len(manifest.scenes))
    job.set_metadata("scene_total_duration_seconds", round(manifest.total_duration_seconds, 3))
    job.set_metadata("scene_primary_visual_asset_mode", manifest.primary_visual_asset_mode)
    job.set_metadata("scene_manifest", manifest.to_metadata())
    job.set_metadata("scene_manifest_path", str(manifest_path))
    return manifest_path


def _resolve_raw_segments(job: VideoJob) -> tuple[list[tuple[str, str]], str]:
    sections = job.metadata.get("script_sections")
    if isinstance(sections, Mapping):
        raw_segments: list[tuple[str, str]] = []
        hook = _read_optional_text(sections.get("hook"))
        if hook:
            raw_segments.append(("hook", hook))

        points = sections.get("points")
        if isinstance(points, list):
            for raw_point in points:
                point = _read_optional_text(raw_point)
                if point:
                    raw_segments.append(("body", point))

        cta = _read_optional_text(sections.get("cta"))
        if cta:
            raw_segments.append(("cta", cta))

        if raw_segments:
            return raw_segments, "script_sections"

    script_text = _read_script_text(job)
    lines = [line.strip(" -") for line in script_text.splitlines() if line.strip()]
    if not lines:
        return [], "script_lines"
    if len(lines) == 1:
        return [("body", lines[0])], "script_lines"

    raw_segments: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if index == 0:
            role = "hook"
        elif index == len(lines) - 1:
            role = "cta"
        else:
            role = "body"
        raw_segments.append((role, line))
    return raw_segments, "script_lines"


def _build_scene_seeds(job: VideoJob, raw_segments: list[tuple[str, str]]) -> list[SceneSeed]:
    seeds: list[SceneSeed] = []
    for role, source_text in raw_segments:
        scene_type = _classify_scene_type(job, role=role, text=source_text)
        proof_mode = _resolve_scene_proof_mode(job, scene_type=scene_type, text=source_text)
        visual_asset_mode = _resolve_scene_visual_asset_mode(
            job,
            role=role,
            scene_type=scene_type,
            text=source_text,
        )
        seeds.append(
            SceneSeed(
                role=role,
                source_text=source_text,
                scene_type=scene_type,
                proof_mode=proof_mode,
                visual_asset_mode=visual_asset_mode,
                keywords=_extract_keywords(source_text, fallback=job.topic),
                word_count=max(1, _count_words(source_text)),
                title_text=_build_title_text(role, source_text),
                highlight_text=_build_highlight_text(source_text),
            )
        )
    return seeds


def _load_subtitle_cues(job: VideoJob) -> list[SubtitleCue]:
    raw_cues = job.metadata.get("subtitle_cues")
    if isinstance(raw_cues, list):
        cues = [
            SubtitleCue.from_mapping(item)
            for item in raw_cues
            if isinstance(item, Mapping)
        ]
        if cues:
            return cues

    subtitles_path = _resolve_subtitles_path(job)
    if subtitles_path is None or not subtitles_path.exists():
        return []
    return _parse_srt_cues(subtitles_path.read_text(encoding="utf-8"))


def _resolve_subtitles_path(job: VideoJob) -> Path | None:
    raw_path = job.get_file_path("subtitles")
    if raw_path:
        return resolve_job_path(job, raw_path)
    metadata_path = job.metadata.get("subtitles")
    if isinstance(metadata_path, str) and metadata_path.strip():
        return resolve_job_path(job, metadata_path)
    return None


def _parse_srt_cues(content: str) -> list[SubtitleCue]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", content.strip()) if block.strip()]
    cues: list[SubtitleCue] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        try:
            index = int(lines[0])
        except ValueError:
            index = len(cues) + 1
        raw_start, raw_end = [part.strip() for part in lines[1].split("-->", 1)]
        start_seconds = _parse_srt_timestamp(raw_start)
        end_seconds = _parse_srt_timestamp(raw_end)
        text = " ".join(lines[2:]).strip()
        if end_seconds <= start_seconds or not text:
            continue
        cues.append(
            SubtitleCue(
                index=index,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=round(end_seconds - start_seconds, 3),
                text=text,
                word_count=max(1, _count_words(text)),
                timing_source="srt_file",
            )
        )
    return cues


def _parse_srt_timestamp(value: str) -> float:
    match = SRT_TIMESTAMP_RE.fullmatch(value.strip())
    if match is None:
        raise ScenePlanningError(f"invalid SRT timestamp: {value}")
    hours = int(match.group("hours"))
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    milliseconds = int(match.group("milliseconds"))
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0


def _build_cue_aligned_scenes(
    job: VideoJob,
    scene_seeds: list[SceneSeed],
    subtitle_cues: list[SubtitleCue],
) -> list[SceneSegment]:
    if len(subtitle_cues) < len(scene_seeds):
        return _build_weighted_scenes(job, scene_seeds, _resolve_total_duration_seconds(job, subtitle_cues))

    total_weight = sum(_build_scene_weight(seed.role, seed.scene_type, seed.word_count) for seed in scene_seeds)
    total_cue_words = sum(cue.word_count for cue in subtitle_cues)
    scenes: list[SceneSegment] = []
    cue_cursor = 0

    for index, seed in enumerate(scene_seeds):
        remaining_scenes = len(scene_seeds) - index
        remaining_cues = len(subtitle_cues) - cue_cursor
        if remaining_cues <= 0:
            break
        if remaining_scenes == 1:
            cue_group = subtitle_cues[cue_cursor:]
            cue_cursor = len(subtitle_cues)
        else:
            weight = _build_scene_weight(seed.role, seed.scene_type, seed.word_count)
            target_words = max(1.0, total_cue_words * (weight / total_weight))
            cue_group = [subtitle_cues[cue_cursor]]
            cue_cursor += 1
            group_words = cue_group[0].word_count
            while cue_cursor < len(subtitle_cues):
                cues_left_after_current = len(subtitle_cues) - cue_cursor
                min_cues_reserved = remaining_scenes - 1
                if cues_left_after_current <= min_cues_reserved:
                    break
                if group_words >= target_words:
                    break
                cue_group.append(subtitle_cues[cue_cursor])
                group_words += subtitle_cues[cue_cursor].word_count
                cue_cursor += 1

        scenes.append(_build_scene_from_cues(seed, cue_group))

    if cue_cursor < len(subtitle_cues) and scenes:
        trailing_cues = subtitle_cues[cue_cursor:]
        last_scene = scenes[-1]
        merged_cues = [*last_scene.cue_timeline, *trailing_cues]
        scenes[-1] = _build_scene_from_cues(
            SceneSeed(
                role=last_scene.role,
                source_text=f"{last_scene.source_text} {' '.join(cue.text for cue in trailing_cues)}".strip(),
                scene_type=last_scene.scene_type,
                proof_mode=last_scene.proof_mode,
                visual_asset_mode=last_scene.visual_asset_mode,
                keywords=_merge_keywords(last_scene.keywords, _extract_keywords(' '.join(cue.text for cue in trailing_cues), fallback=last_scene.source_text)),
                word_count=sum(cue.word_count for cue in merged_cues),
                title_text=_build_title_text(last_scene.role, f"{last_scene.source_text} {' '.join(cue.text for cue in trailing_cues)}"),
                highlight_text=_build_highlight_text(f"{last_scene.source_text} {' '.join(cue.text for cue in trailing_cues)}"),
            ),
            merged_cues,
            alignment_source=f"{last_scene.alignment_source}+rebalance",
        )

    return scenes


def _build_weighted_scenes(
    job: VideoJob,
    scene_seeds: list[SceneSeed],
    total_duration_seconds: float,
) -> list[SceneSegment]:
    weights = [
        _build_scene_weight(seed.role, seed.scene_type, seed.word_count)
        for seed in scene_seeds
    ]
    durations = _assign_scene_durations(total_duration_seconds, weights)
    scenes: list[SceneSegment] = []
    elapsed = 0.0
    for index, (seed, duration_seconds) in enumerate(zip(scene_seeds, durations, strict=True)):
        start_seconds = elapsed
        end_seconds = total_duration_seconds if index == len(scene_seeds) - 1 else elapsed + duration_seconds
        scenes.append(
            SceneSegment(
                scene_id="",
                index=index,
                role=seed.role,
                scene_type=seed.scene_type,
                source_text=seed.source_text,
                title_text=seed.title_text,
                highlight_text=seed.highlight_text,
                keywords=list(seed.keywords),
                word_count=seed.word_count,
                proof_mode=seed.proof_mode,
                visual_asset_mode=seed.visual_asset_mode,
                motion_effect="static",
                transition_effect=DEFAULT_SCENE_TRANSITION,
                transition_duration_seconds=DEFAULT_SCENE_TRANSITION_DURATION_SECONDS,
                start_seconds=round(start_seconds, 3),
                end_seconds=round(end_seconds, 3),
                duration_seconds=round(max(0.05, end_seconds - start_seconds), 3),
                alignment_source="weighted",
            )
        )
        elapsed = end_seconds
    return scenes


def _build_scene_from_cues(
    seed: SceneSeed,
    cue_group: list[SubtitleCue],
    *,
    alignment_source: str = "subtitle_cues",
) -> SceneSegment:
    if not cue_group:
        raise ScenePlanningError("scene cue group must not be empty")
    source_text = " ".join(cue.text for cue in cue_group).strip()
    source_text = source_text or seed.source_text
    keywords = _extract_keywords(source_text, fallback=seed.source_text)
    start_seconds = cue_group[0].start_seconds
    end_seconds = cue_group[-1].end_seconds
    return SceneSegment(
        scene_id="",
        index=0,
        role=seed.role,
        scene_type=seed.scene_type,
        source_text=source_text,
        title_text=_build_title_text(seed.role, source_text),
        highlight_text=_build_highlight_text(source_text),
        keywords=keywords or list(seed.keywords),
        word_count=max(1, sum(cue.word_count for cue in cue_group)),
        proof_mode=seed.proof_mode,
        visual_asset_mode=seed.visual_asset_mode,
        motion_effect="static",
        transition_effect=DEFAULT_SCENE_TRANSITION,
        transition_duration_seconds=DEFAULT_SCENE_TRANSITION_DURATION_SECONDS,
        start_seconds=round(start_seconds, 3),
        end_seconds=round(end_seconds, 3),
        duration_seconds=round(max(0.05, end_seconds - start_seconds), 3),
        alignment_source=alignment_source,
        cue_timeline=list(cue_group),
    )


def _split_long_scenes(job: VideoJob, scenes: list[SceneSegment]) -> list[SceneSegment]:
    if not _read_bool_metadata(job, "scene_split_enabled", default=True):
        return scenes

    output: list[SceneSegment] = []
    for scene in scenes:
        cue_count = len(scene.cue_timeline)
        if scene.role not in {"body", "hook"} or cue_count <= 1:
            output.append(scene)
            continue

        max_duration_seconds = _resolve_scene_max_duration_seconds(job, role=scene.role)
        max_cues = _resolve_scene_max_cues(job, role=scene.role)
        if scene.duration_seconds <= max_duration_seconds and cue_count <= max_cues:
            output.append(scene)
            continue

        chunk_count = max(
            2,
            math.ceil(scene.duration_seconds / max_duration_seconds),
            math.ceil(cue_count / max_cues),
        )
        chunk_count = min(cue_count, chunk_count)
        for chunk_index, cue_chunk in enumerate(_chunk_cue_timeline(scene.cue_timeline, chunk_count)):
            chunk_text = " ".join(cue.text for cue in cue_chunk).strip() or scene.source_text
            chunk_role = scene.role if scene.role != "hook" or chunk_index == 0 else "body"
            chunk_scene_type = _classify_scene_type(job, role=chunk_role, text=chunk_text)
            chunk_proof_mode = _resolve_scene_proof_mode(job, scene_type=chunk_scene_type, text=chunk_text)
            chunk_visual_mode = _resolve_scene_visual_asset_mode(
                job,
                role=chunk_role,
                scene_type=chunk_scene_type,
                text=chunk_text,
            )
            output.append(
                SceneSegment(
                    scene_id="",
                    index=0,
                    role=chunk_role,
                    scene_type=chunk_scene_type,
                    source_text=chunk_text,
                    title_text=_build_title_text(chunk_role, chunk_text),
                    highlight_text=_build_highlight_text(chunk_text),
                    keywords=_extract_keywords(chunk_text, fallback=scene.source_text),
                    word_count=max(1, sum(cue.word_count for cue in cue_chunk)),
                    proof_mode=chunk_proof_mode,
                    visual_asset_mode=chunk_visual_mode,
                    motion_effect="static",
                    transition_effect=DEFAULT_SCENE_TRANSITION,
                    transition_duration_seconds=DEFAULT_SCENE_TRANSITION_DURATION_SECONDS,
                    start_seconds=round(cue_chunk[0].start_seconds, 3),
                    end_seconds=round(cue_chunk[-1].end_seconds, 3),
                    duration_seconds=round(max(0.05, cue_chunk[-1].end_seconds - cue_chunk[0].start_seconds), 3),
                    alignment_source=f"{scene.alignment_source}+split",
                    cue_timeline=list(cue_chunk),
                )
            )
    return output


def _merge_short_scenes(job: VideoJob, scenes: list[SceneSegment]) -> list[SceneSegment]:
    if not _read_bool_metadata(job, "scene_merge_enabled", default=True):
        return scenes

    min_duration_seconds = _read_float_metadata(
        job,
        "scene_min_duration_seconds",
        DEFAULT_SCENE_MIN_DURATION_SECONDS,
        minimum=0.6,
        maximum=3.0,
    )

    output: list[SceneSegment] = []
    index = 0
    while index < len(scenes):
        scene = scenes[index]
        if (
            scene.role == "body"
            and scene.duration_seconds < min_duration_seconds
            and index + 1 < len(scenes)
            and scenes[index + 1].role == "body"
        ):
            output.append(_merge_scene_pair(scene, scenes[index + 1]))
            index += 2
            continue
        if (
            scene.role == "body"
            and scene.duration_seconds < min_duration_seconds
            and output
            and output[-1].role == "body"
        ):
            output[-1] = _merge_scene_pair(output[-1], scene)
            index += 1
            continue
        output.append(scene)
        index += 1
    return output


def _merge_scene_pair(left: SceneSegment, right: SceneSegment) -> SceneSegment:
    merged_cues = [*left.cue_timeline, *right.cue_timeline]
    merged_text = " ".join(part for part in (left.source_text, right.source_text) if part).strip()
    merged_role = right.role if right.role == "cta" else left.role
    merged_scene_type = left.scene_type if left.scene_type == right.scene_type else "explain"
    merged_visual_mode = (
        right.visual_asset_mode
        if right.scene_type in {"workflow", "proof", "interface"}
        else left.visual_asset_mode
    )
    merged_proof_mode = left.proof_mode if left.proof_mode != "generic" else right.proof_mode
    start_seconds = merged_cues[0].start_seconds if merged_cues else left.start_seconds
    end_seconds = merged_cues[-1].end_seconds if merged_cues else right.end_seconds
    return SceneSegment(
        scene_id="",
        index=0,
        role=merged_role,
        scene_type=merged_scene_type,
        source_text=merged_text,
        title_text=_build_title_text(merged_role, merged_text),
        highlight_text=_build_highlight_text(merged_text),
        keywords=_merge_keywords(left.keywords, right.keywords),
        word_count=max(1, left.word_count + right.word_count),
        proof_mode=merged_proof_mode,
        visual_asset_mode=merged_visual_mode,
        motion_effect="static",
        transition_effect=DEFAULT_SCENE_TRANSITION,
        transition_duration_seconds=DEFAULT_SCENE_TRANSITION_DURATION_SECONDS,
        start_seconds=round(start_seconds, 3),
        end_seconds=round(end_seconds, 3),
        duration_seconds=round(max(0.05, end_seconds - start_seconds), 3),
        alignment_source=f"{left.alignment_source}+merge",
        cue_timeline=merged_cues,
    )


def _chunk_cue_timeline(cues: list[SubtitleCue], chunk_count: int) -> list[list[SubtitleCue]]:
    if chunk_count <= 1 or len(cues) <= 1:
        return [list(cues)]

    chunks: list[list[SubtitleCue]] = []
    cursor = 0
    remaining_chunks = chunk_count
    while cursor < len(cues):
        remaining_cues = len(cues) - cursor
        chunk_size = max(1, math.ceil(remaining_cues / remaining_chunks))
        chunks.append(cues[cursor : cursor + chunk_size])
        cursor += chunk_size
        remaining_chunks -= 1
    return chunks


def _finalize_scene_sequence(job: VideoJob, scenes: list[SceneSegment]) -> list[SceneSegment]:
    finalized: list[SceneSegment] = []
    previous_motion: str | None = None
    for index, scene in enumerate(scenes):
        motion_effect = _resolve_motion_effect(index=index, scene_type=scene.scene_type, previous_motion=previous_motion)
        transition_effect = _resolve_transition_effect(job, scene_type=scene.scene_type, index=index)
        duration_seconds = max(0.05, scene.end_seconds - scene.start_seconds)
        transition_duration_seconds = (
            _resolve_transition_duration_seconds(job, duration_seconds)
            if transition_effect != "cut"
            else 0.0
        )
        finalized_scene = replace(
            scene,
            scene_id=f"scene_{index + 1:02d}",
            index=index,
            motion_effect=motion_effect,
            transition_effect=transition_effect,
            transition_duration_seconds=transition_duration_seconds,
            duration_seconds=round(duration_seconds, 3),
        )
        finalized.append(finalized_scene)
        previous_motion = motion_effect
    return finalized


def _apply_scene_presentation_policy(scenes: list[SceneSegment]) -> list[SceneSegment]:
    prepared_scenes: list[SceneSegment] = []
    overlay_candidates: list[tuple[int, int]] = []

    for index, scene in enumerate(scenes):
        subtitle_text = compute_scene_subtitle_text(scene)
        overlay_role = compute_overlay_role(scene)
        overlay_text = _resolve_overlay_text(scene, overlay_role=overlay_role, subtitle_text=subtitle_text)
        visual_intensity_level = _resolve_visual_intensity_level(scene, overlay_text=overlay_text)
        primary_focus = _resolve_primary_focus(scene, overlay_text=overlay_text)
        prepared_scene = replace(
            scene,
            subtitle_text=subtitle_text or None,
            overlay_role=overlay_role,
            overlay_text=overlay_text,
            visual_intensity_level=visual_intensity_level,
            primary_focus=primary_focus,
        )
        prepared_scenes.append(prepared_scene)
        if overlay_text:
            overlay_candidates.append((index, _overlay_scene_priority(prepared_scene)))

    if not overlay_candidates:
        return prepared_scenes

    overlay_budget = _resolve_overlay_scene_budget(len(prepared_scenes))
    retained_overlay_indexes = {
        index
        for index, _priority in sorted(
            overlay_candidates,
            key=lambda item: (-item[1], item[0]),
        )[:overlay_budget]
    }

    finalized_scenes: list[SceneSegment] = []
    for index, scene in enumerate(prepared_scenes):
        if index in retained_overlay_indexes or not scene.overlay_text:
            finalized_scenes.append(scene)
            continue
        finalized_scenes.append(
            replace(
                scene,
                overlay_text=None,
                primary_focus=_resolve_primary_focus(scene, overlay_text=None),
                visual_intensity_level=_resolve_visual_intensity_level(scene, overlay_text=None),
            )
        )
    return finalized_scenes


def _resolve_manifest_duration(
    job: VideoJob,
    scenes: list[SceneSegment],
    subtitle_cues: list[SubtitleCue],
) -> float:
    if scenes:
        return round(scenes[-1].end_seconds, 3)
    return _resolve_total_duration_seconds(job, subtitle_cues)


def compute_scene_subtitle_text(scene: SceneSegment) -> str:
    if scene.subtitle_text is not None:
        return scene.subtitle_text
    if scene.cue_timeline:
        return " ".join(cue.text.strip() for cue in scene.cue_timeline if cue.text.strip()).strip()
    return " ".join(scene.source_text.split()).strip()


def _read_script_text(job: VideoJob) -> str:
    value = job.metadata.get("script_text")
    if isinstance(value, str) and value.strip():
        return value.strip()

    script_path = job.get_file_path("script")
    if script_path:
        resolved = resolve_job_path(job, script_path)
        if resolved.exists():
            content = resolved.read_text(encoding="utf-8").strip()
            if content:
                return content

    raise ScenePlanningError("scene planner requires script_text metadata or a script artifact")


def _resolve_total_duration_seconds(job: VideoJob, subtitle_cues: list[SubtitleCue] | None = None) -> float:
    if subtitle_cues:
        return round(subtitle_cues[-1].end_seconds, 3)
    for key in ("voice_duration_seconds", "estimated_duration_seconds", "target_duration_seconds"):
        value = job.metadata.get(key)
        if isinstance(value, int | float) and float(value) > 0:
            return round(float(value), 3)
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value.strip())
            except ValueError:
                continue
            if parsed > 0:
                return round(parsed, 3)
    return DEFAULT_AUDIO_FALLBACK_SECONDS


def compute_overlay_role(scene: SceneSegment) -> str | None:
    source_text = " ".join((scene.source_text or scene.highlight_text or scene.title_text).split()).lower()
    if STRUCTURE_STEP_RE.search(source_text) or STRUCTURE_COUNT_RE.search(source_text):
        return "structure"
    if "avant apres" in source_text or "avant/apres" in source_text:
        return "structure"
    if scene.role == "hook" or scene.scene_type == "hook":
        return "hook"
    if scene.scene_type == "proof":
        return "proof"
    if scene.role == "cta" or scene.scene_type == "cta":
        return "keyword"
    if scene.scene_type in {"workflow", "interface"} and _select_overlay_keyword(scene) is not None:
        return "keyword"
    return None


def _classify_scene_type(job: VideoJob, *, role: str, text: str) -> str:
    lowered_text = text.lower()
    profile = _read_optional_text(job.metadata.get("script_profile")) or ""

    if role == "hook":
        return "hook"
    if role == "cta":
        return "cta"
    if any(keyword in lowered_text for keyword in TOOL_KEYWORDS + WORKFLOW_KEYWORDS):
        return "workflow"
    if any(keyword in lowered_text for keyword in PROOF_KEYWORDS) or re.search(r"\d", lowered_text):
        return "proof"
    if profile in {"hooks", "subtitles"} or any(keyword in lowered_text for keyword in INTERFACE_KEYWORDS):
        return "interface"
    return "explain"


def _resolve_scene_proof_mode(job: VideoJob, *, scene_type: str, text: str) -> str:
    explicit_mode = _read_optional_text(job.metadata.get("script_proof_mode"))
    if explicit_mode and explicit_mode != "auto":
        return explicit_mode.lower()

    lowered_text = text.lower()
    if scene_type == "workflow" or any(keyword in lowered_text for keyword in TOOL_KEYWORDS):
        return "tool"
    if scene_type == "proof" or re.search(r"\d", lowered_text):
        return "result"
    if "error" in lowered_text or "erreur" in lowered_text or "mistake" in lowered_text:
        return "field_error"
    if scene_type == "cta":
        return "generic"
    return "example"


def _resolve_scene_visual_asset_mode(
    job: VideoJob,
    *,
    role: str,
    scene_type: str,
    text: str,
) -> str:
    explicit_mode = _read_optional_text(job.metadata.get("visual_asset_mode"))
    if explicit_mode:
        normalized_explicit_mode = VISUAL_MODE_ALIASES.get(explicit_mode.lower())
        if normalized_explicit_mode and normalized_explicit_mode != "auto":
            return normalized_explicit_mode

    lowered_text = text.lower()
    if scene_type == "workflow":
        return "workflow"
    if scene_type == "proof":
        return "dashboard"
    if scene_type == "interface":
        return "screenshot"
    if role == "cta":
        return "ui"
    if role == "hook":
        return "dashboard" if "dashboard" in lowered_text else "screenshot"
    return "ui"


def _resolve_overlay_text(
    scene: SceneSegment,
    *,
    overlay_role: str | None,
    subtitle_text: str,
) -> str | None:
    if not overlay_role:
        return None

    role_builders = {
        "hook": _build_hook_overlay_text,
        "keyword": _build_keyword_overlay_text,
        "proof": _build_proof_overlay_text,
        "structure": _build_structure_overlay_text,
    }
    builder = role_builders.get(overlay_role)
    overlay_text = builder(scene) if builder is not None else None
    if not overlay_text:
        return None

    if _compute_text_similarity(overlay_text, subtitle_text) > DEFAULT_OVERLAY_SIMILARITY_THRESHOLD:
        overlay_text = _rewrite_overlay_text(scene, preferred_mode="hook_or_keyword")

    if overlay_text and _compute_text_similarity(overlay_text, subtitle_text) > DEFAULT_OVERLAY_SIMILARITY_THRESHOLD:
        overlay_text = None

    return overlay_text


def _rewrite_overlay_text(scene: SceneSegment, *, preferred_mode: str) -> str | None:
    if preferred_mode != "hook_or_keyword":
        return None
    for candidate in (
        _build_keyword_overlay_text(scene),
        _build_hook_overlay_text(scene),
        _build_structure_overlay_text(scene),
    ):
        if candidate:
            return candidate
    return None


def _build_hook_overlay_text(scene: SceneSegment) -> str | None:
    for candidate in (
        scene.highlight_text,
        scene.title_text,
        _extract_first_clause(scene.source_text),
        scene.source_text,
    ):
        compact = _compact_overlay_text(candidate)
        if compact:
            return compact
    return _build_keyword_overlay_text(scene)


def _build_keyword_overlay_text(scene: SceneSegment) -> str | None:
    keyword = _select_overlay_keyword(scene)
    if keyword:
        return _sanitize_overlay_text(keyword)
    return None


def _build_proof_overlay_text(scene: SceneSegment) -> str | None:
    for candidate in (
        scene.highlight_text,
        compute_scene_subtitle_text(scene),
        scene.source_text,
        scene.title_text,
    ):
        proof_phrase = _extract_proof_phrase(candidate)
        if proof_phrase:
            return proof_phrase

    keyword = _select_overlay_keyword(scene)
    if keyword:
        return _sanitize_overlay_text(keyword)
    return None


def _build_structure_overlay_text(scene: SceneSegment) -> str | None:
    for candidate in (scene.highlight_text, scene.title_text, scene.source_text):
        structure_phrase = _extract_structure_phrase(candidate)
        if structure_phrase:
            return structure_phrase
    return _build_keyword_overlay_text(scene)


def _select_overlay_keyword(scene: SceneSegment) -> str | None:
    raw_candidates: list[str] = []
    raw_candidates.extend(scene.keywords)
    raw_candidates.extend(_extract_overlay_tokens(scene.highlight_text))
    raw_candidates.extend(_extract_overlay_tokens(scene.title_text))
    raw_candidates.extend(_extract_overlay_tokens(scene.source_text))

    chosen_tokens: list[str] = []
    seen_normalized: set[str] = set()
    for candidate in raw_candidates:
        normalized = _normalize_overlay_token(candidate)
        if not normalized or normalized in seen_normalized:
            continue
        seen_normalized.add(normalized)
        if normalized in OVERLAY_STOP_WORDS or normalized in OVERLAY_WEAK_KEYWORDS:
            continue
        chosen_tokens.append(candidate)
        if len(chosen_tokens) == 2:
            break

    if not chosen_tokens:
        for candidate in raw_candidates:
            normalized = _normalize_overlay_token(candidate)
            if normalized and normalized not in OVERLAY_STOP_WORDS:
                chosen_tokens.append(candidate)
                break

    if not chosen_tokens:
        return None
    return _sanitize_overlay_text(" ".join(chosen_tokens[:2]))


def _extract_first_clause(text: str) -> str:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return ""
    for separator in (".", "!", "?", ";", ":", ","):
        if separator in normalized:
            return normalized.split(separator, 1)[0].strip()
    return normalized


def _extract_proof_phrase(text: str) -> str | None:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return None
    for match in PROOF_PHRASE_RE.finditer(normalized):
        phrase = _sanitize_overlay_text(match.group(0))
        if phrase:
            return phrase
    return None


def _extract_structure_phrase(text: str) -> str | None:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return None

    for pattern in (STRUCTURE_STEP_RE, STRUCTURE_COUNT_RE):
        match = pattern.search(normalized)
        if match is not None:
            return _sanitize_overlay_text(match.group(0))

    lowered = normalized.lower()
    if "avant" in lowered and "apres" in lowered:
        return "Avant Apres"
    if "erreur" in lowered or "mistake" in lowered:
        return "Erreur critique"
    if "checklist" in lowered:
        return "Checklist"
    return None


def _compact_overlay_text(text: str) -> str | None:
    tokens = _extract_overlay_tokens(text)
    if not tokens:
        return None

    compact_tokens: list[str] = []
    for index, token in enumerate(tokens):
        normalized = _normalize_overlay_token(token)
        if not normalized:
            continue
        if index == 0 and normalized in OVERLAY_LEADING_KEEP_WORDS:
            compact_tokens.append(token)
            continue
        if normalized in OVERLAY_STOP_WORDS:
            continue
        compact_tokens.append(token)
        if len(compact_tokens) == DEFAULT_OVERLAY_MAX_WORDS:
            break

    if not compact_tokens:
        compact_tokens = tokens[:DEFAULT_OVERLAY_MAX_WORDS]

    return _sanitize_overlay_text(" ".join(compact_tokens))


def _sanitize_overlay_text(text: str) -> str | None:
    normalized = " ".join(text.replace("/", " ").replace(">", " > ").split()).strip()
    if not normalized:
        return None

    cleaned_tokens: list[str] = []
    for token in normalized.split():
        cleaned = re.sub(r"[^\w%+><=.,-]+", "", token, flags=re.UNICODE).strip(".,;:!?")
        if not cleaned:
            continue
        cleaned_tokens.append(cleaned)
        if len(cleaned_tokens) == DEFAULT_OVERLAY_MAX_WORDS:
            break

    if not cleaned_tokens:
        return None
    sanitized = " ".join(cleaned_tokens).strip()
    return sanitized or None


def _extract_overlay_tokens(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(0) for match in OVERLAY_TOKEN_RE.finditer(" ".join(text.split()))]


def _normalize_overlay_token(token: str) -> str:
    normalized = token.lower().strip("'-")
    normalized = re.sub(r"[^\w%+]+", "", normalized, flags=re.UNICODE)
    return normalized


def _compute_text_similarity(left: str, right: str) -> float:
    normalized_left = " ".join(_normalize_overlay_token(token) for token in _extract_overlay_tokens(left))
    normalized_right = " ".join(_normalize_overlay_token(token) for token in _extract_overlay_tokens(right))
    normalized_left = " ".join(part for part in normalized_left.split() if part)
    normalized_right = " ".join(part for part in normalized_right.split() if part)
    if not normalized_left or not normalized_right:
        return 0.0

    left_tokens = set(normalized_left.split())
    right_tokens = set(normalized_right.split())
    overlap_ratio = len(left_tokens.intersection(right_tokens)) / max(1, max(len(left_tokens), len(right_tokens)))
    sequence_ratio = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()
    containment_ratio = 1.0 if normalized_left in normalized_right or normalized_right in normalized_left else 0.0
    if min(len(left_tokens), len(right_tokens)) <= 2 and max(len(left_tokens), len(right_tokens)) >= 4:
        sequence_ratio = overlap_ratio
        containment_ratio = overlap_ratio
    return max(overlap_ratio, sequence_ratio, containment_ratio)


def _resolve_visual_intensity_level(scene: SceneSegment, *, overlay_text: str | None) -> str:
    if scene.role == "hook" or scene.scene_type == "hook":
        return "high"
    if scene.scene_type == "proof":
        return "high" if overlay_text else "medium"
    if scene.role == "cta" or scene.scene_type == "cta":
        return "low"
    if overlay_text and scene.scene_type in {"workflow", "interface"}:
        return "medium"
    return "low"


def _resolve_primary_focus(scene: SceneSegment, *, overlay_text: str | None) -> str:
    if overlay_text:
        return "overlay_text"
    if scene.scene_type in {"proof", "workflow", "interface"}:
        return "visual_proof"
    if compute_scene_subtitle_text(scene):
        return "subtitles"
    return "background"


def _overlay_scene_priority(scene: SceneSegment) -> int:
    priority = 0
    if scene.role == "hook" or scene.scene_type == "hook":
        priority += 100
    elif scene.scene_type == "proof":
        priority += 80
    elif scene.role == "cta" or scene.scene_type == "cta":
        priority += 48
    elif scene.overlay_role == "structure":
        priority += 42
    elif scene.overlay_role == "keyword":
        priority += 32

    if scene.overlay_text:
        priority += 8
        if len(scene.overlay_text.split()) <= 4:
            priority += 4
        if any(character.isdigit() for character in scene.overlay_text):
            priority += 6
        if any(word.lower() in OVERLAY_ACTION_WORDS for word in scene.overlay_text.split()):
            priority -= 6
    return priority


def _resolve_overlay_scene_budget(scene_count: int) -> int:
    if scene_count <= 1:
        return scene_count
    target = max(1, math.ceil(scene_count * DEFAULT_OVERLAY_TARGET_RATIO))
    if scene_count >= 3:
        target = max(2, target)
    return min(scene_count, target)


def _build_scene_weight(role: str, scene_type: str, word_count: int) -> float:
    weight = max(1.0, float(word_count))
    if role == "hook":
        weight *= 1.16
    elif role == "cta":
        weight *= 0.88
    if scene_type == "proof":
        weight *= 1.08
    elif scene_type == "workflow":
        weight *= 1.06
    return weight


def _assign_scene_durations(total_duration_seconds: float, weights: list[float]) -> list[float]:
    if not weights:
        return []

    available_duration = max(0.3, total_duration_seconds)
    scene_count = len(weights)
    average_duration = available_duration / scene_count
    minimum_duration = min(1.25, max(0.55, average_duration * 0.6))
    maximum_duration = max(minimum_duration, average_duration * 1.8)

    total_weight = sum(max(0.1, weight) for weight in weights)
    durations = [available_duration * (max(0.1, weight) / total_weight) for weight in weights]
    durations = [_clamp(duration, minimum_duration, maximum_duration) for duration in durations]

    difference = available_duration - sum(durations)
    for _ in range(12):
        if abs(difference) <= 0.001:
            break
        adjustable_indexes = [
            index
            for index, duration in enumerate(durations)
            if (difference > 0 and duration < maximum_duration - 0.001)
            or (difference < 0 and duration > minimum_duration + 0.001)
        ]
        if not adjustable_indexes:
            break
        adjustable_weight_total = sum(weights[index] for index in adjustable_indexes)
        for index in adjustable_indexes:
            delta = difference * (weights[index] / adjustable_weight_total)
            durations[index] = _clamp(durations[index] + delta, minimum_duration, maximum_duration)
        difference = available_duration - sum(durations)

    durations[-1] += available_duration - sum(durations)
    return [round(max(0.3, duration), 3) for duration in durations]


def _resolve_motion_effect(*, index: int, scene_type: str, previous_motion: str | None) -> str:
    if scene_type == "hook":
        candidates = ("zoom_in", "pan_up", "pan_right")
    elif scene_type == "cta":
        candidates = ("pan_up", "zoom_in", "pan_left")
    elif scene_type == "proof":
        candidates = ("pan_up", "pan_right", "pan_left")
    elif scene_type == "workflow":
        candidates = ("pan_right", "pan_left", "pan_up") if index % 2 == 0 else ("pan_left", "pan_right", "pan_up")
    elif scene_type == "interface":
        candidates = ("pan_left", "pan_right", "zoom_in") if index % 2 == 0 else ("pan_right", "pan_left", "zoom_in")
    else:
        candidates = ("pan_right", "pan_left", "pan_up") if index % 2 == 0 else ("pan_left", "pan_right", "pan_up")

    for candidate in candidates:
        if candidate != previous_motion:
            return candidate
    return candidates[0]


def _resolve_transition_effect(job: VideoJob, *, scene_type: str, index: int) -> str:
    raw_value = job.metadata.get("scene_transition_effect", DEFAULT_SCENE_TRANSITION)
    if isinstance(raw_value, str) and raw_value.strip():
        normalized = raw_value.strip().lower()
        if normalized in {"cut", "fade"}:
            return normalized
    return "cut" if index > 0 else DEFAULT_SCENE_TRANSITION


def _resolve_transition_duration_seconds(job: VideoJob, scene_duration_seconds: float) -> float:
    raw_value = job.metadata.get(
        "scene_transition_duration_seconds",
        DEFAULT_SCENE_TRANSITION_DURATION_SECONDS,
    )
    if isinstance(raw_value, int | float):
        value = float(raw_value)
    elif isinstance(raw_value, str) and raw_value.strip():
        try:
            value = float(raw_value.strip())
        except ValueError:
            value = DEFAULT_SCENE_TRANSITION_DURATION_SECONDS
    else:
        value = DEFAULT_SCENE_TRANSITION_DURATION_SECONDS
    return round(_clamp(value, 0.0, min(0.4, scene_duration_seconds / 3.0)), 3)


def _resolve_scene_max_duration_seconds(job: VideoJob, *, role: str) -> float:
    if role == "hook":
        return _read_float_metadata(
            job,
            "scene_hook_max_duration_seconds",
            4.2,
            minimum=1.5,
            maximum=8.0,
        )
    return _read_float_metadata(
        job,
        "scene_max_duration_seconds",
        DEFAULT_SCENE_MAX_DURATION_SECONDS,
        minimum=2.0,
        maximum=12.0,
    )


def _resolve_scene_max_cues(job: VideoJob, *, role: str) -> int:
    if role == "hook":
        return _read_int_metadata(
            job,
            "scene_hook_max_cues",
            3,
            minimum=2,
            maximum=6,
        )
    return _read_int_metadata(
        job,
        "scene_max_cues_per_scene",
        DEFAULT_SCENE_MAX_CUES,
        minimum=2,
        maximum=8,
    )


def _resolve_primary_visual_asset_mode(scenes: list[SceneSegment]) -> str:
    counts: dict[str, int] = {}
    for scene in scenes:
        counts[scene.visual_asset_mode] = counts.get(scene.visual_asset_mode, 0) + 1
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _build_title_text(role: str, text: str) -> str:
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return role.upper()
    if role == "hook":
        return normalized[:72]
    if role == "cta":
        return normalized[:64]
    return normalized[:68]


def _build_highlight_text(text: str) -> str:
    normalized = " ".join(text.split()).strip()
    if len(normalized) <= 84:
        return normalized
    truncated = normalized[:81].rstrip(" ,.;:!?")
    return f"{truncated}..."


def _extract_keywords(text: str, *, fallback: str) -> list[str]:
    keywords: list[str] = []
    source = f"{text} {fallback}".lower()
    for match in WORD_RE.findall(source):
        token = match.lower()
        if token.isdigit() or len(token) <= 2:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) == 5:
            break
    return keywords


def _merge_keywords(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*left, *right]:
        normalized = " ".join(item.split()).strip().lower()
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged[:5]


def _count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _read_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        normalized = " ".join(value.split()).strip()
        return normalized or None
    return None


def _read_bool_metadata(job: VideoJob, key: str, *, default: bool) -> bool:
    value = job.metadata.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip():
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


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


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


__all__ = [
    "SCENE_MANIFEST_FILENAME",
    "SCENE_PIPELINE_VERSION",
    "SceneManifest",
    "ScenePlanningError",
    "SceneSegment",
    "SubtitleCue",
    "build_scene_manifest",
    "compute_overlay_role",
    "compute_scene_subtitle_text",
    "load_scene_manifest",
    "plan_scenes_step",
    "save_scene_manifest",
]
