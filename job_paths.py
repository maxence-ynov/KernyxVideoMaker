from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import TYPE_CHECKING
import unicodedata

if TYPE_CHECKING:
    from schemas import VideoJob

JOB_STORAGE_NAME_METADATA_KEY = "job_storage_name"
JOB_FILE_PATH_METADATA_KEY = "job_file_path"
JOB_JSON_FILENAME = "job.json"
SCRIPT_FILENAME = "01_script.txt"
VOICE_AUDIO_FILENAME = "02_voice.mp3"
VOICE_AUDIO_RENDERING_FILENAME = "02_voice_rendering.mp3"
VOICE_BOUNDARIES_FILENAME = "02_voice_boundaries.jsonl"
VOICE_INPUT_FILENAME = "02_voice_input.txt"
SUBTITLES_FILENAME = "03_subtitles.srt"
SCENE_MANIFEST_FILENAME = "04_scene_manifest.json"
VISUAL_MANIFEST_FILENAME = "05_visual_manifest.json"
EDIT_MANIFEST_FILENAME = "06_edit_manifest.json"
SOURCE_MEDIA_ARTIFACT_BASENAME = "05_source_media"
SCENE_ASSETS_DIRNAME = "scene_assets"
SCENE_CLIPS_DIRNAME = "scene_clips"
SCENE_CLIP_LIST_FILENAME = "06_scene_clips.txt"
SCENES_CONCAT_FILENAME = "06_scenes_concat.mp4"
AUDIO_MIX_FILENAME = "07_audio_mix.m4a"
FINAL_VIDEO_FILENAME = "final.mp4"
FINAL_VIDEO_RENDERING_FILENAME = "final_rendering.mp4"
DEFAULT_TOPIC_SLUG = "video"
DEFAULT_TOPIC_SLUG_MAX_LENGTH = 48
DEFAULT_SHORT_ID_LENGTH = 8

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SHORT_ID_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify_topic(
    topic: str,
    *,
    max_length: int = DEFAULT_TOPIC_SLUG_MAX_LENGTH,
    fallback: str = DEFAULT_TOPIC_SLUG,
) -> str:
    normalized = unicodedata.normalize("NFKD", topic or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM_RE.sub("-", ascii_value.lower()).strip("-")
    if not slug:
        return fallback
    if max_length <= 0:
        return fallback
    if len(slug) <= max_length:
        return slug

    truncated = slug[:max_length].strip("-")
    last_separator = truncated.rfind("-")
    if last_separator >= max(8, max_length // 2):
        truncated = truncated[:last_separator]
    return truncated.strip("-") or fallback


def build_short_job_id(job_id: str, *, length: int = DEFAULT_SHORT_ID_LENGTH) -> str:
    normalized = _SHORT_ID_RE.sub("", job_id.strip().lower())
    if not normalized:
        raise ValueError("job_id must contain at least one alphanumeric character")
    if length <= 0:
        return normalized
    return normalized[:length]


def build_job_storage_name(
    topic: str,
    job_id: str,
    created_at: datetime,
    *,
    slug_max_length: int = DEFAULT_TOPIC_SLUG_MAX_LENGTH,
) -> str:
    timestamp = _normalize_datetime(created_at).astimezone().strftime("%Y%m%d_%H%M")
    slug = slugify_topic(topic, max_length=slug_max_length)
    short_id = build_short_job_id(job_id)
    return f"{timestamp}_{slug}_{short_id}"


def ensure_job_storage_name(job: "VideoJob") -> str:
    raw_value = job.metadata.get(JOB_STORAGE_NAME_METADATA_KEY)
    if isinstance(raw_value, str):
        normalized_value = raw_value.strip()
        if normalized_value and "/" not in normalized_value and "\\" not in normalized_value:
            return normalized_value

    storage_name = build_job_storage_name(job.topic, job.job_id, job.created_at)
    job.set_metadata(JOB_STORAGE_NAME_METADATA_KEY, storage_name)
    return storage_name


def get_job_storage_dir(base_dir: str | Path, job: "VideoJob") -> Path:
    return (Path(base_dir).expanduser().resolve() / ensure_job_storage_name(job)).resolve()


def get_job_json_path(base_dir: str | Path, job: "VideoJob") -> Path:
    return (get_job_storage_dir(base_dir, job) / JOB_JSON_FILENAME).resolve()


def get_job_artifacts_dir(base_dir: str | Path, job: "VideoJob") -> Path:
    return get_job_storage_dir(base_dir, job)


def build_source_media_artifact_filename(suffix: str) -> str:
    normalized_suffix = suffix.strip().lower()
    if not normalized_suffix.startswith("."):
        normalized_suffix = f".{normalized_suffix}"
    return f"{SOURCE_MEDIA_ARTIFACT_BASENAME}{normalized_suffix}"


def build_scene_asset_filename(scene_index: int, suffix: str) -> str:
    if scene_index < 0:
        raise ValueError("scene_index must be >= 0")
    normalized_suffix = suffix.strip().lower()
    if not normalized_suffix.startswith("."):
        normalized_suffix = f".{normalized_suffix}"
    return f"scene_{scene_index + 1:02d}{normalized_suffix}"


def build_scene_clip_filename(scene_index: int) -> str:
    if scene_index < 0:
        raise ValueError("scene_index must be >= 0")
    return f"scene_{scene_index + 1:02d}.mp4"


def resolve_job_path(job: "VideoJob", raw_value: str) -> Path:
    candidate_path = Path(raw_value).expanduser()
    if candidate_path.is_absolute():
        return candidate_path.resolve()

    artifact_dir = _read_metadata_path(job, "artifact_dir")
    if artifact_dir is not None:
        artifact_candidate = (artifact_dir / candidate_path).resolve()
        if artifact_candidate.exists():
            return artifact_candidate

    resolved_from_cwd = candidate_path.resolve()
    if resolved_from_cwd.exists():
        return resolved_from_cwd

    config_path = _read_metadata_path(job, "config_path")
    if config_path is not None:
        config_candidate = (config_path.parent / candidate_path).resolve()
        if config_candidate.exists():
            return config_candidate

    if artifact_dir is not None:
        return (artifact_dir / candidate_path).resolve()
    if config_path is not None:
        return (config_path.parent / candidate_path).resolve()
    return resolved_from_cwd


def _read_metadata_path(job: "VideoJob", key: str) -> Path | None:
    raw_value = job.metadata.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    return Path(raw_value).expanduser().resolve()


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "AUDIO_MIX_FILENAME",
    "EDIT_MANIFEST_FILENAME",
    "FINAL_VIDEO_FILENAME",
    "FINAL_VIDEO_RENDERING_FILENAME",
    "JOB_FILE_PATH_METADATA_KEY",
    "JOB_JSON_FILENAME",
    "JOB_STORAGE_NAME_METADATA_KEY",
    "SCENE_ASSETS_DIRNAME",
    "SCENE_CLIPS_DIRNAME",
    "SCENE_CLIP_LIST_FILENAME",
    "SCENE_MANIFEST_FILENAME",
    "SCENES_CONCAT_FILENAME",
    "SCRIPT_FILENAME",
    "SOURCE_MEDIA_ARTIFACT_BASENAME",
    "SUBTITLES_FILENAME",
    "VISUAL_MANIFEST_FILENAME",
    "VOICE_AUDIO_FILENAME",
    "VOICE_AUDIO_RENDERING_FILENAME",
    "VOICE_BOUNDARIES_FILENAME",
    "VOICE_INPUT_FILENAME",
    "build_job_storage_name",
    "build_scene_asset_filename",
    "build_scene_clip_filename",
    "build_short_job_id",
    "build_source_media_artifact_filename",
    "ensure_job_storage_name",
    "get_job_artifacts_dir",
    "get_job_json_path",
    "get_job_storage_dir",
    "resolve_job_path",
    "slugify_topic",
]
