from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from job_paths import VISUAL_MANIFEST_FILENAME, resolve_job_path
from schemas import JSONValue, VideoJob

VISUAL_MANIFEST_VERSION = "scene_visuals_v1"


class VisualManifestError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SceneVisualAsset:
    scene_id: str
    scene_index: int
    asset_path: str
    asset_type: str
    source_kind: str
    selected_by: str | None = None
    asset_origin_path: str | None = None
    matched_tags: tuple[str, ...] = ()
    matched_entities: tuple[str, ...] = ()
    matched_visual_categories: tuple[str, ...] = ()
    selection_score: float | None = None
    reuse_rank: int | None = None
    visual_asset_mode: str | None = None
    motion_effect: str | None = None
    transition_effect: str | None = None
    transition_duration_seconds: float | None = None

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "asset_path": self.asset_path,
            "asset_type": self.asset_type,
            "source_kind": self.source_kind,
            "selected_by": self.selected_by,
            "asset_origin_path": self.asset_origin_path,
            "matched_tags": list(self.matched_tags),
            "matched_entities": list(self.matched_entities),
            "matched_visual_categories": list(self.matched_visual_categories),
            "selection_score": (
                round(self.selection_score, 3)
                if self.selection_score is not None
                else None
            ),
            "reuse_rank": self.reuse_rank,
            "visual_asset_mode": self.visual_asset_mode,
            "motion_effect": self.motion_effect,
            "transition_effect": self.transition_effect,
            "transition_duration_seconds": (
                round(self.transition_duration_seconds, 3)
                if self.transition_duration_seconds is not None
                else None
            ),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> SceneVisualAsset:
        asset_path = str(payload.get("asset_path", "")).strip()
        if not asset_path:
            raise VisualManifestError("visual manifest scene entry is missing 'asset_path'")
        return cls(
            scene_id=str(payload.get("scene_id", "")).strip(),
            scene_index=max(0, int(payload.get("scene_index", payload.get("index", 0)))),
            asset_path=asset_path,
            asset_type=str(payload.get("asset_type", "video")).strip() or "video",
            source_kind=str(payload.get("source_kind", "unknown")).strip() or "unknown",
            selected_by=_read_optional_text(payload.get("selected_by")),
            asset_origin_path=_read_optional_text(payload.get("asset_origin_path")),
            matched_tags=tuple(_read_optional_string_list(payload.get("matched_tags"))),
            matched_entities=tuple(_read_optional_string_list(payload.get("matched_entities"))),
            matched_visual_categories=tuple(
                _read_optional_string_list(payload.get("matched_visual_categories"))
            ),
            selection_score=_read_optional_float(payload.get("selection_score")),
            reuse_rank=_read_optional_int(payload.get("reuse_rank")),
            visual_asset_mode=_read_optional_text(payload.get("visual_asset_mode")),
            motion_effect=_read_optional_text(payload.get("motion_effect")),
            transition_effect=_read_optional_text(payload.get("transition_effect")),
            transition_duration_seconds=_read_optional_float(payload.get("transition_duration_seconds")),
        )


@dataclass(slots=True, frozen=True)
class VisualManifest:
    version: str
    scenes: list[SceneVisualAsset]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "version": self.version,
            "scene_count": len(self.scenes),
            "scenes": [scene.to_metadata() for scene in self.scenes],
        }

    def get_scene_asset(self, scene_id: str) -> SceneVisualAsset | None:
        for scene in self.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> VisualManifest:
        raw_scenes = payload.get("scenes", [])
        if not isinstance(raw_scenes, list):
            raise VisualManifestError("visual manifest must contain a 'scenes' array")
        scenes = [
            SceneVisualAsset.from_mapping(item)
            for item in raw_scenes
            if isinstance(item, Mapping)
        ]
        if not scenes:
            raise VisualManifestError("visual manifest is empty")
        return cls(
            version=str(payload.get("version", VISUAL_MANIFEST_VERSION)).strip()
            or VISUAL_MANIFEST_VERSION,
            scenes=scenes,
        )


def load_visual_manifest(job: VideoJob) -> VisualManifest | None:
    raw_manifest = job.metadata.get("visual_manifest")
    if isinstance(raw_manifest, Mapping):
        return VisualManifest.from_mapping(raw_manifest)

    raw_path = job.metadata.get("visual_manifest_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    manifest_path = resolve_job_path(job, raw_path)
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise VisualManifestError(f"visual manifest is not a JSON object: {manifest_path}")
    return VisualManifest.from_mapping(payload)


def save_visual_manifest(job: VideoJob, job_dir: Path, manifest: VisualManifest) -> Path:
    manifest_path = (job_dir / VISUAL_MANIFEST_FILENAME).resolve()
    manifest_path.write_text(
        json.dumps(manifest.to_metadata(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    job.set_metadata("visual_manifest_version", manifest.version)
    job.set_metadata("visual_manifest_scene_count", len(manifest.scenes))
    job.set_metadata("visual_manifest", manifest.to_metadata())
    job.set_metadata("visual_manifest_path", str(manifest_path))
    return manifest_path


def _read_optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _read_optional_int(value: Any) -> int | None:
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


def _read_optional_string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value
        if isinstance(item, str) and str(item).strip()
    ]


__all__ = [
    "SceneVisualAsset",
    "VISUAL_MANIFEST_FILENAME",
    "VISUAL_MANIFEST_VERSION",
    "VisualManifest",
    "VisualManifestError",
    "load_visual_manifest",
    "save_visual_manifest",
]
