from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from schemas import JSONValue, VideoJob, VideoJobStatus
from storage import VideoJobStore

DEFAULT_WINDOW_SIZE = 5
DEFAULT_EXCLUSION_WINDOW = 2
DEFAULT_RECENCY_DECAY = 0.72
DEFAULT_LOCK_SCRIPT_PROFILE = True
DEFAULT_ALLOWED_VARIATION_FIELDS = frozenset(
    {
        "script_hook_family",
        "script_proof_mode",
        "script_cta_family",
        "script_body_structure",
        "visual_asset_mode",
        "visual_theme",
        "visual_layout_variant",
    }
)
DEFAULT_FIELD_WEIGHTS = {
    "script_profile": 0.8,
    "script_proof_mode": 1.0,
    "script_hook_family": 1.6,
    "script_hook_signature": 1.2,
    "script_hook_text": 0.4,
    "script_cta_family": 1.3,
    "script_cta_signature": 0.9,
    "script_cta_text": 0.4,
    "script_body_structure": 1.2,
    "script_signature": 1.8,
    "visual_asset_mode": 1.0,
    "visual_theme": 0.7,
    "visual_layout_signature": 1.5,
    "visual_signature": 1.8,
}
DEFAULT_HARD_EXCLUDE_FIELDS = frozenset(
    {
        "script_hook_family",
        "script_cta_family",
        "visual_layout_signature",
    }
)
DEFAULT_INCLUDED_STATUSES = frozenset(
    {
        VideoJobStatus.RUNNING.value,
        VideoJobStatus.COMPLETED.value,
        VideoJobStatus.UPLOADED.value,
    }
)


@dataclass(slots=True, frozen=True)
class DiversityConfig:
    enabled: bool
    window_size: int
    exclusion_window_size: int
    recency_decay: float
    lock_script_profile: bool
    allowed_variation_fields: frozenset[str]
    hard_exclude_fields: frozenset[str]
    field_weights: dict[str, float]
    included_statuses: frozenset[str]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "enabled": self.enabled,
            "window_size": self.window_size,
            "exclusion_window_size": self.exclusion_window_size,
            "recency_decay": round(self.recency_decay, 3),
            "lock_script_profile": self.lock_script_profile,
            "allowed_variation_fields": sorted(self.allowed_variation_fields),
            "hard_exclude_fields": sorted(self.hard_exclude_fields),
            "included_statuses": sorted(self.included_statuses),
            "field_weights": {
                field: round(weight, 3)
                for field, weight in sorted(self.field_weights.items())
            },
        }


@dataclass(slots=True, frozen=True)
class DiversityCandidate:
    key: str
    signals: dict[str, str]
    base_rank: int = 0


@dataclass(slots=True, frozen=True)
class CandidateScore:
    key: str
    score: float
    base_rank: int
    hard_excluded: bool
    signals: dict[str, str]
    matched_job_ids: dict[str, list[str]]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "key": self.key,
            "score": round(self.score, 3),
            "base_rank": self.base_rank,
            "hard_excluded": self.hard_excluded,
            "signals": dict(self.signals),
            "matched_job_ids": {
                field: list(job_ids)
                for field, job_ids in self.matched_job_ids.items()
            },
        }


@dataclass(slots=True, frozen=True)
class DiversityDecision:
    field_name: str
    selected_key: str
    enabled: bool
    window_job_ids: list[str]
    scores: list[CandidateScore]

    def to_metadata(self) -> dict[str, JSONValue]:
        return {
            "field_name": self.field_name,
            "selected_key": self.selected_key,
            "enabled": self.enabled,
            "window_job_ids": list(self.window_job_ids),
            "scores": [score.to_metadata() for score in self.scores],
        }


