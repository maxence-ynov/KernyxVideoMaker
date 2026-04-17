from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from job_paths import EDIT_MANIFEST_FILENAME, resolve_job_path
from pipeline.scene_planner import (
    DEFAULT_OVERLAY_TARGET_RATIO,
    SceneManifest,
    SceneSegment,
    compute_scene_subtitle_text,
    load_scene_manifest,
)
from pipeline.visual_manifest import VisualManifest, load_visual_manifest
from schemas import JSONValue, VideoJob

EDIT_MANIFEST_VERSION = "scene_edit_v1"

CAMERA_PATTERNS = frozenset(
    {
        "push_in_soft",
        "push_in_fast",
        "pull_out_soft",
        "pan_left",
        "pan_right",
        "hold_static",
        "impact_zoom",
        "micro_shake_light",
    }
)
CAMERA_INTENSITIES = frozenset({"low", "medium", "high"})
TRANSITIONS = frozenset({"cut", "flash_cut", "blur_in", "blur_out", "dip_to_black_soft"})
TEXT_STYLES = frozenset(
    {
        "standard_subtitles",
        "keyword_emphasis",
        "hook_focus",
        "proof_clean",
        "explain_readable",
        "cta_clean",
    }
)
OVERLAY_STYLES = frozenset({"none", "subtle_vignette", "light_glow", "proof_frame", "cta_band"})
SFX_PROFILES = frozenset(
    {
        "none",
        "hit_soft",
        "hit_strong",
        "whoosh_soft",
        "whoosh_fast",
        "pop_light",
        "pop_short",
        "click_ui_soft",
        "click_ui_hard",
        "riser_short",
        "glitch_light",
    }
)
BGM_PROFILES = frozenset({"none", "tension_light", "business_clean", "energy_boost"})
PACE_ROLES = frozenset({"hook", "proof", "explain", "cta"})


class EditPlanningError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SceneEditPlan:
    scene_id: str
    scene_index: int
    role: str
    scene_type: str
    pace_role: str
    camera_pattern: str
    camera_intensity: str
    transition_in: str
    transition_out: str
    text_style: str
    overlay_style: str
    sfx_profile: str
    bgm_profile: str

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "role": self.role,
            "scene_type": self.scene_type,
            "pace_role": self.pace_role,
            "camera_pattern": self.camera_pattern,
            "camera_intensity": self.camera_intensity,
            "transition_in": self.transition_in,
            "transition_out": self.transition_out,
            "text_style": self.text_style,
            "overlay_style": self.overlay_style,
            "sfx_profile": self.sfx_profile,
            "bgm_profile": self.bgm_profile,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SceneEditPlan:
        scene_id = _read_required_text(payload, "scene_id")
        role = _read_required_text(payload, "role")
        scene_type = _read_required_text(payload, "scene_type")
        return cls(
            scene_id=scene_id,
            scene_index=max(0, int(payload.get("scene_index", payload.get("index", 0)))),
            role=role,
            scene_type=scene_type,
            pace_role=_normalize_choice(payload.get("pace_role"), valid=PACE_ROLES, default="explain"),
            camera_pattern=_normalize_choice(
                payload.get("camera_pattern"),
                valid=CAMERA_PATTERNS,
                default="hold_static",
            ),
            camera_intensity=_normalize_choice(
                payload.get("camera_intensity"),
                valid=CAMERA_INTENSITIES,
                default="medium",
            ),
            transition_in=_normalize_choice(payload.get("transition_in"), valid=TRANSITIONS, default="cut"),
            transition_out=_normalize_choice(payload.get("transition_out"), valid=TRANSITIONS, default="cut"),
            text_style=_normalize_choice(
                payload.get("text_style"),
                valid=TEXT_STYLES,
                default="standard_subtitles",
            ),
            overlay_style=_normalize_choice(
                payload.get("overlay_style"),
                valid=OVERLAY_STYLES,
                default="none",
            ),
            sfx_profile=_normalize_choice(payload.get("sfx_profile"), valid=SFX_PROFILES, default="none"),
            bgm_profile=_normalize_choice(payload.get("bgm_profile"), valid=BGM_PROFILES, default="none"),
        )


