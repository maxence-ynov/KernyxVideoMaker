from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
import shutil

from job_paths import (
    SCENE_ASSETS_DIRNAME,
    build_scene_asset_filename,
    build_source_media_artifact_filename,
    resolve_job_path,
)
from pipeline.diversity_memory import DiversityCandidate, DiversityDecision, load_diversity_memory
from pipeline.scene_asset_selector import (
    AssetCatalog,
    AssetCatalogEntry,
    AssetCatalogError,
    AssetSelectionContext,
    build_asset_selection_context,
    build_scene_visual_intent,
    load_asset_catalog,
    load_implicit_automation_asset_catalog,
    register_selected_asset,
    resolve_scene_asset as resolve_catalog_scene_asset,
)
from pipeline.scene_planner import SceneManifest, SceneSegment, load_scene_manifest, save_scene_manifest
from pipeline.visual_manifest import (
    SceneVisualAsset,
    VISUAL_MANIFEST_VERSION,
    VisualManifest,
    save_visual_manifest,
)
from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

GENERATED_MEDIA_FILENAME = build_source_media_artifact_filename(".ppm")
TARGET_WIDTH = 720
TARGET_HEIGHT = 1280
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
SCENE_ASSET_CONFIG_KEYS = ("scene_assets", "scene_asset_map", "scene_assets_by_scene")
SCENE_ASSET_POOL_KEYS = ("scene_asset_pool", "asset_pool", "source_media_pool")
CONTEXTUAL_VIDEO_DIRS = ("tiktok/video",)
CONTEXTUAL_IMAGE_DIRS = ("tiktok/image", "tiktok/photo")
DEFAULT_VISUAL_THEME = "signal"
DEFAULT_VISUAL_ASSET_MODE = "auto"
VISUAL_THEMES = ("signal", "midnight", "mint")
VISUAL_ASSET_MODES = ("dashboard", "workflow", "ui", "screenshot")
VISUAL_ASSET_MODE_ALIASES = {
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
VISUAL_STOP_WORDS = {
    "a",
    "an",
    "and",
    "au",
    "aux",
    "before",
    "comment",
    "common",
    "dans",
    "de",
    "des",
    "du",
    "for",
    "how",
    "ideas",
    "into",
    "les",
    "most",
    "one",
    "pour",
    "short",
    "sur",
    "that",
    "the",
    "this",
    "turn",
    "videos",
    "way",
    "with",
}
SCENE_STORY_STOP_WORDS = VISUAL_STOP_WORDS.union(
    {
        "ce",
        "cet",
        "cette",
        "est",
        "juste",
        "mais",
        "plus",
        "qui",
        "son",
        "ton",
        "trop",
        "une",
        "votre",
    }
)
CONTEXTUAL_BACKGROUND_ALL_SCENE_TYPES = ("hook", "explain", "workflow", "proof", "interface", "cta")
TOOL_HINTS = (
    ("google sheets", "Google Sheets"),
    ("make.com", "Make"),
    ("make", "Make"),
    ("zapier", "Zapier"),
    ("n8n", "n8n"),
    ("notion", "Notion"),
    ("airtable", "Airtable"),
    ("hubspot", "HubSpot"),
    ("slack", "Slack"),
    ("clickup", "ClickUp"),
    ("asana", "Asana"),
    ("trello", "Trello"),
    ("typeform", "Typeform"),
    ("calendly", "Calendly"),
    ("capcut", "CapCut"),
    ("descript", "Descript"),
    ("premiere", "Premiere"),
    ("chatgpt", "ChatGPT"),
    ("openai", "OpenAI"),
    ("gmail", "Gmail"),
)
MODE_LABELS = {
    "dashboard": "LIVE DASHBOARD",
    "workflow": "WORKFLOW CANVAS",
    "ui": "OPERATOR UI",
    "screenshot": "PSEUDO SCREENSHOT",
}
PROOF_BADGES = {
    "example": "CASE",
    "tool": "STACK",
    "result": "RESULT",
    "field_error": "FIELD",
}
PROFILE_DEFAULT_MODES = {
    "automation": "workflow",
    "hooks": "screenshot",
    "subtitles": "screenshot",
    "repurpose": "ui",
    "generic": "dashboard",
}
PROFILE_MODE_CANDIDATES = {
    "automation": ("workflow", "dashboard", "ui", "screenshot"),
    "hooks": ("screenshot", "ui", "dashboard"),
    "subtitles": ("screenshot", "ui", "dashboard"),
    "repurpose": ("ui", "dashboard", "screenshot"),
    "generic": ("dashboard", "ui", "screenshot", "workflow"),
}
MODE_THEME_PREFERENCES = {
    "dashboard": "signal",
    "workflow": "midnight",
    "ui": "mint",
    "screenshot": "midnight",
}
VISUAL_LAYOUT_VARIANTS = {
    "dashboard": ("ops_grid", "chart_focus"),
    "workflow": ("map_center", "proof_focus"),
    "ui": ("queue_split", "inspector_focus"),
    "screenshot": ("browser_grid", "browser_spotlight"),
}
DRAWABLE_CHARACTERS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -/")
WORD_RE = re.compile(
    r"\d+(?:[.,]\d+)?%?|[\w]+(?:[.'/:%+-][\w]+)*",
    flags=re.UNICODE,
)
NUMBER_RE = re.compile(r"\b\d+\b")
BITMAP_FONT = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00100", "01000", "10000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11100", "10010", "10001", "10001", "10001", "10010", "11100"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00001", "00001", "00001", "00001", "10001", "10001", "01110"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


class VisualGenerationError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class ExistingSource:
    key: str
    path: Path


@dataclass(slots=True, frozen=True)
class ConfiguredSceneAsset:
    path: Path
    source_kind: str
    order: int
    selected_by: str
    asset_origin_path: str


@dataclass(slots=True)
class SceneAssetSelectionState:
    pool_assets: list[ConfiguredSceneAsset]
    asset_catalog: AssetCatalog | None
    selection_context: AssetSelectionContext
    background_video_catalog: AssetCatalog | None = None
    background_image_catalog: AssetCatalog | None = None


@dataclass(slots=True, frozen=True)
class ResolvedSceneAsset:
    path: Path
    asset_type: str
    source_kind: str
    selected_by: str
    asset_origin_path: str
    matched_tags: tuple[str, ...] = ()
    matched_entities: tuple[str, ...] = ()
    matched_visual_categories: tuple[str, ...] = ()
    selection_score: float | None = None
    reuse_rank: int | None = None


@dataclass(slots=True, frozen=True)
class Palette:
    background_top: tuple[int, int, int]
    background_bottom: tuple[int, int, int]
    glow: tuple[int, int, int]
    glow_alt: tuple[int, int, int]
    surface: tuple[int, int, int]
    surface_alt: tuple[int, int, int]
    surface_soft: tuple[int, int, int]
    chrome: tuple[int, int, int]
    border: tuple[int, int, int]
    text_dark: tuple[int, int, int]
    text_muted: tuple[int, int, int]
    text_light: tuple[int, int, int]
    accent: tuple[int, int, int]
    accent_soft: tuple[int, int, int]
    success: tuple[int, int, int]
    warning: tuple[int, int, int]
    danger: tuple[int, int, int]


@dataclass(slots=True, frozen=True)
class MetricTile:
    label: str
    value: str
    status: str


@dataclass(slots=True, frozen=True)
class TableRow:
    label: str
    detail: str
    state: str


@dataclass(slots=True, frozen=True)
class WorkflowNode:
    title: str
    subtitle: str
    state: str


@dataclass(slots=True, frozen=True)
class VisualAssetSpec:
    mode: str
    theme: str
    layout_variant: str
    profile: str
    proof_mode: str
    product_name: str
    eyebrow: str
    title_lines: list[str]
    chips: list[str]
    metrics: list[MetricTile]
    table_rows: list[TableRow]
    workflow_nodes: list[WorkflowNode]
    highlight_lines: list[str]
    footer_text: str
    tool_label: str


async def generate_visual_step(job: VideoJob, job_dir: Path) -> None:
    scene_manifest = load_scene_manifest(job)
    if scene_manifest is not None and len(scene_manifest.scenes) > 1:
        await _generate_multi_scene_visuals(job, job_dir, scene_manifest)
        return

    existing_source = _resolve_existing_source(job)
    if existing_source is not None:
        output_path = _stage_existing_source(job, job_dir, existing_source)
        mode = "copied_source"
        resolution = "unknown"
        job.set_metadata(
            "anti_repetition_visual",
            {
                "skipped": True,
                "reason": "existing_source_media",
            },
        )
        LOGGER.info(
            "job=%s visual_gen reused source key=%s output=%s",
            job.job_id,
            existing_source.key,
            output_path,
        )
    else:
        output_path = job_dir / GENERATED_MEDIA_FILENAME
        spec, decisions, memory = _build_visual_asset_spec(job)
        _generate_visual_asset(job, output_path, spec)
        mode = f"generated_{spec.mode}"
        resolution = f"{TARGET_WIDTH}x{TARGET_HEIGHT}"
        job.set_metadata("visual_theme", spec.theme)
        job.set_metadata("visual_asset_mode", spec.mode)
        job.set_metadata("visual_layout_variant", spec.layout_variant)
        job.set_metadata(
            "visual_layout_signature",
            _build_visual_layout_signature(spec.mode, spec.layout_variant),
        )
        job.set_metadata(
            "visual_signature",
            _build_visual_signature(spec.mode, spec.theme, spec.layout_variant),
        )
        job.set_metadata(
            "visual_layout",
            {
                "variant": spec.layout_variant,
                "signature": _build_visual_layout_signature(spec.mode, spec.layout_variant),
                "product_name": spec.product_name,
                "eyebrow": spec.eyebrow,
                "title_lines": list(spec.title_lines),
                "chips": list(spec.chips),
                "footer_text": spec.footer_text,
            },
        )
        job.set_metadata(
            "visual_asset_spec",
            {
                "mode": spec.mode,
                "theme": spec.theme,
                "layout_variant": spec.layout_variant,
                "profile": spec.profile,
                "proof_mode": spec.proof_mode,
                "product_name": spec.product_name,
                "eyebrow": spec.eyebrow,
                "title_lines": list(spec.title_lines),
                "chips": list(spec.chips),
                "highlight_lines": list(spec.highlight_lines),
                "footer_text": spec.footer_text,
                "tool_label": spec.tool_label,
                "metrics": [
                    {"label": metric.label, "value": metric.value, "status": metric.status}
                    for metric in spec.metrics
                ],
                "table_rows": [
                    {"label": row.label, "detail": row.detail, "state": row.state}
                    for row in spec.table_rows
                ],
                "workflow_nodes": [
                    {"title": node.title, "subtitle": node.subtitle, "state": node.state}
                    for node in spec.workflow_nodes
                ],
            },
        )
        job.set_metadata(
            "anti_repetition_visual",
            {
                "config": memory.config.to_metadata(),
                "window_job_ids": memory.window_job_ids,
                "decisions": {
                    "mode": decisions["mode"].to_metadata(),
                    "theme": decisions["theme"].to_metadata(),
                    "layout": decisions["layout"].to_metadata(),
                },
            },
        )
        LOGGER.info(
            "job=%s visual_gen generated asset mode=%s theme=%s layout=%s output=%s",
            job.job_id,
            spec.mode,
            spec.theme,
            spec.layout_variant,
            output_path,
        )

    job.set_source_media_path(str(output_path.resolve()))
    job.set_metadata("visual_generation_mode", mode)
    job.set_metadata("visual_source_resolution", resolution)
    job.set_metadata("visual_source_format", output_path.suffix.lower().lstrip("."))


async def _generate_multi_scene_visuals(
    job: VideoJob,
    job_dir: Path,
    manifest: SceneManifest,
) -> None:
    scene_assets_dir = (job_dir / SCENE_ASSETS_DIRNAME).resolve()
    scene_assets_dir.mkdir(parents=True, exist_ok=True)

    explicit_scene_assets = _build_explicit_scene_asset_map(job, manifest)
    try:
        asset_catalog = load_asset_catalog(job)
    except AssetCatalogError as exc:
        raise VisualGenerationError(str(exc)) from exc
    if asset_catalog is None and _should_use_implicit_automation_asset_catalog(
        job,
        explicit_scene_assets=explicit_scene_assets,
    ):
        asset_catalog = load_implicit_automation_asset_catalog(job)
    selection_state = SceneAssetSelectionState(
        pool_assets=_build_scene_asset_pool(job),
        asset_catalog=asset_catalog,
        selection_context=build_asset_selection_context(job),
        background_video_catalog=_build_contextual_background_catalog(job, asset_type="video"),
        background_image_catalog=_build_contextual_background_catalog(job, asset_type="image"),
    )
    rendered_scenes: list[SceneSegment] = []
    visual_manifest_scenes: list[SceneVisualAsset] = []
    visual_summaries: list[dict[str, object]] = []
    primary_output_path: Path | None = None
    generated_scene_count = 0
    external_scene_count = 0
    selected_by_counts: dict[str, int] = {}

    for scene in manifest.scenes:
        resolved_asset = select_background_asset(
            scene,
            job=job,
            explicit_scene_assets=explicit_scene_assets,
            selection_state=selection_state,
        )
        visual_theme = scene.visual_theme
        visual_layout_variant = scene.visual_layout_variant
        summary: dict[str, object]

        if resolved_asset is not None:
            staged_asset_path = _stage_scene_asset(scene_assets_dir, scene, resolved_asset.path)
            asset_output_path = staged_asset_path.resolve()
            external_scene_count += 1
            rendered_scene = SceneSegment(
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
                asset_path=str(asset_output_path),
                clip_path=scene.clip_path,
                visual_theme=visual_theme,
                visual_layout_variant=visual_layout_variant,
                overlay_role=scene.overlay_role,
                overlay_text=scene.overlay_text,
                subtitle_text=scene.subtitle_text,
                visual_intensity_level=scene.visual_intensity_level,
                primary_focus=scene.primary_focus,
            )
            summary = {
                "scene_id": scene.scene_id,
                "scene_index": scene.index,
                "asset_path": str(asset_output_path),
                "asset_origin_path": resolved_asset.asset_origin_path,
                "asset_type": resolved_asset.asset_type,
                "source_kind": resolved_asset.source_kind,
                "selected_by": resolved_asset.selected_by,
                "matched_tags": list(resolved_asset.matched_tags),
                "matched_entities": list(resolved_asset.matched_entities),
                "matched_visual_categories": list(resolved_asset.matched_visual_categories),
                "selection_score": resolved_asset.selection_score,
                "reuse_rank": resolved_asset.reuse_rank,
                "visual_asset_mode": scene.visual_asset_mode,
                "motion_effect": scene.motion_effect,
                "transition_effect": scene.transition_effect,
                "transition_duration_seconds": scene.transition_duration_seconds,
            }
        else:
            scene_output_path = scene_assets_dir / build_scene_asset_filename(scene.index, ".ppm")
            scene_job = _build_scene_visual_job(job, scene)
            _apply_intra_video_visual_diversity(
                scene_job,
                scene,
                previous_scene=rendered_scenes[-1] if rendered_scenes else None,
            )
            spec, _decisions, _memory = _build_visual_asset_spec(scene_job)
            _generate_visual_asset(scene_job, scene_output_path, spec)
            asset_output_path = scene_output_path.resolve()
            generated_scene_count += 1
            visual_theme = spec.theme
            visual_layout_variant = spec.layout_variant
            rendered_scene = SceneSegment(
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
                asset_path=str(asset_output_path),
                clip_path=scene.clip_path,
                visual_theme=spec.theme,
                visual_layout_variant=spec.layout_variant,
                overlay_role=scene.overlay_role,
                overlay_text=scene.overlay_text,
                subtitle_text=scene.subtitle_text,
                visual_intensity_level=scene.visual_intensity_level,
                primary_focus=scene.primary_focus,
            )
            summary = {
                "scene_id": scene.scene_id,
                "scene_index": scene.index,
                "asset_path": str(asset_output_path),
                "asset_origin_path": str(asset_output_path),
                "asset_type": "image",
                "source_kind": "generated",
                "selected_by": "generated",
                "matched_tags": [],
                "matched_entities": [],
                "matched_visual_categories": [],
                "selection_score": None,
                "reuse_rank": None,
                "visual_asset_mode": scene.visual_asset_mode,
                "visual_theme": spec.theme,
                "visual_layout_variant": spec.layout_variant,
                "motion_effect": scene.motion_effect,
                "transition_effect": scene.transition_effect,
                "transition_duration_seconds": scene.transition_duration_seconds,
                "duration_seconds": scene.duration_seconds,
                "title_lines": list(spec.title_lines),
            }

        story_metadata = _build_scene_story_metadata(
            scene,
            asset_path=asset_output_path,
            asset_type=str(summary["asset_type"]),
            source_kind=str(summary["source_kind"]),
            selected_by=str(summary["selected_by"]),
        )
        summary.update(story_metadata)

        rendered_scenes.append(rendered_scene)
        visual_manifest_scenes.append(
            SceneVisualAsset(
                scene_id=scene.scene_id,
                scene_index=scene.index,
                asset_path=str(asset_output_path),
                asset_type=summary["asset_type"],
                source_kind=summary["source_kind"],
                selected_by=summary["selected_by"],
                asset_origin_path=str(summary["asset_origin_path"]),
                matched_tags=tuple(str(item) for item in summary["matched_tags"]),
                matched_entities=tuple(str(item) for item in summary["matched_entities"]),
                matched_visual_categories=tuple(
                    str(item) for item in summary["matched_visual_categories"]
                ),
                selection_score=(
                    float(summary["selection_score"])
                    if summary["selection_score"] is not None
                    else None
                ),
                reuse_rank=(
                    int(summary["reuse_rank"])
                    if summary["reuse_rank"] is not None
                    else None
                ),
                visual_asset_mode=scene.visual_asset_mode,
                motion_effect=scene.motion_effect,
                transition_effect=scene.transition_effect,
                transition_duration_seconds=scene.transition_duration_seconds,
                scene_title=str(summary["scene_title"]),
                scene_keywords=tuple(str(item) for item in summary["scene_keywords"]),
                visual_asset_used=str(summary["visual_asset_used"]),
                overlay_applied=tuple(str(item) for item in summary["overlay_applied"]),
            )
        )
        visual_summaries.append(summary)
        selected_by = str(summary["selected_by"])
        selected_by_counts[selected_by] = selected_by_counts.get(selected_by, 0) + 1
        if primary_output_path is None:
            primary_output_path = asset_output_path

    if primary_output_path is None:
        raise VisualGenerationError("multi-scene visual generation produced no source media")

    updated_manifest = SceneManifest(
        version=manifest.version,
        strategy=manifest.strategy,
        total_duration_seconds=manifest.total_duration_seconds,
        primary_visual_asset_mode=manifest.primary_visual_asset_mode,
        scenes=rendered_scenes,
    )
    save_scene_manifest(job, job_dir, updated_manifest)
    save_visual_manifest(
        job,
        job_dir,
        VisualManifest(version=VISUAL_MANIFEST_VERSION, scenes=visual_manifest_scenes),
    )

    dominant_mode = _resolve_dominant_scene_field(rendered_scenes, field_name="visual_asset_mode")
    dominant_theme = _resolve_dominant_scene_field(rendered_scenes, field_name="visual_theme")
    dominant_layout = _resolve_dominant_scene_field(rendered_scenes, field_name="visual_layout_variant")

    job.set_source_media_path(str(primary_output_path.resolve()))
    job.set_metadata("visual_generation_mode", "scene_pipeline_v2")
    job.set_metadata("visual_scene_asset_count", len(rendered_scenes))
    job.set_metadata("visual_scene_assets", visual_summaries)
    job.set_metadata("visual_asset_mode", dominant_mode or manifest.primary_visual_asset_mode)
    if dominant_theme:
        job.set_metadata("visual_theme", dominant_theme)
    if dominant_layout:
        job.set_metadata("visual_layout_variant", dominant_layout)
        job.set_metadata(
            "visual_layout_signature",
            _build_visual_layout_signature(
                dominant_mode or manifest.primary_visual_asset_mode,
                dominant_layout,
            ),
        )
    if dominant_mode and dominant_theme and dominant_layout:
        job.set_metadata(
            "visual_signature",
            _build_visual_signature(dominant_mode, dominant_theme, dominant_layout),
        )
    job.set_metadata(
        "anti_repetition_visual",
        {
            "skipped": generated_scene_count == 0,
            "mode": _resolve_scene_asset_strategy(
                generated_scene_count=generated_scene_count,
                external_scene_count=external_scene_count,
                selected_by_counts=selected_by_counts,
            ),
            "scene_count": len(rendered_scenes),
        },
    )
    job.set_metadata(
        "visual_source_resolution",
        f"{TARGET_WIDTH}x{TARGET_HEIGHT}"
        if generated_scene_count == len(rendered_scenes)
        else "mixed",
    )
    job.set_metadata("visual_source_format", primary_output_path.suffix.lower().lstrip("."))

    LOGGER.info(
        "job=%s visual_gen generated multi-scene assets count=%s primary=%s mode=%s",
        job.job_id,
        len(rendered_scenes),
        primary_output_path,
        dominant_mode or manifest.primary_visual_asset_mode,
    )


def _resolve_existing_source(job: VideoJob) -> ExistingSource | None:
    for key in SOURCE_MEDIA_PATH_KEYS:
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
            raise VisualGenerationError(
                f"source media configured in '{key}' does not exist: {candidate_path}"
            )
        if not candidate_path.is_file():
            raise VisualGenerationError(
                f"source media configured in '{key}' is not a file: {candidate_path}"
            )
        return ExistingSource(key=key, path=candidate_path)

    return None


def _stage_existing_source(job: VideoJob, job_dir: Path, source: ExistingSource) -> Path:
    suffix = source.path.suffix.lower()
    if not suffix:
        raise VisualGenerationError(f"source media has no file extension: {source.path}")

    target_path = job_dir / build_source_media_artifact_filename(suffix)
    if source.path.resolve() != target_path.resolve():
        shutil.copy2(source.path, target_path)
    elif not target_path.exists():
        raise VisualGenerationError(f"expected source media is missing: {target_path}")

    job.set_metadata("visual_source_origin", str(source.path))
    job.set_metadata("visual_source_input_key", source.key)
    return target_path


def _build_explicit_scene_asset_map(
    job: VideoJob,
    manifest: SceneManifest,
) -> dict[str, ConfiguredSceneAsset]:
    for key in SCENE_ASSET_CONFIG_KEYS:
        raw_value = job.metadata.get(key)
        if raw_value is None:
            continue
        return _parse_scene_asset_config(job, manifest, raw_value, source_key=key)
    return {}


def _parse_scene_asset_config(
    job: VideoJob,
    manifest: SceneManifest,
    raw_value: object,
    *,
    source_key: str,
) -> dict[str, ConfiguredSceneAsset]:
    assignments: dict[str, ConfiguredSceneAsset] = {}
    scenes_by_id = {scene.scene_id: scene for scene in manifest.scenes}
    scenes_by_index = {scene.index: scene for scene in manifest.scenes}

    if isinstance(raw_value, list):
        if not raw_value:
            raise VisualGenerationError(f"scene asset config '{source_key}' must not be empty")
        for offset, item in enumerate(raw_value):
            scene = scenes_by_index.get(offset)
            if scene is None:
                break
            configured_asset = _build_configured_scene_asset(
                job,
                item,
                source_kind=f"{source_key}[{offset + 1}]",
                order=offset,
                selected_by="explicit",
            )
            if configured_asset is None:
                continue
            if isinstance(item, dict):
                scene = _resolve_scene_reference(
                    manifest,
                    scene_id=_read_optional_text(item.get("scene_id")),
                    scene_index=_read_optional_int(item.get("scene_index") or item.get("index")),
                    default_scene=scene,
                )
            assignments[scene.scene_id] = configured_asset
        return assignments

    if isinstance(raw_value, dict):
        if not raw_value:
            raise VisualGenerationError(f"scene asset config '{source_key}' must not be empty")
        for order, (raw_scene_id, item) in enumerate(raw_value.items()):
            scene_id = str(raw_scene_id).strip()
            if not scene_id:
                continue
            scene = scenes_by_id.get(scene_id)
            if scene is None:
                raise VisualGenerationError(
                    f"scene asset config '{source_key}' references an unknown scene_id: {scene_id}"
                )
            configured_asset = _build_configured_scene_asset(
                job,
                item,
                source_kind=f"{source_key}.{scene_id}",
                order=order,
                selected_by="explicit",
            )
            if configured_asset is not None:
                assignments[scene.scene_id] = configured_asset
        return assignments

    raise VisualGenerationError(
        f"scene asset config '{source_key}' must be a list or object, got {type(raw_value).__name__}"
    )


def _build_scene_asset_pool(job: VideoJob) -> list[ConfiguredSceneAsset]:
    for key in SCENE_ASSET_POOL_KEYS:
        raw_value = job.metadata.get(key)
        if raw_value is None:
            continue
        if not isinstance(raw_value, list):
            raise VisualGenerationError(
                f"scene asset pool '{key}' must be a list, got {type(raw_value).__name__}"
            )
        if not raw_value:
            raise VisualGenerationError(f"scene asset pool '{key}' must not be empty")
        pool_assets: list[ConfiguredSceneAsset] = []
        for index, item in enumerate(raw_value):
            configured_asset = _build_configured_scene_asset(
                job,
                item,
                source_kind=f"{key}[{index + 1}]",
                order=index,
                selected_by="pool",
            )
            if configured_asset is not None:
                pool_assets.append(configured_asset)
        if not pool_assets:
            raise VisualGenerationError(f"scene asset pool '{key}' resolved to no usable assets")
        return pool_assets

    existing_source = _resolve_existing_source(job)
    if existing_source is None:
        return []
    return [
        ConfiguredSceneAsset(
            path=existing_source.path,
            source_kind=f"legacy_source:{existing_source.key}",
            order=0,
            selected_by="legacy",
            asset_origin_path=str(existing_source.path.resolve()),
        )
    ]


def _build_configured_scene_asset(
    job: VideoJob,
    raw_value: object,
    *,
    source_kind: str,
    order: int,
    selected_by: str,
) -> ConfiguredSceneAsset | None:
    raw_path: str | None
    if isinstance(raw_value, str):
        raw_path = raw_value.strip()
    elif isinstance(raw_value, dict):
        raw_path = (
            _read_optional_text(raw_value.get("asset_path"))
            or _read_optional_text(raw_value.get("path"))
            or _read_optional_text(raw_value.get("source_media_path"))
            or _read_optional_text(raw_value.get("source_media"))
        )
    else:
        raise VisualGenerationError(
            f"scene asset entry '{source_kind}' must be a string or object, got {type(raw_value).__name__}"
        )

    if not raw_path:
        return None

    resolved_path = resolve_job_path(job, raw_path)
    if not resolved_path.exists():
        raise VisualGenerationError(f"scene asset '{source_kind}' does not exist: {resolved_path}")
    if not resolved_path.is_file():
        raise VisualGenerationError(f"scene asset '{source_kind}' is not a file: {resolved_path}")
    return ConfiguredSceneAsset(
        path=resolved_path.resolve(),
        source_kind=source_kind,
        order=order,
        selected_by=selected_by,
        asset_origin_path=str(resolved_path.resolve()),
    )


def _should_use_implicit_automation_asset_catalog(
    job: VideoJob,
    *,
    explicit_scene_assets: dict[str, ConfiguredSceneAsset],
) -> bool:
    if explicit_scene_assets:
        return False
    if any(job.metadata.get(key) is not None for key in SCENE_ASSET_POOL_KEYS):
        return False
    if _resolve_existing_source(job) is not None:
        return False
    return _resolve_visual_profile(job) == "automation"


def _resolve_scene_reference(
    manifest: SceneManifest,
    *,
    scene_id: str | None,
    scene_index: int | None,
    default_scene: SceneSegment,
) -> SceneSegment:
    if scene_id:
        for scene in manifest.scenes:
            if scene.scene_id == scene_id:
                return scene
        raise VisualGenerationError(f"scene asset config references an unknown scene_id: {scene_id}")

    if scene_index is not None:
        for scene in manifest.scenes:
            if scene.index == scene_index:
                return scene
        raise VisualGenerationError(f"scene asset config references an unknown scene index: {scene_index}")

    return default_scene


def select_background_asset(
    scene: SceneSegment,
    *,
    job: VideoJob,
    explicit_scene_assets: dict[str, ConfiguredSceneAsset],
    selection_state: SceneAssetSelectionState,
) -> ResolvedSceneAsset | None:
    if scene.asset_path:
        return _build_resolved_scene_asset(
            job,
            raw_path=scene.asset_path,
            source_kind="scene_manifest.asset_path",
            selected_by="explicit",
            selection_context=selection_state.selection_context,
        )

    configured_asset = explicit_scene_assets.get(scene.scene_id)
    if configured_asset is not None:
        return _build_resolved_configured_scene_asset(
            configured_asset,
            selection_context=selection_state.selection_context,
        )

    if selection_state.asset_catalog is not None:
        catalog_asset = _resolve_catalog_scene_asset(
            scene,
            job=job,
            asset_catalog=selection_state.asset_catalog,
            selection_context=selection_state.selection_context,
            selected_by="catalog_match",
        )
        if catalog_asset is not None:
            return catalog_asset

    pooled_asset = _select_scene_asset_from_pool(selection_state)
    if pooled_asset is not None:
        return _build_resolved_configured_scene_asset(
            pooled_asset,
            selection_context=selection_state.selection_context,
        )

    contextual_video_asset = _resolve_catalog_scene_asset(
        scene,
        job=job,
        asset_catalog=selection_state.background_video_catalog,
        selection_context=selection_state.selection_context,
        selected_by="contextual_video",
    )
    if contextual_video_asset is not None:
        return contextual_video_asset

    contextual_image_asset = _resolve_catalog_scene_asset(
        scene,
        job=job,
        asset_catalog=selection_state.background_image_catalog,
        selection_context=selection_state.selection_context,
        selected_by="contextual_image",
    )
    if contextual_image_asset is not None:
        return contextual_image_asset

    return None


def _resolve_scene_asset(
    job: VideoJob,
    scene: SceneSegment,
    *,
    explicit_scene_assets: dict[str, ConfiguredSceneAsset],
    selection_state: SceneAssetSelectionState,
) -> ResolvedSceneAsset | None:
    return select_background_asset(
        scene,
        job=job,
        explicit_scene_assets=explicit_scene_assets,
        selection_state=selection_state,
    )


def _resolve_catalog_scene_asset(
    scene: SceneSegment,
    *,
    job: VideoJob,
    asset_catalog: AssetCatalog | None,
    selection_context: AssetSelectionContext,
    selected_by: str,
) -> ResolvedSceneAsset | None:
    if asset_catalog is None:
        return None

    scene_intent = build_scene_visual_intent(
        scene,
        asset_catalog,
        global_topic=job.topic,
    )
    try:
        catalog_candidate = resolve_catalog_scene_asset(
            scene_intent,
            asset_catalog,
            selection_context,
        )
    except AssetCatalogError as exc:
        raise VisualGenerationError(str(exc)) from exc
    if catalog_candidate is None:
        return None

    reuse_rank = register_selected_asset(
        selection_context,
        catalog_candidate.entry.selection_key,
    )
    return ResolvedSceneAsset(
        path=catalog_candidate.entry.path,
        asset_type=catalog_candidate.entry.asset_type,
        source_kind=catalog_candidate.entry.source_kind,
        selected_by=selected_by,
        asset_origin_path=catalog_candidate.entry.selection_key,
        matched_tags=catalog_candidate.matched_tags,
        matched_entities=catalog_candidate.matched_entities,
        matched_visual_categories=catalog_candidate.matched_visual_categories,
        selection_score=float(catalog_candidate.score),
        reuse_rank=reuse_rank,
    )


def _build_contextual_background_catalog(
    job: VideoJob,
    *,
    asset_type: str,
) -> AssetCatalog | None:
    if asset_type not in {"image", "video"}:
        raise VisualGenerationError(f"unsupported contextual background asset_type: {asset_type}")

    relative_dirs = CONTEXTUAL_VIDEO_DIRS if asset_type == "video" else CONTEXTUAL_IMAGE_DIRS
    seen_paths: set[Path] = set()
    entries: list[AssetCatalogEntry] = []
    for root in _iter_contextual_background_roots(job):
        for relative_dir in relative_dirs:
            asset_dir = (root / relative_dir).resolve()
            if not asset_dir.exists() or not asset_dir.is_dir():
                continue
            for asset_path in sorted(asset_dir.iterdir()):
                if not asset_path.is_file():
                    continue
                resolved_path = asset_path.resolve()
                if resolved_path in seen_paths:
                    continue
                if _infer_asset_type(resolved_path) != asset_type:
                    continue
                seen_paths.add(resolved_path)
                tags = _build_contextual_background_tags(resolved_path, asset_type=asset_type)
                categories = _derive_contextual_background_categories(tags, asset_type=asset_type)
                entries.append(
                    AssetCatalogEntry(
                        path=resolved_path,
                        asset_type=asset_type,
                        tags=tuple(tags),
                        entities=(),
                        visual_categories=tuple(categories),
                        allowed_scene_types=CONTEXTUAL_BACKGROUND_ALL_SCENE_TYPES,
                        source_kind=f"contextual_background_{asset_type}[{len(entries) + 1}]",
                    )
                )

    if not entries:
        return None
    return AssetCatalog(
        entries=entries,
        source_key=f"contextual_background_{asset_type}",
    )


def _iter_contextual_background_roots(job: VideoJob) -> list[Path]:
    candidates: list[Path] = []
    raw_config_path = _read_text_metadata(job, "config_path")
    if raw_config_path is not None:
        config_path = Path(raw_config_path).expanduser().resolve()
        candidates.extend([config_path.parent, config_path.parent.parent])
    candidates.append(Path.cwd().resolve())

    roots: list[Path] = []
    for candidate in candidates:
        if candidate not in roots:
            roots.append(candidate)
    return roots


def _build_contextual_background_tags(path: Path, *, asset_type: str) -> list[str]:
    normalized_name = path.stem.replace("-", " ").replace("_", " ").lower()
    tags = ["background", asset_type]
    if asset_type == "video":
        tags.extend(["motion", "attention", "concept"])
    else:
        tags.extend(["illustration", "concept"])

    for token in WORD_RE.findall(normalized_name):
        normalized_token = token.lower()
        if len(normalized_token) <= 2 or normalized_token in VISUAL_STOP_WORDS:
            continue
        if normalized_token not in tags:
            tags.append(normalized_token)

    tag_set = set(tags)
    if {"dashboard", "graph", "graphs", "statistics", "analytics", "financial", "marketing", "monitoring"}.intersection(tag_set):
        tags.extend(["dashboard", "analytics", "metrics", "proof", "result"])
    if {"workflow", "automation", "process", "pipeline", "system", "systems", "server", "support"}.intersection(tag_set):
        tags.extend(["workflow", "automation", "process", "system"])
    if {"screen", "computer", "laptop", "office", "startup", "ui", "interface", "software"}.intersection(tag_set):
        tags.extend(["ui", "software", "screen", "screenshot"])
    if {"error", "warning", "alert", "broken", "glitch"}.intersection(tag_set):
        tags.extend(["error", "glitch", "attention"])
    return _unique_strings(tags)


def _derive_contextual_background_categories(
    tags: list[str],
    *,
    asset_type: str,
) -> list[str]:
    categories = ["background", "concept"]
    if asset_type == "video":
        categories.extend(["motion", "attention"])
    else:
        categories.extend(["illustration"])

    tag_set = set(tags)
    if {"dashboard", "analytics", "metrics", "proof", "result"}.intersection(tag_set):
        categories.extend(["dashboard", "analytics", "metrics", "proof", "result"])
    if {"workflow", "automation", "process", "system"}.intersection(tag_set):
        categories.extend(["workflow", "automation", "process", "system"])
    if {"ui", "software", "screen", "screenshot"}.intersection(tag_set):
        categories.extend(["ui", "software", "screen", "screenshot"])
    if {"error", "glitch"}.intersection(tag_set):
        categories.extend(["error", "glitch"])
    if {"business", "company", "employees", "planning", "strategy"}.intersection(tag_set):
        categories.extend(["business", "workflow", "dashboard"])
    return _unique_strings(categories)


def _build_scene_story_metadata(
    scene: SceneSegment,
    *,
    asset_path: Path,
    asset_type: str,
    source_kind: str,
    selected_by: str,
) -> dict[str, object]:
    scene_title = _derive_scene_title(scene)
    scene_keywords = _derive_scene_keywords(scene)
    overlay_applied = _derive_scene_overlay_applied(
        scene,
        asset_type=asset_type,
        selected_by=selected_by,
        scene_title=scene_title,
        scene_keywords=scene_keywords,
    )
    return {
        "scene_title": scene_title,
        "scene_keywords": scene_keywords,
        "visual_asset_used": str(asset_path),
        "overlay_applied": overlay_applied,
        "background_source_kind": source_kind,
    }


def _derive_scene_title(scene: SceneSegment) -> str:
    if scene.overlay_text:
        overlay_words = _split_story_words(scene.overlay_text)
        if overlay_words:
            return " ".join(overlay_words[: min(5, len(overlay_words))])
    if scene.visual_intensity_level is not None:
        return ""
    for candidate in (scene.highlight_text, scene.title_text, scene.source_text):
        words = _split_story_words(candidate)
        if 2 <= len(words) <= 6:
            return " ".join(words)
        if len(words) > 6:
            return " ".join(words[:6])

    fallback_words = _split_story_words(scene.source_text or scene.title_text or scene.highlight_text)
    if fallback_words:
        return " ".join(fallback_words[: max(1, min(6, len(fallback_words)))])
    return scene.scene_type.replace("_", " ").strip().title() or "Scene"


def _derive_scene_keywords(scene: SceneSegment) -> list[str]:
    raw_candidates: list[str] = []
    raw_candidates.extend(scene.keywords)
    raw_candidates.extend(_split_story_words(scene.highlight_text))
    raw_candidates.extend(_split_story_words(scene.title_text))
    raw_candidates.extend(_split_story_words(scene.source_text))

    keywords: list[str] = []
    seen_normalized: set[str] = set()
    for candidate in raw_candidates:
        normalized_candidate = candidate.strip()
        lowered_candidate = normalized_candidate.lower()
        if not normalized_candidate:
            continue
        if len(lowered_candidate) <= 2 and not lowered_candidate.isdigit():
            continue
        if lowered_candidate in SCENE_STORY_STOP_WORDS:
            continue
        if lowered_candidate in seen_normalized:
            continue
        seen_normalized.add(lowered_candidate)
        keywords.append(normalized_candidate)
        if len(keywords) == 3:
            break
    return keywords


def _derive_scene_overlay_applied(
    scene: SceneSegment,
    *,
    asset_type: str,
    selected_by: str,
    scene_title: str,
    scene_keywords: list[str],
) -> list[str]:
    overlays: list[str] = []
    if scene_title:
        overlays.append("title_text")
    if scene.role == "hook" and scene_title and (
        selected_by == "contextual_video" or asset_type == "video" or scene.motion_effect == "zoom_in"
    ):
        overlays.append("impact_zoom")
    return _unique_strings(overlays)


def _split_story_words(value: str) -> list[str]:
    if not value:
        return []
    return [match.strip() for match in WORD_RE.findall(" ".join(value.split())) if match.strip()]


def _unique_strings(values: list[str]) -> list[str]:
    unique_values: list[str] = []
    for value in values:
        normalized_value = value.strip()
        if normalized_value and normalized_value not in unique_values:
            unique_values.append(normalized_value)
    return unique_values


def _build_resolved_scene_asset(
    job: VideoJob,
    *,
    raw_path: str,
    source_kind: str,
    selected_by: str,
    selection_context: AssetSelectionContext,
) -> ResolvedSceneAsset:
    asset_path = resolve_job_path(job, raw_path)
    if not asset_path.exists():
        raise VisualGenerationError(f"scene asset '{source_kind}' does not exist: {asset_path}")
    if not asset_path.is_file():
        raise VisualGenerationError(f"scene asset '{source_kind}' is not a file: {asset_path}")
    asset_path = asset_path.resolve()
    reuse_rank = register_selected_asset(selection_context, str(asset_path))
    return ResolvedSceneAsset(
        path=asset_path,
        asset_type=_infer_asset_type(asset_path),
        source_kind=source_kind,
        selected_by=selected_by,
        asset_origin_path=str(asset_path),
        reuse_rank=reuse_rank,
    )


def _select_scene_asset_from_pool(
    selection_state: SceneAssetSelectionState,
) -> ConfiguredSceneAsset | None:
    if not selection_state.pool_assets:
        return None

    selection_context = selection_state.selection_context
    selected_asset = min(
        selection_state.pool_assets,
        key=lambda asset: (
            -_score_pooled_asset(asset, selection_context=selection_context),
            selection_context.current_video_usage_counts.get(asset.asset_origin_path, 0),
            selection_context.historical_usage_counts.get(asset.asset_origin_path, 0),
            asset.order,
            str(asset.path),
        ),
    )
    return selected_asset


def _build_resolved_configured_scene_asset(
    configured_asset: ConfiguredSceneAsset,
    *,
    selection_context: AssetSelectionContext,
) -> ResolvedSceneAsset:
    reuse_rank = register_selected_asset(selection_context, configured_asset.asset_origin_path)
    return ResolvedSceneAsset(
        path=configured_asset.path,
        asset_type=_infer_asset_type(configured_asset.path),
        source_kind=configured_asset.source_kind,
        selected_by=configured_asset.selected_by,
        asset_origin_path=configured_asset.asset_origin_path,
        reuse_rank=reuse_rank,
    )


def _score_pooled_asset(
    asset: ConfiguredSceneAsset,
    *,
    selection_context: AssetSelectionContext,
) -> int:
    asset_key = asset.asset_origin_path
    score = 0
    if asset_key in selection_context.recent_asset_keys[-selection_context.recent_window_size :]:
        score -= 30
    usage_count = selection_context.current_video_usage_counts.get(asset_key, 0)
    if usage_count:
        score -= 15 * usage_count
    else:
        score += 8
    historical_usage = selection_context.historical_usage_counts.get(asset_key, 0)
    score -= 5 * historical_usage
    return score


def _stage_scene_asset(scene_assets_dir: Path, scene: SceneSegment, source_path: Path) -> Path:
    suffix = source_path.suffix.lower()
    if not suffix:
        raise VisualGenerationError(f"scene asset has no file extension: {source_path}")
    target_path = scene_assets_dir / build_scene_asset_filename(scene.index, suffix)
    if source_path.resolve() != target_path.resolve():
        shutil.copy2(source_path, target_path)
    elif not target_path.exists():
        raise VisualGenerationError(f"expected scene asset is missing: {target_path}")
    return target_path


def _infer_asset_type(path: Path) -> str:
    if not path.suffix:
        raise VisualGenerationError(f"scene asset has no file extension: {path}")
    return "image" if path.suffix.lower() in IMAGE_SUFFIXES else "video"


def _resolve_scene_asset_strategy(
    *,
    generated_scene_count: int,
    external_scene_count: int,
    selected_by_counts: dict[str, int],
) -> str:
    if generated_scene_count and external_scene_count:
        return "mixed_scene_assets"
    if generated_scene_count:
        return "generated_scene_assets"
    if selected_by_counts.get("catalog_match"):
        if selected_by_counts.get("pool") or selected_by_counts.get("legacy"):
            return "catalog_with_pool_fallback"
        return "catalog_scene_assets"
    if selected_by_counts.get("pool"):
        return "distributed_asset_pool"
    if selected_by_counts.get("legacy"):
        return "legacy_scene_assets"
    if selected_by_counts.get("explicit"):
        return "explicit_scene_assets"
    return "no_scene_assets"


def _build_scene_visual_job(job: VideoJob, scene: SceneSegment) -> VideoJob:
    metadata = dict(job.metadata)
    metadata["anti_repetition"] = {"enabled": False}
    metadata["visual_asset_mode"] = scene.visual_asset_mode
    metadata["visual_title_text"] = (
        scene.overlay_text
        if scene.visual_intensity_level is not None
        else scene.title_text
    )
    metadata["visual_highlight_text"] = scene.highlight_text
    metadata["visual_keywords"] = list(scene.keywords)
    metadata["script_keywords"] = list(scene.keywords)
    metadata["script_proof_mode"] = scene.proof_mode
    metadata["scene_parent_script_profile"] = _read_text_metadata(job, "script_profile")
    metadata["script_profile"] = _resolve_scene_profile(job, scene)
    metadata["scene_role"] = scene.role
    metadata["scene_type"] = scene.scene_type
    metadata["scene_id"] = scene.scene_id
    return VideoJob(
        topic=scene.source_text or job.topic,
        template=job.template,
        account=job.account,
        job_id=f"{job.job_id}-{scene.scene_id}",
        file_paths=dict(job.file_paths),
        metadata=metadata,
    )


def _resolve_scene_profile(job: VideoJob, scene: SceneSegment) -> str:
    base_profile = _read_text_metadata(job, "script_profile")
    if scene.scene_type == "workflow":
        return "automation"
    if scene.scene_type == "interface":
        return "subtitles"
    if scene.scene_type == "hook":
        return "hooks"
    if scene.scene_type == "proof":
        return "generic"
    if base_profile:
        return base_profile
    return "generic"


def _apply_intra_video_visual_diversity(
    scene_job: VideoJob,
    scene: SceneSegment,
    *,
    previous_scene: SceneSegment | None,
) -> None:
    if previous_scene is None:
        return

    mode = scene.visual_asset_mode
    if mode != previous_scene.visual_asset_mode:
        return

    if previous_scene.visual_theme and not _read_text_metadata(scene_job, "visual_theme"):
        for theme in VISUAL_THEMES:
            if theme != previous_scene.visual_theme:
                scene_job.set_metadata("visual_theme", theme)
                break

    if previous_scene.visual_layout_variant and not _read_text_metadata(scene_job, "visual_layout_variant"):
        variants = VISUAL_LAYOUT_VARIANTS.get(mode, ())
        for variant in variants:
            if variant != previous_scene.visual_layout_variant:
                scene_job.set_metadata("visual_layout_variant", variant)
                break


def _resolve_dominant_scene_field(
    scenes: list[SceneSegment],
    *,
    field_name: str,
) -> str | None:
    counts: dict[str, int] = {}
    for scene in scenes:
        value = getattr(scene, field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def _build_visual_asset_spec(
    job: VideoJob,
) -> tuple[VisualAssetSpec, dict[str, DiversityDecision], object]:
    memory = load_diversity_memory(job)
    profile = _resolve_visual_profile(job)
    mode, mode_decision = _select_visual_asset_mode(job, profile, memory)
    theme, theme_decision = _select_visual_theme(job, mode, memory)
    layout_variant, layout_decision = _select_visual_layout_variant(job, mode, theme, memory)
    proof_mode = _resolve_job_string_metadata(job, "script_proof_mode") or "generic"
    keywords = _resolve_visual_keywords(job)
    tool_label = _resolve_tool_label(job, keywords, profile)
    product_name = _build_product_name(job, profile, mode, tool_label)
    eyebrow = _build_visual_eyebrow(mode, proof_mode)
    title_lines = _build_title_lines(job, profile, mode, keywords, tool_label)
    chips = _build_visual_chips(mode, proof_mode, tool_label, keywords)
    metrics = _build_metric_tiles(job, mode, proof_mode, tool_label)
    table_rows = _build_table_rows(profile, mode, keywords, tool_label)
    workflow_nodes = _build_workflow_nodes(profile, keywords, tool_label)
    highlight_lines = _build_highlight_lines(job, proof_mode, tool_label)
    footer_text = _build_footer_text(mode, proof_mode)
    return VisualAssetSpec(
        mode=mode,
        theme=theme,
        layout_variant=layout_variant,
        profile=profile,
        proof_mode=proof_mode,
        product_name=product_name,
        eyebrow=eyebrow,
        title_lines=title_lines,
        chips=chips,
        metrics=metrics,
        table_rows=table_rows,
        workflow_nodes=workflow_nodes,
        highlight_lines=highlight_lines,
        footer_text=footer_text,
        tool_label=tool_label,
    ), {
        "mode": mode_decision,
        "theme": theme_decision,
        "layout": layout_decision,
    }, memory


def _resolve_visual_profile(job: VideoJob) -> str:
    profile_value = job.metadata.get("script_profile")
    if isinstance(profile_value, str) and profile_value.strip():
        normalized_profile = profile_value.strip()
        if normalized_profile in PROFILE_DEFAULT_MODES:
            return normalized_profile

    lowered_topic = job.topic.lower()
    if "hook" in lowered_topic or "scroll" in lowered_topic:
        return "hooks"
    if "subtitle" in lowered_topic or "caption" in lowered_topic:
        return "subtitles"
    if "idea" in lowered_topic or "videos" in lowered_topic:
        return "repurpose"
    if any(keyword in lowered_topic for keyword in ("automation", "automatisation", "ai", "ia", "workflow", "pipeline", "agent", "ops")):
        return "automation"
    return "generic"


def _select_visual_asset_mode(job: VideoJob, profile: str, memory) -> tuple[str, DiversityDecision]:
    candidates = _resolve_visual_asset_mode_candidates(job, profile)
    decision = memory.choose(
        "visual_asset_mode",
        [
            DiversityCandidate(
                key=mode,
                signals={"visual_asset_mode": mode},
                base_rank=index,
            )
            for index, mode in enumerate(candidates)
        ],
    )
    return decision.selected_key, decision


def _resolve_visual_asset_mode_candidates(job: VideoJob, profile: str) -> list[str]:
    raw_value = job.metadata.get("visual_asset_mode", DEFAULT_VISUAL_ASSET_MODE)
    if isinstance(raw_value, str) and raw_value.strip():
        explicit_mode = VISUAL_ASSET_MODE_ALIASES.get(raw_value.strip().lower())
        if explicit_mode and explicit_mode != "auto":
            return [explicit_mode]

    preferred_mode = _resolve_preferred_visual_asset_mode(job, profile)
    candidates = [preferred_mode]
    for mode in PROFILE_MODE_CANDIDATES[profile]:
        if mode not in candidates:
            candidates.append(mode)
    for mode in VISUAL_ASSET_MODES:
        if mode not in candidates:
            candidates.append(mode)
    return candidates


def _resolve_preferred_visual_asset_mode(job: VideoJob, profile: str) -> str:
    lowered_topic = job.topic.lower()
    if profile == "automation":
        if any(keyword in lowered_topic for keyword in ("workflow", "zapier", "n8n", "make", "pipeline")):
            return "workflow"
        if any(keyword in lowered_topic for keyword in ("dashboard", "audit", "monitor", "metrics", "queue", "ops")):
            return "dashboard"
        if any(keyword in lowered_topic for keyword in ("assistant", "agent", "copilot", "triage", "review")):
            return "ui"
        return "screenshot"
    return PROFILE_DEFAULT_MODES[profile]


def _select_visual_theme(job: VideoJob, mode: str, memory) -> tuple[str, DiversityDecision]:
    candidates = _resolve_visual_theme_candidates(job, mode)
    decision = memory.choose(
        "visual_theme",
        [
            DiversityCandidate(
                key=theme,
                signals={"visual_theme": theme},
                base_rank=index,
            )
            for index, theme in enumerate(candidates)
        ],
    )
    return decision.selected_key, decision


def _resolve_visual_theme_candidates(job: VideoJob, mode: str) -> list[str]:
    theme_value = job.metadata.get("visual_theme")
    if isinstance(theme_value, str) and theme_value.strip():
        normalized_theme = theme_value.strip().lower()
        if normalized_theme in VISUAL_THEMES:
            return [normalized_theme]

    candidates = [MODE_THEME_PREFERENCES.get(mode, DEFAULT_VISUAL_THEME)]
    for theme in VISUAL_THEMES:
        if theme not in candidates:
            candidates.append(theme)
    return candidates


def _select_visual_layout_variant(
    job: VideoJob,
    mode: str,
    theme: str,
    memory,
) -> tuple[str, DiversityDecision]:
    explicit_variant = _read_text_metadata(job, "visual_layout_variant")
    if explicit_variant in VISUAL_LAYOUT_VARIANTS[mode]:
        candidates = [explicit_variant]
    else:
        candidates = list(VISUAL_LAYOUT_VARIANTS[mode])

    decision = memory.choose(
        "visual_layout_signature",
        [
            DiversityCandidate(
                key=variant,
                signals={
                    "visual_layout_signature": _build_visual_layout_signature(mode, variant),
                    "visual_signature": _build_visual_signature(mode, theme, variant),
                },
                base_rank=index,
            )
            for index, variant in enumerate(candidates)
        ],
    )
    return decision.selected_key, decision


def _build_visual_layout_signature(mode: str, layout_variant: str) -> str:
    return f"{mode}:{layout_variant}"


def _build_visual_signature(mode: str, theme: str, layout_variant: str) -> str:
    return f"{mode}|{theme}|{layout_variant}"


def _resolve_visual_keywords(job: VideoJob) -> list[str]:
    explicit_keywords = job.metadata.get("visual_keywords")
    if isinstance(explicit_keywords, list):
        collected = [
            " ".join(str(item).split()).strip()
            for item in explicit_keywords
            if isinstance(item, str) and item.strip()
        ]
        if collected:
            return collected[:5]

    script_keywords = job.metadata.get("script_keywords")
    if isinstance(script_keywords, list):
        collected = [
            " ".join(str(item).split()).strip()
            for item in script_keywords
            if isinstance(item, str) and item.strip()
        ]
        if collected:
            return collected[:5]

    return _extract_keywords(job.topic)


def _extract_keywords(topic: str) -> list[str]:
    keywords: list[str] = []
    for match in WORD_RE.findall(topic):
        normalized_word = match.lower()
        if normalized_word.isdigit():
            continue
        if normalized_word in VISUAL_STOP_WORDS:
            continue
        if len(normalized_word) <= 2:
            continue
        if normalized_word not in keywords:
            keywords.append(normalized_word)
        if len(keywords) == 5:
            break
    return keywords


def _resolve_tool_label(job: VideoJob, keywords: list[str], profile: str) -> str:
    explicit_tool = _read_text_metadata(job, "visual_tool_label")
    if explicit_tool:
        return _normalize_draw_text(explicit_tool)

    proof_mode = _resolve_job_string_metadata(job, "script_proof_mode")
    proof_detail = _read_text_metadata(job, "script_proof_detail")
    if proof_mode == "tool" and proof_detail:
        return _normalize_draw_text(proof_detail)

    proof_tool = _read_text_metadata(job, "script_proof_tool")
    if proof_tool:
        return _normalize_draw_text(proof_tool)

    lowered_topic = job.topic.lower()
    for hint, tool_name in TOOL_HINTS:
        if hint in lowered_topic:
            return _normalize_draw_text(tool_name)

    for keyword in keywords:
        lowered_keyword = keyword.lower()
        for hint, tool_name in TOOL_HINTS:
            if hint == lowered_keyword:
                return _normalize_draw_text(tool_name)

    if profile == "automation":
        return "ZAPIER"
    if profile == "repurpose":
        return "NOTION"
    if profile == "subtitles":
        return "CAPCUT"
    return "KERNYX"


def _build_product_name(job: VideoJob, profile: str, mode: str, tool_label: str) -> str:
    explicit_name = _read_text_metadata(job, "visual_product_name")
    if explicit_name:
        return _normalize_draw_text(explicit_name)

    if mode == "workflow":
        return _short_label(f"{tool_label} FLOW", max_chars=16)
    if mode == "dashboard":
        return "OPS MONITOR"
    if mode == "ui":
        return "AGENT CONSOLE"
    if profile == "subtitles":
        return "CAPTION STUDIO"
    return "AUTOMATION DESK"


def _build_visual_eyebrow(mode: str, proof_mode: str) -> str:
    suffix = PROOF_BADGES.get(proof_mode)
    if suffix:
        return _normalize_draw_text(f"{MODE_LABELS[mode]} {suffix}")
    return _normalize_draw_text(MODE_LABELS[mode])


def _build_title_lines(
    job: VideoJob,
    profile: str,
    mode: str,
    keywords: list[str],
    tool_label: str,
) -> list[str]:
    explicit_title = _read_text_metadata(job, "visual_title_text")
    if explicit_title:
        lines = _wrap_draw_text(explicit_title, max_chars=12, max_lines=3)
        if lines:
            return lines

    if profile == "automation" and mode == "workflow":
        title_source = f"{tool_label} workflow audit"
    elif profile == "automation" and mode == "dashboard":
        title_source = "automation control room"
    elif profile == "automation" and mode == "ui":
        title_source = "agent review queue"
    elif profile == "subtitles":
        title_source = "subtitle flow check"
    elif profile == "hooks":
        title_source = "hook signal test"
    elif keywords:
        title_source = " ".join(keywords[:4])
    else:
        title_source = job.topic

    lines = _wrap_draw_text(title_source, max_chars=12, max_lines=3)
    if lines:
        return lines
    return ["AI", "OPS", "SYSTEM"]


def _build_visual_chips(
    mode: str,
    proof_mode: str,
    tool_label: str,
    keywords: list[str],
) -> list[str]:
    candidates = [mode, proof_mode, tool_label, *keywords]
    chips: list[str] = []
    for candidate in candidates:
        normalized = _short_label(candidate, max_chars=12)
        if not normalized:
            continue
        if normalized not in chips:
            chips.append(normalized)
        if len(chips) == 4:
            break
    return chips


def _build_metric_tiles(job: VideoJob, mode: str, proof_mode: str, tool_label: str) -> list[MetricTile]:
    seed = _build_seed(job)
    number_token = _extract_number_token(job.topic)
    proof_detail = _read_text_metadata(job, "script_proof_detail")
    proof_number = _extract_number_token(proof_detail or "") or number_token

    if proof_mode == "result":
        lead_metric = MetricTile("GAIN", proof_number or str(8 + seed[2] % 14), "FAST")
    elif proof_mode == "tool":
        lead_metric = MetricTile("STACK", str(2 + seed[2] % 4), _short_label(tool_label, max_chars=8))
    elif proof_mode == "field_error":
        lead_metric = MetricTile("FAIL", str(1 + seed[2] % 5), "CHECK")
    elif proof_mode == "example":
        lead_metric = MetricTile("CASE", "1", "REAL")
    else:
        lead_metric = MetricTile("LIVE", str(3 + seed[2] % 7), "NOW")

    if mode == "workflow":
        return [
            lead_metric,
            MetricTile("NODES", str(4 + seed[6] % 3), "MAP"),
            MetricTile("PASS", str(88 + seed[10] % 10), "SAFE"),
        ]
    if mode == "ui":
        return [
            lead_metric,
            MetricTile("QUEUE", str(12 + seed[6] % 22), "OPEN"),
            MetricTile("SLA", str(91 + seed[10] % 8), "FAST"),
        ]
    if mode == "screenshot":
        return [
            lead_metric,
            MetricTile("TOOLS", str(2 + seed[6] % 4), "LIVE"),
            MetricTile("SYNC", str(84 + seed[10] % 14), "OK"),
        ]
    return [
        lead_metric,
        MetricTile("QUEUE", str(4 + seed[6] % 16), "RUN"),
        MetricTile("SYNC", str(86 + seed[10] % 12), "OK"),
    ]


def _build_table_rows(profile: str, mode: str, keywords: list[str], tool_label: str) -> list[TableRow]:
    if profile == "automation":
        base_rows = [
            ("INTAKE", keywords[0] if keywords else "LEADS", "LIVE"),
            ("ROUTER", tool_label, "READY"),
            ("AGENT", keywords[1] if len(keywords) > 1 else "TRIAGE", "RUN"),
            ("REVIEW", keywords[2] if len(keywords) > 2 else "CHECK", "WAIT"),
            ("CRM", keywords[3] if len(keywords) > 3 else "SYNC", "DONE"),
        ]
    elif profile == "subtitles":
        base_rows = [
            ("CAPTION", "BLOCKS", "LIVE"),
            ("PACE", "READ", "CHECK"),
            ("HIGHLIGHT", "WORDS", "READY"),
            ("EXPORT", "SRT", "DONE"),
        ]
    elif profile == "hooks":
        base_rows = [
            ("HOOK", "ANGLE", "LIVE"),
            ("PROMISE", "RESULT", "READY"),
            ("TEST", "VERSION", "RUN"),
            ("KEEP", "WINNER", "DONE"),
        ]
    else:
        base_rows = [
            ("SYSTEM", keywords[0] if keywords else "CORE", "LIVE"),
            ("PROOF", keywords[1] if len(keywords) > 1 else "EVIDENCE", "READY"),
            ("ACTION", keywords[2] if len(keywords) > 2 else "MOVE", "RUN"),
            ("OUTPUT", keywords[3] if len(keywords) > 3 else "SHIP", "DONE"),
        ]

    rows = [
        TableRow(
            label=_short_label(label, max_chars=10),
            detail=_short_label(detail, max_chars=10),
            state=_short_label(state, max_chars=8),
        )
        for label, detail, state in base_rows
    ]

    if mode == "dashboard":
        return rows[:4]
    if mode == "workflow":
        return rows[:5]
    return rows[:4]


def _build_workflow_nodes(profile: str, keywords: list[str], tool_label: str) -> list[WorkflowNode]:
    if profile == "automation":
        raw_nodes = [
            ("TRIGGER", keywords[0] if keywords else "INPUT", "LIVE"),
            (_short_label(tool_label, max_chars=10), "ROUTE", "READY"),
            ("AGENT", keywords[1] if len(keywords) > 1 else "TRIAGE", "RUN"),
            ("REVIEW", "HUMAN", "CHECK"),
            ("CRM", keywords[2] if len(keywords) > 2 else "SYNC", "DONE"),
        ]
    elif profile == "subtitles":
        raw_nodes = [
            ("SCRIPT", "LOAD", "LIVE"),
            ("CAPTION", "CUT", "READY"),
            ("EMPHASIS", "WORDS", "RUN"),
            ("REVIEW", "FLOW", "CHECK"),
            ("EXPORT", "SRT", "DONE"),
        ]
    elif profile == "hooks":
        raw_nodes = [
            ("HOOK", "DRAFT", "LIVE"),
            ("ANGLE", "MATCH", "READY"),
            ("BENEFIT", "CHECK", "RUN"),
            ("TEST", "OPEN", "CHECK"),
            ("POST", "WIN", "DONE"),
        ]
    else:
        raw_nodes = [
            ("INPUT", "LOAD", "LIVE"),
            ("PROOF", "CHECK", "READY"),
            ("BUILD", "FLOW", "RUN"),
            ("REVIEW", "HUMAN", "CHECK"),
            ("SHIP", "DONE", "DONE"),
        ]
    return [
        WorkflowNode(
            title=_short_label(title, max_chars=10),
            subtitle=_short_label(subtitle, max_chars=10),
            state=_short_label(state, max_chars=8),
        )
        for title, subtitle, state in raw_nodes
    ]


def _build_highlight_lines(job: VideoJob, proof_mode: str, tool_label: str) -> list[str]:
    explicit_highlight = _read_text_metadata(job, "visual_highlight_text")
    if explicit_highlight:
        lines = _wrap_draw_text(explicit_highlight, max_chars=18, max_lines=3)
        if lines:
            return lines

    proof_detail = _read_text_metadata(job, "script_proof_detail")
    if proof_detail:
        lines = _wrap_draw_text(proof_detail, max_chars=18, max_lines=3)
        if lines:
            return lines

    if proof_mode == "tool":
        return _wrap_draw_text(f"stack visible with {tool_label}", max_chars=18, max_lines=3)
    if proof_mode == "result":
        return _wrap_draw_text("result visible in first screen", max_chars=18, max_lines=3)
    if proof_mode == "field_error":
        return _wrap_draw_text("field error exposed before publish", max_chars=18, max_lines=3)
    return _wrap_draw_text("one concrete proof inside the frame", max_chars=18, max_lines=3)


def _build_footer_text(mode: str, proof_mode: str) -> str:
    if proof_mode == "tool":
        return "SYSTEM STACK LIVE"
    if proof_mode == "result":
        return "MEASURABLE SIGNAL"
    if proof_mode == "field_error":
        return "FIELD CHECK BEFORE SHIP"
    if mode == "workflow":
        return "ORCHESTRATE REVIEW SHIP"
    if mode == "ui":
        return "OPERATE TRIAGE DECIDE"
    if mode == "screenshot":
        return "PSEUDO SCREEN REAL SIGNAL"
    return "MONITOR PROOF ACTION"


def _resolve_job_string_metadata(job: VideoJob, key: str) -> str | None:
    value = job.metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def _read_text_metadata(job: VideoJob, key: str) -> str | None:
    value = job.metadata.get(key)
    if isinstance(value, str) and value.strip():
        return " ".join(value.strip().split())
    return None


def _read_optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _extract_number_token(topic: str) -> str | None:
    match = NUMBER_RE.search(topic)
    return match.group(0) if match else None


def _short_label(text: str, *, max_chars: int) -> str:
    normalized = _normalize_draw_text(text)
    if len(normalized) <= max_chars:
        return normalized
    words = normalized.split()
    if not words:
        return normalized[:max_chars].strip()
    output_words: list[str] = []
    current_length = 0
    for word in words:
        additional = len(word) if not output_words else len(word) + 1
        if output_words and current_length + additional > max_chars:
            break
        output_words.append(word)
        current_length += additional
    if output_words:
        return " ".join(output_words)
    return normalized[:max_chars].strip()


def _generate_visual_asset(job: VideoJob, output_path: Path, spec: VisualAssetSpec) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seed = _build_seed(job)
    palette = _build_palette(seed, spec.theme)

    width = TARGET_WIDTH
    height = TARGET_HEIGHT
    pixel_data = bytearray(width * height * 3)

    _paint_background(pixel_data, width, height, palette, seed)

    if spec.mode == "workflow":
        _render_workflow_surface(pixel_data, width, height, palette, spec, seed)
    elif spec.mode == "ui":
        _render_ui_surface(pixel_data, width, height, palette, spec, seed)
    elif spec.mode == "screenshot":
        _render_screenshot_surface(pixel_data, width, height, palette, spec, seed)
    else:
        _render_dashboard_surface(pixel_data, width, height, palette, spec, seed)

    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    output_path.write_bytes(header + pixel_data)


def _build_seed(job: VideoJob) -> bytes:
    payload = f"{job.job_id}|{job.topic}|{job.template}|{job.account}".encode("utf-8")
    return hashlib.sha256(payload).digest()


def _build_palette(seed: bytes, theme: str) -> Palette:
    if theme == "midnight":
        return Palette(
            background_top=(8 + seed[0] % 10, 14 + seed[4] % 16, 28 + seed[8] % 20),
            background_bottom=(4 + seed[12] % 8, 8 + seed[16] % 12, 18 + seed[20] % 14),
            glow=(92 + seed[24] % 34, 196 + seed[28] % 36, 224 + seed[2] % 20),
            glow_alt=(62 + seed[6] % 24, 112 + seed[9] % 26, 214 + seed[11] % 20),
            surface=(234, 240, 248),
            surface_alt=(20, 30, 52),
            surface_soft=(244, 248, 252),
            chrome=(226, 234, 244),
            border=(128, 170, 208),
            text_dark=(16, 24, 36),
            text_muted=(74, 88, 112),
            text_light=(242, 248, 252),
            accent=(62, 204, 226),
            accent_soft=(176, 238, 246),
            success=(78, 214, 156),
            warning=(246, 188, 78),
            danger=(248, 112, 120),
        )
    if theme == "mint":
        return Palette(
            background_top=(12 + seed[0] % 12, 34 + seed[4] % 18, 28 + seed[8] % 14),
            background_bottom=(8 + seed[12] % 8, 18 + seed[16] % 12, 16 + seed[20] % 10),
            glow=(108 + seed[24] % 34, 224 + seed[28] % 20, 186 + seed[2] % 22),
            glow_alt=(86 + seed[6] % 18, 168 + seed[9] % 20, 144 + seed[11] % 18),
            surface=(242, 248, 244),
            surface_alt=(22, 46, 44),
            surface_soft=(248, 252, 250),
            chrome=(228, 244, 236),
            border=(118, 182, 162),
            text_dark=(18, 34, 30),
            text_muted=(74, 98, 92),
            text_light=(244, 250, 246),
            accent=(44, 190, 142),
            accent_soft=(188, 242, 220),
            success=(68, 204, 142),
            warning=(244, 182, 86),
            danger=(240, 112, 122),
        )
    return Palette(
        background_top=(18 + seed[0] % 18, 24 + seed[4] % 20, 44 + seed[8] % 26),
        background_bottom=(8 + seed[12] % 10, 10 + seed[16] % 10, 24 + seed[20] % 12),
        glow=(116 + seed[24] % 26, 150 + seed[28] % 28, 255),
        glow_alt=(86 + seed[6] % 20, 104 + seed[9] % 24, 236 + seed[11] % 16),
        surface=(244, 246, 250),
        surface_alt=(20, 24, 42),
        surface_soft=(248, 250, 252),
        chrome=(226, 230, 244),
        border=(132, 144, 236),
        text_dark=(18, 22, 32),
        text_muted=(82, 88, 118),
        text_light=(244, 248, 252),
        accent=(104, 118, 255),
        accent_soft=(212, 218, 255),
        success=(88, 208, 156),
        warning=(246, 188, 82),
        danger=(248, 114, 124),
    )


def _paint_background(
    pixel_data: bytearray,
    width: int,
    height: int,
    palette: Palette,
    seed: bytes,
) -> None:
    for y in range(height):
        y_ratio = y / max(1, height - 1)
        row_color = _lerp_color(palette.background_top, palette.background_bottom, y_ratio)
        for x in range(width):
            x_ratio = x / max(1, width - 1)
            color = _lerp_color(row_color, palette.glow_alt, (x_ratio * 0.3 + y_ratio * 0.1) * 0.18)
            glow_strength = _ellipse_strength(
                x=x,
                y=y,
                center_x=width * 0.76,
                center_y=height * 0.18,
                radius_x=width * 0.42,
                radius_y=height * 0.18,
            )
            if glow_strength > 0.0:
                color = _mix_color(color, palette.glow, glow_strength * 0.24)
            glow_strength_alt = _ellipse_strength(
                x=x,
                y=y,
                center_x=width * 0.18,
                center_y=height * 0.82,
                radius_x=width * 0.32,
                radius_y=height * 0.18,
            )
            if glow_strength_alt > 0.0:
                color = _mix_color(color, palette.glow_alt, glow_strength_alt * 0.16)
            stripe_strength = _stripe_strength(x=x, y=y, width=width, height=height)
            if stripe_strength > 0.0:
                color = _mix_color(color, palette.border, stripe_strength * 0.45)
            if (x * 3 + y * 5 + seed[0]) % 97 == 0:
                color = _mix_color(color, palette.surface_soft, 0.04)
            _set_pixel(pixel_data, width, x, y, color)


def _render_dashboard_surface(
    pixel_data: bytearray,
    width: int,
    height: int,
    palette: Palette,
    spec: VisualAssetSpec,
    seed: bytes,
) -> None:
    if spec.layout_variant == "chart_focus":
        metrics_y = 392
        chart_box = (194, 540, 644, 900)
        events_box = (506, 920, 644, 1088)
        table_box = (194, 920, 484, 1088)
        highlight_box = (194, 1110, 644, 1172)
        footer_y = 1190
    else:
        metrics_y = 420
        chart_box = (194, 570, 484, 846)
        events_box = (506, 570, 644, 1040)
        table_box = (194, 872, 644, 1086)
        highlight_box = (194, 1108, 644, 1170)
        footer_y = 1190

    window = (36, 64, width - 36, height - 64)
    _draw_panel(pixel_data, width, height, *window, radius=34, fill=palette.surface, border=palette.border, shadow=True)
    _draw_panel(pixel_data, width, height, 36, 64, width - 36, 136, radius=34, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, 36, 136, 168, height - 64, radius=0, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_toolbar_dots(pixel_data, width, 70, 98, palette)
    _draw_text(pixel_data, width, height, 182, 94, spec.product_name, scale=3, color=palette.text_dark)
    _draw_chip_row(pixel_data, width, height, width - 266, 90, spec.chips, palette, max_width=206)

    _draw_text(pixel_data, width, height, 194, 164, spec.eyebrow, scale=2, color=palette.accent)
    _draw_text_lines(pixel_data, width, height, 194, 202, spec.title_lines, scale=4, line_gap=76, color=palette.text_dark)

    sidebar_items = ["OVERVIEW", "WORKFLOW", "ALERTS", "PROOF", "EXPORT"]
    item_y = 206
    for index, item in enumerate(sidebar_items):
        accent = palette.accent if index == 0 else palette.text_light
        _draw_status_dot(pixel_data, width, 68, item_y + 12, 6, palette.accent_soft if index == 0 else palette.text_muted)
        _draw_text(pixel_data, width, height, 84, item_y, item, scale=2, color=accent)
        item_y += 72
    _draw_text(pixel_data, width, height, 64, height - 168, "LIVE STACK", scale=2, color=palette.text_light)
    _draw_text(pixel_data, width, height, 64, height - 128, _short_label(spec.tool_label, max_chars=10), scale=3, color=palette.accent_soft)

    tile_x = 194
    for metric in spec.metrics:
        _draw_metric_tile(pixel_data, width, height, tile_x, metrics_y, tile_x + 136, metrics_y + 124, palette, metric)
        tile_x += 148

    _draw_chart_panel(pixel_data, width, height, *chart_box, palette, "LIVE METRICS", spec.highlight_lines, seed)
    _draw_events_panel(pixel_data, width, height, *events_box, palette, "EVENTS", spec.table_rows)
    _draw_table_panel(pixel_data, width, height, *table_box, palette, "QUEUE", spec.table_rows)
    _draw_highlight_strip(pixel_data, width, height, *highlight_box, palette, spec.highlight_lines)
    _draw_text(pixel_data, width, height, 198, footer_y, spec.footer_text, scale=2, color=palette.text_muted)


def _render_workflow_surface(
    pixel_data: bytearray,
    width: int,
    height: int,
    palette: Palette,
    spec: VisualAssetSpec,
    seed: bytes,
) -> None:
    if spec.layout_variant == "proof_focus":
        left_panel = (48, 174, 148, 820)
        center_panel = (164, 174, 470, 820)
        proof_panel = (488, 174, 672, 820)
        bottom_panel = (164, 852, 672, 1156)
        canvas_box = (182, 352, 450, 796)
        proof_x = 504
        table_box = (182, 878, 654, 1128)
        highlight_box = (182, 1138, 654, 1186)
        footer_x = 186
    else:
        left_panel = (48, 174, 148, 820)
        center_panel = (164, 174, 560, 820)
        proof_panel = (578, 174, 672, 820)
        bottom_panel = (164, 852, 672, 1156)
        canvas_box = (184, 352, 540, 796)
        proof_x = 592
        table_box = (182, 878, 654, 1128)
        highlight_box = (182, 1138, 654, 1186)
        footer_x = 186

    window = (32, 70, width - 32, height - 70)
    _draw_panel(pixel_data, width, height, *window, radius=34, fill=palette.surface, border=palette.border, shadow=True)
    _draw_panel(pixel_data, width, height, 32, 70, width - 32, 142, radius=34, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_toolbar_dots(pixel_data, width, 66, 104, palette)
    _draw_text(pixel_data, width, height, 184, 98, spec.product_name, scale=3, color=palette.text_dark)
    _draw_chip_row(pixel_data, width, height, width - 280, 94, spec.chips, palette, max_width=220)

    _draw_panel(pixel_data, width, height, *left_panel, radius=24, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_panel(pixel_data, width, height, *center_panel, radius=26, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, *proof_panel, radius=24, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_panel(pixel_data, width, height, *bottom_panel, radius=26, fill=palette.surface_soft, border=palette.border, shadow=False)

    _draw_text(pixel_data, width, height, 62, 202, "MAP", scale=2, color=palette.accent_soft)
    metric_y = 252
    for metric in spec.metrics:
        _draw_text(pixel_data, width, height, 62, metric_y, metric.label, scale=2, color=palette.text_light)
        _draw_text(pixel_data, width, height, 62, metric_y + 26, metric.value, scale=3, color=palette.accent_soft)
        _draw_text(pixel_data, width, height, 62, metric_y + 66, metric.status, scale=2, color=palette.text_muted)
        metric_y += 110

    _draw_text(pixel_data, width, height, 182, 202, spec.eyebrow, scale=2, color=palette.accent)
    _draw_text_lines(pixel_data, width, height, 182, 240, spec.title_lines, scale=3, line_gap=64, color=palette.text_dark)
    _draw_workflow_canvas(pixel_data, width, height, *canvas_box, palette, spec.workflow_nodes)

    _draw_text(pixel_data, width, height, proof_x + 2, 202, "PROOF", scale=2, color=palette.accent_soft)
    _draw_text_lines(pixel_data, width, height, proof_x, 248, spec.highlight_lines, scale=2, line_gap=34, color=palette.text_light)
    _draw_text(pixel_data, width, height, proof_x, 372, "TOOL", scale=2, color=palette.text_muted)
    _draw_text(pixel_data, width, height, proof_x, 408, _short_label(spec.tool_label, max_chars=10), scale=2, color=palette.text_light)
    _draw_mini_progress_stack(pixel_data, width, height, proof_x, 486, proof_x + 58, 666, palette, seed)

    _draw_table_panel(pixel_data, width, height, *table_box, palette, "EVENT LOG", spec.table_rows)
    _draw_highlight_strip(pixel_data, width, height, *highlight_box, palette, spec.highlight_lines)
    _draw_text(pixel_data, width, height, footer_x, 1196, spec.footer_text, scale=2, color=palette.text_muted)


def _render_ui_surface(
    pixel_data: bytearray,
    width: int,
    height: int,
    palette: Palette,
    spec: VisualAssetSpec,
    seed: bytes,
) -> None:
    if spec.layout_variant == "inspector_focus":
        left_panel = (170, 156, 360, 1126)
        right_panel = (378, 156, 694, 1126)
        metric_x = 190
        list_box = (188, 318, 342, 1092)
        title_x = 398
        highlight_box = (396, 392, 676, 568)
        chart_box = (396, 596, 676, 866)
        detail_box = (396, 894, 676, 1084)
        footer_left = 188
        footer_right = 390
    else:
        left_panel = (170, 156, 424, 1126)
        right_panel = (442, 156, 694, 1126)
        metric_x = 190
        list_box = (188, 318, 406, 1092)
        title_x = 462
        highlight_box = (460, 420, 676, 560)
        chart_box = (460, 588, 676, 848)
        detail_box = (460, 878, 676, 1068)
        footer_left = 188
        footer_right = 454

    _draw_panel(pixel_data, width, height, 26, 42, width - 26, height - 42, radius=36, fill=palette.surface, border=palette.border, shadow=True)
    _draw_panel(pixel_data, width, height, 26, 42, width - 26, 120, radius=36, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, 26, 120, 152, height - 42, radius=0, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_panel(pixel_data, width, height, *left_panel, radius=28, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, *right_panel, radius=28, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, 170, 1148, 694, 1212, radius=22, fill=palette.chrome, border=palette.border, shadow=False)

    _draw_toolbar_dots(pixel_data, width, 60, 82, palette)
    _draw_text(pixel_data, width, height, 178, 72, spec.product_name, scale=3, color=palette.text_dark)
    _draw_chip_row(pixel_data, width, height, width - 292, 68, spec.chips, palette, max_width=232)

    nav_items = ["QUEUE", "INBOX", "REVIEW", "EXPORT"]
    nav_y = 182
    for index, item in enumerate(nav_items):
        _draw_status_dot(pixel_data, width, 54, nav_y + 12, 6, palette.accent_soft if index == 1 else palette.text_muted)
        _draw_text(pixel_data, width, height, 70, nav_y, item, scale=2, color=palette.text_light)
        nav_y += 72

    _draw_text(pixel_data, width, height, 190, 182, "TASK QUEUE", scale=2, color=palette.accent)
    _draw_metric_row(pixel_data, width, height, metric_x, 216, spec.metrics, palette)
    _draw_list_surface(pixel_data, width, height, *list_box, palette, spec.table_rows, seed)

    _draw_text(pixel_data, width, height, title_x, 182, spec.eyebrow, scale=2, color=palette.accent)
    _draw_text_lines(pixel_data, width, height, title_x, 220, spec.title_lines, scale=3, line_gap=58, color=palette.text_dark)
    _draw_highlight_block(pixel_data, width, height, *highlight_box, palette, spec.highlight_lines)
    _draw_chart_panel(pixel_data, width, height, *chart_box, palette, "SYNC HEALTH", spec.highlight_lines, seed)
    _draw_detail_rows(pixel_data, width, height, *detail_box, palette, spec.table_rows)
    _draw_text(pixel_data, width, height, footer_left, 1170, "READY TO SHIP", scale=2, color=palette.text_muted)
    _draw_text(
        pixel_data,
        width,
        height,
        footer_right,
        1170,
        _short_label(spec.footer_text, max_chars=18),
        scale=2,
        color=palette.text_dark,
    )


def _render_screenshot_surface(
    pixel_data: bytearray,
    width: int,
    height: int,
    palette: Palette,
    spec: VisualAssetSpec,
    seed: bytes,
) -> None:
    if spec.layout_variant == "browser_spotlight":
        left_nav = (98, 474, 166, 1046)
        center_panel = (178, 474, 432, 866)
        right_panel = (446, 474, 622, 1046)
        lower_panel = (178, 882, 432, 1046)
        highlight_box = (372, 824, 606, 968)
        chart_box = (196, 506, 414, 836)
        detail_box = (196, 900, 414, 1028)
        events_box = (458, 540, 610, 1028)
    else:
        left_nav = (98, 474, 184, 1046)
        center_panel = (196, 474, 468, 866)
        right_panel = (482, 474, 622, 1046)
        lower_panel = (196, 882, 468, 1046)
        highlight_box = (398, 864, 596, 958)
        chart_box = (214, 506, 450, 836)
        detail_box = (214, 900, 450, 1028)
        events_box = (494, 540, 610, 1028)

    _draw_text(pixel_data, width, height, 86, 92, spec.eyebrow, scale=2, color=palette.accent_soft)
    _draw_text_lines(pixel_data, width, height, 82, 136, spec.title_lines, scale=4, line_gap=76, color=palette.text_light)
    _draw_chip_row(pixel_data, width, height, 84, 330, spec.chips, palette, max_width=320, dark=True)

    screenshot = (76, 384, width - 76, 1084)
    _draw_panel(pixel_data, width, height, *screenshot, radius=30, fill=palette.surface, border=palette.border, shadow=True)
    _draw_panel(pixel_data, width, height, 76, 384, width - 76, 450, radius=30, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_toolbar_dots(pixel_data, width, 110, 414, palette)
    _draw_address_bar(pixel_data, width, height, 186, 400, width - 116, 434, palette, spec.product_name)

    _draw_panel(pixel_data, width, height, *left_nav, radius=20, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_panel(pixel_data, width, height, *center_panel, radius=22, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, *right_panel, radius=22, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, *lower_panel, radius=22, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_panel(pixel_data, width, height, 382, 842, 612, 980, radius=22, fill=palette.chrome, border=palette.border, shadow=True)

    _draw_text(pixel_data, width, height, 110, 500, "TABS", scale=2, color=palette.accent_soft)
    tab_y = 552
    for chip in spec.chips:
        _draw_text(pixel_data, width, height, 110, tab_y, _short_label(chip, max_chars=8), scale=2, color=palette.text_light)
        tab_y += 64

    _draw_chart_panel(pixel_data, width, height, *chart_box, palette, "CONTROL ROOM", spec.highlight_lines, seed)
    _draw_detail_rows(pixel_data, width, height, *detail_box, palette, spec.table_rows)
    _draw_text(pixel_data, width, height, 500, 506, "ALERTS", scale=2, color=palette.accent)
    _draw_events_panel(pixel_data, width, height, *events_box, palette, "LIVE", spec.table_rows)
    _draw_highlight_block(pixel_data, width, height, *highlight_box, palette, spec.highlight_lines)

    _draw_metric_row(pixel_data, width, height, 100, 1106, spec.metrics, palette, dark=True)
    _draw_text(pixel_data, width, height, 86, 1188, spec.footer_text, scale=2, color=palette.text_light)


def _draw_metric_row(
    pixel_data: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    metrics: list[MetricTile],
    palette: Palette,
    *,
    dark: bool = False,
) -> None:
    tile_x = x
    tile_fill = palette.surface_alt if dark else palette.surface_soft
    for metric in metrics:
        _draw_panel(pixel_data, width, height, tile_x, y, tile_x + 116, y + 78, radius=18, fill=tile_fill, border=palette.border, shadow=False)
        value_color = palette.text_light if dark else palette.text_dark
        label_color = palette.accent_soft if dark else palette.accent
        status_color = palette.text_muted if not dark else palette.text_light
        _draw_text(pixel_data, width, height, tile_x + 12, y + 12, metric.label, scale=2, color=label_color)
        _draw_text(pixel_data, width, height, tile_x + 12, y + 34, metric.value, scale=3, color=value_color)
        _draw_text(pixel_data, width, height, tile_x + 68, y + 48, metric.status, scale=2, color=status_color)
        tile_x += 128


def _draw_metric_tile(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    metric: MetricTile,
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=22, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 16, y0 + 18, metric.label, scale=2, color=palette.accent)
    _draw_text(pixel_data, width, height, x0 + 16, y0 + 50, metric.value, scale=4, color=palette.text_dark)
    _draw_text(pixel_data, width, height, x0 + 16, y0 + 96, metric.status, scale=2, color=palette.text_muted)


def _draw_chart_panel(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    title: str,
    notes: list[str],
    seed: bytes,
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=24, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 18, title, scale=2, color=palette.accent)
    note_line = notes[0] if notes else "ONE PROOF INSIDE"
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 46, _short_label(note_line, max_chars=18), scale=2, color=palette.text_muted)

    chart_x0 = x0 + 24
    chart_y0 = y0 + 90
    chart_x1 = x1 - 22
    chart_y1 = y1 - 24
    for guide_index in range(4):
        guide_y = chart_y0 + int((chart_y1 - chart_y0) * guide_index / 3)
        _draw_line(pixel_data, width, chart_x0, guide_y, chart_x1, guide_y, color=palette.border, thickness=1, alpha=0.28)

    point_count = 6
    span = max(1, chart_x1 - chart_x0)
    points: list[tuple[int, int]] = []
    for index in range(point_count):
        metric = 32 + seed[(index * 3) % len(seed)] % 58
        px = chart_x0 + int(span * index / max(1, point_count - 1))
        py = chart_y1 - int((chart_y1 - chart_y0) * metric / 100)
        points.append((px, py))

    for index in range(point_count - 1):
        _draw_line(
            pixel_data,
            width,
            points[index][0],
            points[index][1],
            points[index + 1][0],
            points[index + 1][1],
            color=palette.accent,
            thickness=4,
        )
    for point_x, point_y in points:
        _draw_circle(pixel_data, width, height, point_x, point_y, 6, palette.surface_soft)
        _draw_circle(pixel_data, width, height, point_x, point_y, 4, palette.accent)

    bar_x = chart_x0
    bar_width = 18
    for index in range(5):
        value = 28 + seed[(index * 5 + 7) % len(seed)] % 44
        bar_height = int((chart_y1 - chart_y0) * value / 100)
        _draw_panel(
            pixel_data,
            width,
            height,
            bar_x,
            chart_y1 - bar_height,
            bar_x + bar_width,
            chart_y1,
            radius=8,
            fill=palette.accent_soft,
            border=palette.accent_soft,
            shadow=False,
        )
        bar_x += 28


def _draw_events_panel(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    title: str,
    rows: list[TableRow],
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=24, fill=palette.surface_alt, border=palette.surface_alt, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 14, y0 + 18, title, scale=2, color=palette.accent_soft)
    row_y = y0 + 66
    for index, row in enumerate(rows):
        if row_y + 74 > y1:
            break
        state_color = _state_color(palette, row.state)
        _draw_status_dot(pixel_data, width, x0 + 18, row_y + 10, 5, state_color)
        _draw_text(pixel_data, width, height, x0 + 34, row_y, row.label, scale=2, color=palette.text_light)
        _draw_text(pixel_data, width, height, x0 + 34, row_y + 28, row.detail, scale=2, color=palette.text_muted)
        if index < len(rows) - 1:
            _draw_line(pixel_data, width, x0 + 14, row_y + 56, x1 - 14, row_y + 56, color=palette.chrome, thickness=1, alpha=0.20)
        row_y += 88


def _draw_table_panel(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    title: str,
    rows: list[TableRow],
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=24, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 18, title, scale=2, color=palette.accent)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 46, "STAGE", scale=2, color=palette.text_muted)
    _draw_text(pixel_data, width, height, x0 + 220, y0 + 46, "DETAIL", scale=2, color=palette.text_muted)
    _draw_text(pixel_data, width, height, x1 - 104, y0 + 46, "STATE", scale=2, color=palette.text_muted)

    row_y = y0 + 84
    for row in rows:
        if row_y + 50 > y1:
            break
        _draw_text(pixel_data, width, height, x0 + 18, row_y, row.label, scale=2, color=palette.text_dark)
        _draw_text(pixel_data, width, height, x0 + 220, row_y, row.detail, scale=2, color=palette.text_dark)
        state_color = _state_color(palette, row.state)
        _draw_status_dot(pixel_data, width, x1 - 100, row_y + 8, 5, state_color)
        _draw_text(pixel_data, width, height, x1 - 84, row_y, row.state, scale=2, color=palette.text_muted)
        _draw_line(pixel_data, width, x0 + 18, row_y + 34, x1 - 18, row_y + 34, color=palette.border, thickness=1, alpha=0.28)
        row_y += 42


def _draw_workflow_canvas(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    nodes: list[WorkflowNode],
) -> None:
    for column in range(x0 + 18, x1, 56):
        _draw_line(pixel_data, width, column, y0 + 18, column, y1 - 18, color=palette.border, thickness=1, alpha=0.14)
    for row in range(y0 + 18, y1, 56):
        _draw_line(pixel_data, width, x0 + 18, row, x1 - 18, row, color=palette.border, thickness=1, alpha=0.14)

    node_size = (104, 84)
    canvas_width = max(node_size[0] + 24, x1 - x0)
    canvas_height = max(node_size[1] * 2, y1 - y0)
    top_y = y0 + min(40, max(24, canvas_height // 8))
    bottom_y = y0 + max(180, min(canvas_height - node_size[1] - 24, canvas_height // 2))
    positions = [
        (x0 + 8, top_y),
        (x0 + canvas_width // 2 - node_size[0] // 2, top_y),
        (x1 - node_size[0] - 8, top_y),
        (x0 + canvas_width // 3 - node_size[0] // 2, bottom_y),
        (x0 + (canvas_width * 2) // 3 - node_size[0] // 2, bottom_y),
    ]
    drawn_positions: list[tuple[int, int, int, int]] = []
    for node, (node_x, node_y) in zip(nodes, positions):
        node_box = (node_x, node_y, node_x + node_size[0], node_y + node_size[1])
        drawn_positions.append(node_box)
        _draw_workflow_node(pixel_data, width, height, *node_box, palette, node)

    connection_pairs = ((0, 1), (1, 2), (1, 3), (3, 4))
    for start_index, end_index in connection_pairs:
        if start_index >= len(drawn_positions) or end_index >= len(drawn_positions):
            continue
        start_box = drawn_positions[start_index]
        end_box = drawn_positions[end_index]
        _draw_connector(
            pixel_data,
            width,
            (start_box[0] + start_box[2]) // 2,
            start_box[3],
            (end_box[0] + end_box[2]) // 2,
            end_box[1],
            palette.accent,
        )


def _draw_workflow_node(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    node: WorkflowNode,
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=20, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_status_dot(pixel_data, width, x0 + 18, y0 + 18, 6, _state_color(palette, node.state))
    _draw_text(pixel_data, width, height, x0 + 32, y0 + 14, node.title, scale=2, color=palette.text_dark)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 42, node.subtitle, scale=2, color=palette.text_muted)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 62, node.state, scale=2, color=palette.accent)


def _draw_connector(
    pixel_data: bytearray,
    width: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    color: tuple[int, int, int],
) -> None:
    mid_y = start_y + (end_y - start_y) // 2
    _draw_line(pixel_data, width, start_x, start_y, start_x, mid_y, color=color, thickness=4)
    _draw_line(pixel_data, width, start_x, mid_y, end_x, mid_y, color=color, thickness=4)
    _draw_line(pixel_data, width, end_x, mid_y, end_x, end_y, color=color, thickness=4)


def _draw_list_surface(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    rows: list[TableRow],
    seed: bytes,
) -> None:
    _draw_text(pixel_data, width, height, x0 + 8, y0, "SELECTED KPIS", scale=2, color=palette.text_muted)
    _draw_mini_progress_stack(pixel_data, width, height, x0 + 8, y0 + 26, x1 - 8, y0 + 160, palette, seed)
    row_y = y0 + 190
    for index, row in enumerate(rows):
        row_panel_fill = palette.chrome if index == 1 else palette.surface
        _draw_panel(pixel_data, width, height, x0 + 8, row_y, x1 - 8, row_y + 92, radius=18, fill=row_panel_fill, border=palette.border, shadow=False)
        state_color = _state_color(palette, row.state)
        _draw_status_dot(pixel_data, width, x0 + 24, row_y + 24, 6, state_color)
        _draw_text(pixel_data, width, height, x0 + 40, row_y + 16, row.label, scale=2, color=palette.text_dark)
        _draw_text(pixel_data, width, height, x0 + 40, row_y + 44, row.detail, scale=2, color=palette.text_muted)
        _draw_text(pixel_data, width, height, x1 - 80, row_y + 30, row.state, scale=2, color=palette.accent)
        row_y += 108
        if row_y + 92 > y1:
            break


def _draw_detail_rows(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    rows: list[TableRow],
) -> None:
    _draw_text(pixel_data, width, height, x0 + 8, y0, "LAST SYNC", scale=2, color=palette.text_muted)
    row_y = y0 + 34
    for row in rows[:4]:
        _draw_text(pixel_data, width, height, x0 + 8, row_y, row.label, scale=2, color=palette.text_dark)
        _draw_text(pixel_data, width, height, x0 + 106, row_y, row.detail, scale=2, color=palette.text_muted)
        _draw_status_dot(pixel_data, width, x1 - 18, row_y + 8, 5, _state_color(palette, row.state))
        _draw_line(pixel_data, width, x0 + 8, row_y + 28, x1 - 8, row_y + 28, color=palette.border, thickness=1, alpha=0.26)
        row_y += 40
        if row_y + 32 > y1:
            break


def _draw_highlight_strip(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    lines: list[str],
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=18, fill=palette.accent_soft, border=palette.accent_soft, shadow=False)
    if not lines:
        return
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 18, _short_label(lines[0], max_chars=32), scale=2, color=palette.text_dark)


def _draw_highlight_block(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    lines: list[str],
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=22, fill=palette.chrome, border=palette.border, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 14, y0 + 16, "PROOF", scale=2, color=palette.accent)
    _draw_text_lines(pixel_data, width, height, x0 + 14, y0 + 48, lines[:3], scale=2, line_gap=30, color=palette.text_dark)


def _draw_mini_progress_stack(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    seed: bytes,
) -> None:
    progress_y = y0
    for index in range(4):
        if progress_y + 28 > y1:
            break
        _draw_text(pixel_data, width, height, x0, progress_y, f"STEP {index + 1}", scale=2, color=palette.text_muted)
        bar_x0 = x0
        bar_y0 = progress_y + 18
        bar_x1 = x1
        bar_y1 = progress_y + 28
        _draw_panel(pixel_data, width, height, bar_x0, bar_y0, bar_x1, bar_y1, radius=6, fill=palette.chrome, border=palette.border, shadow=False)
        fill_width = int((bar_x1 - bar_x0) * (0.36 + (seed[index * 4] % 48) / 100))
        _draw_panel(pixel_data, width, height, bar_x0, bar_y0, bar_x0 + fill_width, bar_y1, radius=6, fill=palette.accent, border=palette.accent, shadow=False)
        progress_y += 42


def _draw_address_bar(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    palette: Palette,
    product_name: str,
) -> None:
    _draw_panel(pixel_data, width, height, x0, y0, x1, y1, radius=16, fill=palette.surface_soft, border=palette.border, shadow=False)
    _draw_text(pixel_data, width, height, x0 + 18, y0 + 10, _short_label(product_name, max_chars=18), scale=2, color=palette.text_muted)


def _draw_chip_row(
    pixel_data: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    chips: list[str],
    palette: Palette,
    *,
    max_width: int,
    dark: bool = False,
) -> None:
    cursor_x = x
    chip_fill = palette.surface_alt if dark else palette.accent_soft
    chip_border = palette.border if not dark else palette.surface_alt
    chip_text = palette.text_light if dark else palette.text_dark
    for chip in chips:
        normalized = _short_label(chip, max_chars=12)
        if not normalized:
            continue
        chip_width = _estimate_text_width(normalized, scale=2) + 24
        if cursor_x + chip_width > x + max_width:
            break
        _draw_panel(pixel_data, width, height, cursor_x, y, cursor_x + chip_width, y + 32, radius=16, fill=chip_fill, border=chip_border, shadow=False)
        _draw_text(pixel_data, width, height, cursor_x + 12, y + 10, normalized, scale=2, color=chip_text)
        cursor_x += chip_width + 10


def _draw_toolbar_dots(
    pixel_data: bytearray,
    width: int,
    x: int,
    y: int,
    palette: Palette,
) -> None:
    _draw_circle(pixel_data, width, TARGET_HEIGHT, x, y, 6, palette.danger)
    _draw_circle(pixel_data, width, TARGET_HEIGHT, x + 18, y, 6, palette.warning)
    _draw_circle(pixel_data, width, TARGET_HEIGHT, x + 36, y, 6, palette.success)


def _draw_status_dot(
    pixel_data: bytearray,
    width: int,
    x: int,
    y: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    _draw_circle(pixel_data, width, TARGET_HEIGHT, x, y, radius, color)


def _draw_text_lines(
    pixel_data: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    lines: list[str],
    *,
    scale: int,
    line_gap: int,
    color: tuple[int, int, int],
) -> None:
    cursor_y = y
    for line in lines:
        _draw_text(pixel_data, width, height, x, cursor_y, line, scale=scale, color=color)
        cursor_y += line_gap


def _draw_panel(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    radius: int,
    fill: tuple[int, int, int],
    border: tuple[int, int, int],
    shadow: bool,
) -> None:
    if shadow:
        _draw_rounded_rect_alpha(
            pixel_data,
            width,
            height,
            x0 + 10,
            y0 + 14,
            x1 + 10,
            y1 + 14,
            radius=max(0, radius + 2),
            color=(4, 8, 16),
            alpha=0.18,
        )
    _draw_rounded_rect(pixel_data, width, height, x0, y0, x1, y1, radius, border)
    if x1 - x0 > 4 and y1 - y0 > 4:
        _draw_rounded_rect(
            pixel_data,
            width,
            height,
            x0 + 2,
            y0 + 2,
            x1 - 2,
            y1 - 2,
            max(0, radius - 2),
            fill,
        )


def _draw_rounded_rect(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    clamped_radius = max(0, radius)
    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            inside = True
            if x < x0 + clamped_radius and y < y0 + clamped_radius:
                inside = _in_circle(x, y, x0 + clamped_radius, y0 + clamped_radius, clamped_radius)
            elif x >= x1 - clamped_radius and y < y0 + clamped_radius:
                inside = _in_circle(x, y, x1 - clamped_radius - 1, y0 + clamped_radius, clamped_radius)
            elif x < x0 + clamped_radius and y >= y1 - clamped_radius:
                inside = _in_circle(x, y, x0 + clamped_radius, y1 - clamped_radius - 1, clamped_radius)
            elif x >= x1 - clamped_radius and y >= y1 - clamped_radius:
                inside = _in_circle(x, y, x1 - clamped_radius - 1, y1 - clamped_radius - 1, clamped_radius)
            if inside:
                _set_pixel(pixel_data, width, x, y, color)


def _draw_rounded_rect_alpha(
    pixel_data: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    radius: int,
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    clamped_radius = max(0, radius)
    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            inside = True
            if x < x0 + clamped_radius and y < y0 + clamped_radius:
                inside = _in_circle(x, y, x0 + clamped_radius, y0 + clamped_radius, clamped_radius)
            elif x >= x1 - clamped_radius and y < y0 + clamped_radius:
                inside = _in_circle(x, y, x1 - clamped_radius - 1, y0 + clamped_radius, clamped_radius)
            elif x < x0 + clamped_radius and y >= y1 - clamped_radius:
                inside = _in_circle(x, y, x0 + clamped_radius, y1 - clamped_radius - 1, clamped_radius)
            elif x >= x1 - clamped_radius and y >= y1 - clamped_radius:
                inside = _in_circle(x, y, x1 - clamped_radius - 1, y1 - clamped_radius - 1, clamped_radius)
            if inside:
                _blend_pixel(pixel_data, width, x, y, color, alpha)


def _draw_circle(
    pixel_data: bytearray,
    width: int,
    height: int,
    center_x: int,
    center_y: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    for y in range(max(0, center_y - radius), min(height, center_y + radius + 1)):
        for x in range(max(0, center_x - radius), min(width, center_x + radius + 1)):
            if _in_circle(x, y, center_x, center_y, radius):
                _set_pixel(pixel_data, width, x, y, color)


def _in_circle(x: int, y: int, center_x: int, center_y: int, radius: int) -> bool:
    dx = x - center_x
    dy = y - center_y
    return dx * dx + dy * dy <= radius * radius


def _draw_text(
    pixel_data: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    *,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    cursor_x = x
    for character in _normalize_draw_text(text):
        glyph = BITMAP_FONT.get(character, BITMAP_FONT[" "])
        for row_index, row in enumerate(glyph):
            for column_index, cell in enumerate(row):
                if cell != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        draw_x = cursor_x + column_index * scale + dx
                        draw_y = y + row_index * scale + dy
                        if 0 <= draw_x < width and 0 <= draw_y < height:
                            _set_pixel(pixel_data, width, draw_x, draw_y, color)
        cursor_x += (len(glyph[0]) + 1) * scale


def _draw_line(
    pixel_data: bytearray,
    width: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    color: tuple[int, int, int],
    thickness: int,
    alpha: float = 1.0,
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    step_x = 1 if x0 < x1 else -1
    step_y = 1 if y0 < y1 else -1
    error = dx + dy
    radius = max(0, thickness // 2)

    while True:
        for offset_y in range(-radius, radius + 1):
            for offset_x in range(-radius, radius + 1):
                if offset_x * offset_x + offset_y * offset_y > radius * radius:
                    continue
                draw_x = x0 + offset_x
                draw_y = y0 + offset_y
                if 0 <= draw_x < width and 0 <= draw_y < TARGET_HEIGHT:
                    if alpha >= 1.0:
                        _set_pixel(pixel_data, width, draw_x, draw_y, color)
                    else:
                        _blend_pixel(pixel_data, width, draw_x, draw_y, color, alpha)
        if x0 == x1 and y0 == y1:
            break
        doubled_error = 2 * error
        if doubled_error >= dy:
            error += dy
            x0 += step_x
        if doubled_error <= dx:
            error += dx
            y0 += step_y


def _estimate_text_width(text: str, *, scale: int) -> int:
    normalized = _normalize_draw_text(text)
    return len(normalized) * (len(BITMAP_FONT["A"][0]) + 1) * scale


def _normalize_draw_text(text: str) -> str:
    normalized_text = text.upper().replace("_", " ")
    normalized_text = re.sub(r"[^A-Z0-9/\- ]+", " ", normalized_text)
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
    cleaned_characters = [character if character in DRAWABLE_CHARACTERS else " " for character in normalized_text]
    return "".join(cleaned_characters).strip()


def _wrap_draw_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    normalized_text = _normalize_draw_text(text)
    if not normalized_text:
        return []

    words = normalized_text.split()
    lines: list[str] = []
    current_line: list[str] = []
    for word in words:
        tentative = " ".join(current_line + [word])
        if current_line and len(tentative) > max_chars and len(lines) < max_lines - 1:
            lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    if current_line and len(lines) < max_lines:
        lines.append(" ".join(current_line))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and len(words) > 0:
        lines[-1] = _short_label(lines[-1], max_chars=max_chars)
    return lines


def _set_pixel(
    pixel_data: bytearray,
    width: int,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    offset = (y * width + x) * 3
    pixel_data[offset] = color[0]
    pixel_data[offset + 1] = color[1]
    pixel_data[offset + 2] = color[2]


def _blend_pixel(
    pixel_data: bytearray,
    width: int,
    x: int,
    y: int,
    color: tuple[int, int, int],
    alpha: float,
) -> None:
    bounded_alpha = max(0.0, min(1.0, alpha))
    inverse_alpha = 1.0 - bounded_alpha
    offset = (y * width + x) * 3
    pixel_data[offset] = int(round(pixel_data[offset] * inverse_alpha + color[0] * bounded_alpha))
    pixel_data[offset + 1] = int(round(pixel_data[offset + 1] * inverse_alpha + color[1] * bounded_alpha))
    pixel_data[offset + 2] = int(round(pixel_data[offset + 2] * inverse_alpha + color[2] * bounded_alpha))


def _ellipse_strength(
    *,
    x: int,
    y: int,
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
) -> float:
    dx = (x - center_x) / max(radius_x, 1.0)
    dy = (y - center_y) / max(radius_y, 1.0)
    distance = dx * dx + dy * dy
    if distance >= 1.0:
        return 0.0
    return 1.0 - distance


def _stripe_strength(*, x: int, y: int, width: int, height: int) -> float:
    normalized = (x / max(1, width - 1)) * 0.6 + (y / max(1, height - 1)) * 1.1
    distance = abs((normalized % 0.18) - 0.09)
    if distance >= 0.016:
        return 0.0
    return (0.016 - distance) / 0.016


def _lerp_color(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    factor: float,
) -> tuple[int, int, int]:
    bounded_factor = max(0.0, min(1.0, factor))
    return (
        int(round(start[0] + (end[0] - start[0]) * bounded_factor)),
        int(round(start[1] + (end[1] - start[1]) * bounded_factor)),
        int(round(start[2] + (end[2] - start[2]) * bounded_factor)),
    )


def _mix_color(
    base: tuple[int, int, int],
    overlay: tuple[int, int, int],
    alpha: float,
) -> tuple[int, int, int]:
    bounded_alpha = max(0.0, min(1.0, alpha))
    inverse_alpha = 1.0 - bounded_alpha
    return (
        int(round(base[0] * inverse_alpha + overlay[0] * bounded_alpha)),
        int(round(base[1] * inverse_alpha + overlay[1] * bounded_alpha)),
        int(round(base[2] * inverse_alpha + overlay[2] * bounded_alpha)),
    )


def _state_color(palette: Palette, state: str) -> tuple[int, int, int]:
    lowered_state = state.lower()
    if lowered_state in {"done", "safe", "ok", "ready", "live"}:
        return palette.success
    if lowered_state in {"check", "wait"}:
        return palette.warning
    if lowered_state in {"fail", "error"}:
        return palette.danger
    return palette.accent


__all__ = ["VisualGenerationError", "generate_visual_step", "select_background_asset"]