@dataclass(slots=True, frozen=True)
class DiversityMemory:
    config: DiversityConfig
    recent_jobs: list[VideoJob]

    @property
    def window_job_ids(self) -> list[str]:
        return [job.job_id for job in self.recent_jobs]

    def choose(
        self,
        field_name: str,
        candidates: Sequence[DiversityCandidate],
    ) -> DiversityDecision:
        if not candidates:
            raise ValueError(f"{field_name} candidates must not be empty")

        normalized_candidates = [
            DiversityCandidate(
                key=" ".join(candidate.key.split()).strip(),
                signals={
                    field: normalized_value
                    for field, raw_value in candidate.signals.items()
                    if (normalized_value := _normalize_signal(raw_value)) is not None
                },
                base_rank=max(0, candidate.base_rank),
            )
            for candidate in candidates
        ]
        normalized_candidates = [candidate for candidate in normalized_candidates if candidate.key]
        if not normalized_candidates:
            raise ValueError(f"{field_name} candidates resolved to an empty set")

        if not self.config.enabled or len(normalized_candidates) == 1:
            selected = min(normalized_candidates, key=lambda candidate: (candidate.base_rank, candidate.key))
            score = CandidateScore(
                key=selected.key,
                score=0.0,
                base_rank=selected.base_rank,
                hard_excluded=False,
                signals=dict(selected.signals),
                matched_job_ids={},
            )
            return DiversityDecision(
                field_name=field_name,
                selected_key=selected.key,
                enabled=self.config.enabled,
                window_job_ids=self.window_job_ids,
                scores=[score],
            )

        scores = [
            _score_candidate(candidate, self.recent_jobs, self.config)
            for candidate in normalized_candidates
        ]
        selectable_scores = [score for score in scores if not score.hard_excluded] or scores
        selected = min(
            selectable_scores,
            key=lambda score: (score.score, score.base_rank, score.key),
        )
        return DiversityDecision(
            field_name=field_name,
            selected_key=selected.key,
            enabled=self.config.enabled,
            window_job_ids=self.window_job_ids,
            scores=scores,
        )


def load_diversity_memory(job: VideoJob) -> DiversityMemory:
    config = _build_config(job)
    jobs_dir = _read_jobs_dir(job)
    if not config.enabled or jobs_dir is None:
        return DiversityMemory(config=config, recent_jobs=[])

    store = VideoJobStore(jobs_dir)
    recent_jobs = store.list_recent_jobs(
        limit=config.window_size,
        exclude_job_id=job.job_id,
        statuses=config.included_statuses,
    )
    return DiversityMemory(config=config, recent_jobs=recent_jobs)


def _build_config(job: VideoJob) -> DiversityConfig:
    raw_config = job.metadata.get("anti_repetition")
    config = raw_config if isinstance(raw_config, Mapping) else {}

    enabled = _read_bool(config.get("enabled"), default=True)
    window_size = _read_positive_int(config.get("window_size"), default=DEFAULT_WINDOW_SIZE)
    exclusion_window_size = _read_positive_int(
        config.get("exclusion_window_size"),
        default=DEFAULT_EXCLUSION_WINDOW,
    )
    recency_decay = _read_float(config.get("recency_decay"), default=DEFAULT_RECENCY_DECAY)
    if recency_decay <= 0.0:
        recency_decay = DEFAULT_RECENCY_DECAY
    lock_script_profile = _read_bool(
        config.get("lock_script_profile"),
        default=DEFAULT_LOCK_SCRIPT_PROFILE,
    )
    allowed_variation_fields = _read_string_set(
        config.get("allowed_variation_fields"),
        default=DEFAULT_ALLOWED_VARIATION_FIELDS,
    )
    hard_exclude_fields = _read_string_set(
        config.get("hard_exclude_fields"),
        default=DEFAULT_HARD_EXCLUDE_FIELDS,
    )
    field_weights = dict(DEFAULT_FIELD_WEIGHTS)
    custom_weights = config.get("field_weights")
    if isinstance(custom_weights, Mapping):
        for raw_field, raw_weight in custom_weights.items():
            field = str(raw_field).strip()
            if not field:
                continue
            weight = _read_float(raw_weight, default=field_weights.get(field, 1.0))
            field_weights[field] = max(0.0, weight)

    included_statuses = _read_status_set(
        config.get("included_statuses"),
        default=DEFAULT_INCLUDED_STATUSES,
    )
    return DiversityConfig(
        enabled=enabled,
        window_size=window_size,
        exclusion_window_size=exclusion_window_size,
        recency_decay=recency_decay,
        lock_script_profile=lock_script_profile,
        allowed_variation_fields=allowed_variation_fields,
        hard_exclude_fields=hard_exclude_fields,
        field_weights=field_weights,
        included_statuses=included_statuses,
    )