@dataclass(slots=True, frozen=True)
class EditManifest:
    version: str
    scenes: list[SceneEditPlan]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "version": self.version,
            "scene_count": len(self.scenes),
            "scenes": [scene.to_metadata() for scene in self.scenes],
        }

    def get_scene_plan(self, scene_id: str) -> SceneEditPlan | None:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> EditManifest:
        raw_scenes = payload.get("scenes", [])
        if not isinstance(raw_scenes, list):
            raise EditPlanningError("edit manifest must contain a 'scenes' array")
        scenes = [
            SceneEditPlan.from_mapping(item)
            for item in raw_scenes
            if isinstance(item, Mapping)
        ]
        if not scenes:
            raise EditPlanningError("edit manifest is empty")
        return cls(
            version=_read_optional_text(payload.get("version")) or EDIT_MANIFEST_VERSION,
            scenes=scenes,
        )


async def plan_edit_step(job: VideoJob, job_dir: Path) -> None:
    scene_manifest = load_scene_manifest(job)
    if scene_manifest is None:
        job.set_metadata("edit_planner_skipped", True)
        job.set_metadata("edit_planner_skip_reason", "scene_manifest_missing")
        return

    visual_manifest = load_visual_manifest(job)
    manifest = build_edit_manifest(
        job,
        scene_manifest=scene_manifest,
        visual_manifest=visual_manifest,
    )
    viewer_simulation = _simulate_mobile_viewer(
        scene_manifest=scene_manifest,
        edit_manifest=manifest,
        visual_manifest=visual_manifest,
    )
    job.set_metadata("viewer_simulation", viewer_simulation)
    job.set_metadata("viewer_simulation_passed", bool(viewer_simulation.get("passed")))
    save_edit_manifest(job, job_dir, manifest)


def build_edit_manifest(
    job: VideoJob,
    *,
    scene_manifest: SceneManifest | None = None,
    visual_manifest: VisualManifest | None = None,
) -> EditManifest:
    manifest = scene_manifest or load_scene_manifest(job)
    if manifest is None:
        raise EditPlanningError("edit planner requires a scene manifest")

    visuals = visual_manifest or load_visual_manifest(job)
    scenes: list[SceneEditPlan] = []
    for index, scene in enumerate(manifest.scenes):
        previous_scene = manifest.scenes[index - 1] if index > 0 else None
        next_scene = manifest.scenes[index + 1] if index + 1 < len(manifest.scenes) else None
        scene_visual = visuals.get_scene_asset(scene.scene_id) if visuals is not None else None
        pace_role = _resolve_pace_role(scene)
        camera_pattern, camera_intensity = _resolve_camera(scene, pace_role=pace_role, index=index)
        transition_in, transition_out = _resolve_transitions(
            scene,
            pace_role=pace_role,
            previous_scene=previous_scene,
            next_scene=next_scene,
        )
        scenes.append(
            SceneEditPlan(
                scene_id=scene.scene_id,
                scene_index=scene.index,
                role=scene.role,
                scene_type=scene.scene_type,
                pace_role=pace_role,
                camera_pattern=camera_pattern,
                camera_intensity=camera_intensity,
                transition_in=transition_in,
                transition_out=transition_out,
                text_style=_resolve_text_style(scene, pace_role=pace_role),
                overlay_style=_resolve_overlay_style(scene, pace_role=pace_role, visual_asset_mode=scene_visual.visual_asset_mode if scene_visual else None),
                sfx_profile=_resolve_sfx_profile(scene, pace_role=pace_role, index=index),
                bgm_profile=_resolve_bgm_profile(scene, pace_role=pace_role),
            )
        )

    return EditManifest(version=EDIT_MANIFEST_VERSION, scenes=scenes)