def _score_candidate(
    candidate: DiversityCandidate,
    recent_jobs: Sequence[VideoJob],
    config: DiversityConfig,
) -> CandidateScore:
    matched_job_ids: dict[str, list[str]] = {}
    total_score = 0.0
    hard_excluded = False

    for offset, recent_job in enumerate(recent_jobs):
        decay = config.recency_decay**offset
        for field, candidate_value in candidate.signals.items():
            recent_value = _read_signal_value(recent_job, field)
            if recent_value != candidate_value:
                continue

            matched_job_ids.setdefault(field, []).append(recent_job.job_id)
            total_score += config.field_weights.get(field, 1.0) * decay
            if field in config.hard_exclude_fields and offset < config.exclusion_window_size:
                hard_excluded = True

    return CandidateScore(
        key=candidate.key,
        score=total_score,
        base_rank=candidate.base_rank,
        hard_excluded=hard_excluded,
        signals=dict(candidate.signals),
        matched_job_ids=matched_job_ids,
    )


def _read_signal_value(job: VideoJob, field: str) -> str | None:
    direct_value = _normalize_signal(_read_text(job.metadata.get(field)))
    if direct_value is not None:
        return direct_value

    if field == "script_hook_text":
        return _normalize_signal(_read_section_text(job, "hook"))
    if field == "script_cta_text":
        return _normalize_signal(_read_section_text(job, "cta"))
    if field == "visual_layout_signature":
        layout = job.metadata.get("visual_layout")
        if isinstance(layout, Mapping):
            signature_parts: list[str] = []
            variant = _read_text(layout.get("variant"))
            title_count = _read_list_length(layout.get("title_lines"))
            chip_count = _read_list_length(layout.get("chips"))
            if variant:
                signature_parts.append(variant)
            if title_count is not None:
                signature_parts.append(f"title{title_count}")
            if chip_count is not None:
                signature_parts.append(f"chips{chip_count}")
            if signature_parts:
                return _normalize_signal("|".join(signature_parts))
    return None


def _read_jobs_dir(job: VideoJob) -> Path | None:
    raw_value = job.metadata.get("jobs_dir")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    return Path(raw_value).expanduser().resolve()


def _normalize_signal(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split()).strip().lower()
    return normalized or None


def _read_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_section_text(job: VideoJob, section_key: str) -> str | None:
    sections = job.metadata.get("script_sections")
    if not isinstance(sections, Mapping):
        return None
    return _read_text(sections.get(section_key))


def _read_list_length(value: object) -> int | None:
    if isinstance(value, list):
        return len(value)
    return None


def _read_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _read_positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, float):
        return max(1, int(round(value)))
    if isinstance(value, str) and value.strip():
        try:
            return max(1, int(value.strip()))
        except ValueError:
            return default
    return default


def _read_float(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _read_string_set(value: object, *, default: frozenset[str]) -> frozenset[str]:
    if not isinstance(value, list):
        return default
    normalized = {
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    }
    return frozenset(normalized) or default


def _read_status_set(value: object, *, default: frozenset[str]) -> frozenset[str]:
    if not isinstance(value, list):
        return default

    normalized_statuses: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            normalized_statuses.add(VideoJobStatus(item.strip().lower()).value)
        except ValueError:
            continue
    return frozenset(normalized_statuses) or default


__all__ = [
    "DiversityCandidate",
    "DiversityConfig",
    "DiversityDecision",
    "DiversityMemory",
    "load_diversity_memory",
]