def _simulate_mobile_viewer(
    *,
    scene_manifest: SceneManifest,
    edit_manifest: EditManifest,
    visual_manifest: VisualManifest | None,
) -> dict[str, JSONValue]:
    scene_reports: list[dict[str, JSONValue]] = []
    overlay_scene_count = 0

    for scene in scene_manifest.scenes:
        scene_visual = visual_manifest.get_scene_asset(scene.scene_id) if visual_manifest is not None else None
        subtitle_text = compute_scene_subtitle_text(scene)
        overlay_text = scene.overlay_text or ""
        overlay_enabled = bool(overlay_text)
        if overlay_enabled:
            overlay_scene_count += 1

        has_visual_proof = bool(
            scene_visual is not None
            and (scene_visual.visual_asset_mode or scene.visual_asset_mode) in {"dashboard", "workflow", "ui", "screenshot"}
            and scene_visual.selected_by in {"explicit", "catalog_match", "contextual_image", "contextual_video"}
        )
        strong_elements = 1
        if subtitle_text:
            strong_elements += 1
        if overlay_enabled:
            strong_elements += 1

        comprehension_in_1_second = bool(
            (overlay_enabled and len(overlay_text.split()) <= 4)
            or has_visual_proof
            or len(subtitle_text.split()) <= 3
        )
        readable_without_sound = bool(subtitle_text or overlay_text)
        focus_clear = strong_elements <= 3 and not (
            overlay_enabled and has_visual_proof and (scene.primary_focus or "") == "visual_proof"
        )

        scene_reports.append(
            {
                "scene_id": scene.scene_id,
                "scene_type": scene.scene_type,
                "visual_intensity_level": scene.visual_intensity_level or "unknown",
                "primary_focus": scene.primary_focus or "unknown",
                "overlay_text": overlay_text or None,
                "overlay_role": scene.overlay_role,
                "subtitle_text": subtitle_text or None,
                "strong_element_count": strong_elements,
                "comprehension_in_1_second": comprehension_in_1_second,
                "readable_without_sound": readable_without_sound,
                "focus_clear": focus_clear,
            }
        )

    scene_count = len(scene_manifest.scenes)
    overlay_ratio = round(overlay_scene_count / scene_count, 3) if scene_count else 0.0
    max_overlay_ratio = round(max(DEFAULT_OVERLAY_TARGET_RATIO if scene_count >= 5 else 0.5, 2 / max(1, scene_count)), 3)
    passed = bool(
        scene_reports
        and all(
            bool(report["readable_without_sound"])
            and bool(report["focus_clear"])
            for report in scene_reports
        )
        and overlay_ratio <= max_overlay_ratio
    )

    return {
        "mode": "mobile_1x",
        "scene_count": scene_count,
        "overlay_scene_count": overlay_scene_count,
        "overlay_ratio": overlay_ratio,
        "target_overlay_ratio": DEFAULT_OVERLAY_TARGET_RATIO,
        "overlay_ratio_target_met": overlay_ratio <= max_overlay_ratio,
        "passed": passed,
        "scenes": scene_reports,
    }


def load_edit_manifest(job: VideoJob) -> EditManifest | None:
    raw_manifest = job.metadata.get("edit_manifest")
    if isinstance(raw_manifest, Mapping):
        return EditManifest.from_mapping(raw_manifest)

    raw_path = job.metadata.get("edit_manifest_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    manifest_path = resolve_job_path(job, raw_path)
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise EditPlanningError(f"edit manifest is not a JSON object: {manifest_path}")
    return EditManifest.from_mapping(payload)


def save_edit_manifest(job: VideoJob, job_dir: Path, manifest: EditManifest) -> Path:
    manifest_path = (job_dir / EDIT_MANIFEST_FILENAME).resolve()
    manifest_path.write_text(
        json.dumps(manifest.to_metadata(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    job.set_metadata("edit_manifest_version", manifest.version)
    job.set_metadata("edit_manifest_scene_count", len(manifest.scenes))
    job.set_metadata("edit_manifest", manifest.to_metadata())
    job.set_metadata("edit_manifest_path", str(manifest_path))
    return manifest_path


def _resolve_pace_role(scene: SceneSegment) -> str:
    if scene.role == "hook" or scene.scene_type == "hook":
        return "hook"
    if scene.role == "cta" or scene.scene_type == "cta":
        return "cta"
    if scene.scene_type == "proof":
        return "proof"
    return "explain"


def _resolve_camera(scene: SceneSegment, *, pace_role: str, index: int) -> tuple[str, str]:
    duration_seconds = max(0.3, float(scene.duration_seconds))
    if pace_role == "hook":
        if index == 0 and duration_seconds <= 2.6:
            return "impact_zoom", "high"
        if duration_seconds <= 3.6:
            return "push_in_fast", "high"
        return ("micro_shake_light", "medium") if index % 2 else ("push_in_fast", "medium")

    if pace_role == "proof":
        if duration_seconds >= 4.0:
            return "hold_static", "low"
        if index % 2 == 0:
            return "push_in_soft", "low"
        return "pan_right", "low" if duration_seconds >= 3.0 else "medium"

    if pace_role == "cta":
        if duration_seconds >= 2.6:
            return "pull_out_soft", "low"
        return "hold_static", "low"

    pattern_cycle = ("pan_left", "pan_right", "push_in_soft")
    pattern = pattern_cycle[index % len(pattern_cycle)]
    intensity = "medium" if duration_seconds <= 3.2 else "low"
    return pattern, intensity


def _resolve_transitions(
    scene: SceneSegment,
    *,
    pace_role: str,
    previous_scene: SceneSegment | None,
    next_scene: SceneSegment | None,
) -> tuple[str, str]:
    if pace_role == "hook":
        transition_in = "cut" if previous_scene is None else "flash_cut"
        transition_out = "flash_cut" if next_scene is not None else "cut"
        return transition_in, transition_out

    if pace_role == "proof":
        transition_in = "blur_in" if previous_scene is not None and _resolve_pace_role(previous_scene) == "hook" else "cut"
        transition_out = "dip_to_black_soft" if next_scene is not None and _resolve_pace_role(next_scene) == "cta" else "cut"
        return transition_in, transition_out

    if pace_role == "cta":
        return ("dip_to_black_soft" if previous_scene is not None else "cut"), "cut"

    transition_in = "blur_in" if previous_scene is not None and _resolve_pace_role(previous_scene) == "hook" else "cut"
    transition_out = "blur_out" if next_scene is not None and _resolve_pace_role(next_scene) != "cta" else "cut"
    return transition_in, transition_out


def _resolve_text_style(scene: SceneSegment, *, pace_role: str) -> str:
    if pace_role == "hook":
        return "hook_focus"
    if pace_role == "proof":
        return "proof_clean"
    if pace_role == "cta":
        return "cta_clean"
    return "explain_readable"


def _resolve_overlay_style(
    scene: SceneSegment,
    *,
    pace_role: str,
    visual_asset_mode: str | None,
) -> str:
    asset_mode = (visual_asset_mode or scene.visual_asset_mode).strip().lower()
    if pace_role == "hook":
        return "light_glow"
    if pace_role == "proof":
        return "proof_frame" if asset_mode in {"dashboard", "screenshot", "ui"} else "none"
    if pace_role == "cta":
        return "cta_band"
    return "subtle_vignette" if asset_mode in {"workflow", "screenshot"} else "none"


def _resolve_sfx_profile(scene: SceneSegment, *, pace_role: str, index: int) -> str:
    has_numbers = any(character.isdigit() for character in scene.source_text)
    visual_mode = scene.visual_asset_mode.strip().lower()
    cue_count = len(scene.cue_timeline)

    if pace_role == "hook":
        if index == 0:
            return "hit_soft"
        return "whoosh_fast" if float(scene.duration_seconds) >= 2.0 else "hit_soft"

    if pace_role == "proof":
        if visual_mode in {"dashboard", "ui", "screenshot"}:
            return "click_ui_soft"
        if has_numbers or scene.proof_mode != "generic":
            return "pop_light"
        return "none"

    if pace_role == "cta":
        return "pop_short" if float(scene.duration_seconds) >= 1.6 else "none"

    if float(scene.duration_seconds) >= 3.2 and (cue_count >= 2 or scene.scene_type in {"workflow", "interface"}):
        return "whoosh_soft"
    return "none"


def _resolve_bgm_profile(scene: SceneSegment, *, pace_role: str) -> str:
    if pace_role == "hook":
        return "tension_light"
    if pace_role == "cta":
        return "business_clean"
    if pace_role in {"proof", "explain"}:
        return "business_clean" if scene.scene_type != "hook" else "tension_light"
    return "none"


def _normalize_choice(value: object, *, valid: frozenset[str], default: str) -> str:
    normalized = _read_optional_text(value)
    if normalized is None:
        return default
    lowered = normalized.lower()
    if lowered in valid:
        return lowered
    return default


def _read_required_text(payload: Mapping[str, Any], key: str) -> str:
    value = _read_optional_text(payload.get(key))
    if value is None:
        raise EditPlanningError(f"edit manifest scene entry is missing '{key}'")
    return value


def _read_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        normalized = " ".join(value.split()).strip()
        return normalized or None
    return None


__all__ = [
    "BGM_PROFILES",
    "CAMERA_INTENSITIES",
    "CAMERA_PATTERNS",
    "EDIT_MANIFEST_FILENAME",
    "EDIT_MANIFEST_VERSION",
    "OVERLAY_STYLES",
    "PACE_ROLES",
    "SFX_PROFILES",
    "TEXT_STYLES",
    "TRANSITIONS",
    "EditManifest",
    "EditPlanningError",
    "SceneEditPlan",
    "build_edit_manifest",
    "load_edit_manifest",
    "plan_edit_step",
    "save_edit_manifest",
]
