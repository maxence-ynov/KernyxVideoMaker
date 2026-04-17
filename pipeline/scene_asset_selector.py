from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import unicodedata

from job_paths import resolve_job_path
from pipeline.diversity_memory import load_diversity_memory
from pipeline.scene_planner import SceneSegment
from schemas import VideoJob

LOGGER = logging.getLogger(__name__)

ASSET_CATALOG_INLINE_KEYS = ("scene_asset_catalog", "asset_catalog", "visual_asset_catalog")
ASSET_CATALOG_PATH_KEYS = (
    "scene_asset_catalog_path",
    "asset_catalog_path",
    "visual_asset_catalog_path",
)
AUTOMATION_IMPLICIT_ASSET_DIRS = ("tiktok/photo", "tiktok/video")
AUTOMATION_TOOL_ENTITIES = (
    "zapier",
    "make",
    "n8n",
    "notion",
    "airtable",
    "hubspot",
    "slack",
    "chatgpt",
    "claude",
    "openai",
)
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".ppm", ".webp"}
RECENT_REUSE_WINDOW = 2
SOFT_MATCH_MIN_SCORE = 20
SOFT_GENERIC_MIN_SCORE = 34
WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
ENTITY_TOKEN_RE = re.compile(r"^[a-z]+(?:-[a-z]+)*$")
REQUIRED_ENTITY_MIN_LENGTH = 4
TERM_STOP_WORDS = {
    "a",
    "an",
    "and",
    "au",
    "aux",
    "avec",
    "avoir",
    "ce",
    "ces",
    "chez",
    "comment",
    "contre",
    "dans",
    "de",
    "des",
    "du",
    "en",
    "entre",
    "etre",
    "et",
    "faire",
    "for",
    "how",
    "ici",
    "il",
    "je",
    "la",
    "le",
    "les",
    "mais",
    "mes",
    "mettre",
    "moins",
    "mon",
    "ma",
    "manque",
    "manquez",
    "ne",
    "notre",
    "ou",
    "par",
    "pas",
    "plus",
    "pour",
    "pourquoi",
    "que",
    "qui",
    "rendre",
    "sans",
    "sa",
    "ses",
    "son",
    "sous",
    "sur",
    "ta",
    "tes",
    "the",
    "this",
    "ton",
    "tres",
    "trop",
    "tu",
    "un",
    "une",
    "vrai",
    "vraie",
    "vraies",
    "vrais",
    "vos",
    "votre",
}
NON_CONCRETE_ENTITY_BLACKLIST = {
    "cause",
    "causes",
    "doit",
    "doivent",
    "fait",
    "faite",
    "faites",
    "font",
    "perd",
    "perdent",
    "perdre",
    "perdu",
    "perdue",
    "presente",
    "presentent",
    "presenter",
    "presentation",
    "rarement",
    "sujet",
    "sujets",
    "suffire",
    "suffit",
    "tiktok",
    "tout",
    "visualise",
    "visualisent",
    "visualiser",
}
ABSTRACT_HINTS = {
    "ai",
    "analytics",
    "automatisation",
    "automation",
    "automatiser",
    "automatisations",
    "business",
    "croissance",
    "efficacite",
    "efficiency",
    "energie",
    "growth",
    "ia",
    "idee",
    "idees",
    "intro",
    "intros",
    "inutile",
    "kpi",
    "metrics",
    "performance",
    "pipeline",
    "process",
    "processus",
    "productivite",
    "qualite",
    "saas",
    "scaling",
    "system",
    "systeme",
    "systemes",
    "workflow",
}
GENERIC_ENTITY_BLACKLIST = {
    "animal",
    "astuce",
    "astuces",
    "brand",
    "browser",
    "business",
    "case",
    "client",
    "clients",
    "community",
    "concept",
    "creator",
    "cta",
    "dashboard",
    "erreur",
    "erreurs",
    "example",
    "exemple",
    "exemples",
    "hook",
    "idee",
    "idees",
    "illustration",
    "interface",
    "methode",
    "methodes",
    "nature",
    "outil",
    "outils",
    "preuve",
    "proof",
    "process",
    "processus",
    "produit",
    "produits",
    "result",
    "resultat",
    "resultats",
    "scene",
    "screenshot",
    "software",
    "solution",
    "solutions",
    "system",
    "systeme",
    "systemes",
    "tool",
    "tools",
    "ui",
    "video",
    "videos",
    "visual",
    "workflow",
}
REQUIRED_ENTITY_EXTRA_STOP_WORDS = {
    "agressif",
    "agressive",
    "agressifs",
    "agressives",
    "autre",
    "autres",
    "clair",
    "claire",
    "clairs",
    "claires",
    "concret",
    "concrete",
    "concrets",
    "concretes",
    "dangereux",
    "dangereuse",
    "dangereuses",
    "flou",
    "floue",
    "flous",
    "floues",
    "fort",
    "forte",
    "fortes",
    "forts",
    "manquer",
    "message",
    "messages",
    "net",
    "nette",
    "nettes",
    "nete",
    "probleme",
    "problemes",
    "rapide",
    "rapides",
    "reel",
    "reelle",
    "reelles",
    "reels",
    "suite",
    "utile",
    "vite",
}
EXPLICIT_CONCRETE_VISUAL_ENTITY_WHITELIST = {
    "hippopotame",
    "lion",
}
CONCRETE_VISUAL_CATEGORIES = {
    "animal",
    "brand",
    "city",
    "creature",
    "food",
    "human",
    "landmark",
    "nature",
    "object",
    "person",
    "pet",
    "place",
    "product",
    "vehicle",
    "wildlife",
}
SCENE_TYPE_CATEGORY_HINTS = {
    "hook": ("concept", "attention"),
    "explain": ("concept", "illustration"),
    "workflow": ("workflow", "automation", "process", "system"),
    "proof": ("proof", "result", "dashboard", "metrics", "analytics"),
    "interface": ("ui", "software", "screenshot", "product"),
    "cta": ("creator", "brand", "community"),
}
VISUAL_MODE_CATEGORY_HINTS = {
    "dashboard": ("dashboard", "analytics", "metrics"),
    "workflow": ("workflow", "automation", "process", "diagram"),
    "ui": ("ui", "software", "product"),
    "screenshot": ("screenshot", "browser", "ui"),
}
FRENCH_ELISION_PREFIXES = {"c", "d", "j", "l", "m", "n", "qu", "s", "t"}
UI_EVIDENCE_TAGS = frozenset({"app", "interface", "screen", "screenshot", "software", "tool", "ui"})
WORKFLOW_EVIDENCE_TAGS = frozenset({"agent", "automation", "process", "system", "workflow"})
DASHBOARD_EVIDENCE_TAGS = frozenset({"analytics", "dashboard", "data", "metrics", "proof", "result"})
GENERIC_STOCK_TAGS = frozenset({"background", "business", "concept", "illustration", "stock"})


class AssetCatalogError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class AssetCatalogEntry:
    path: Path
    asset_type: str
    tags: tuple[str, ...]
    entities: tuple[str, ...]
    visual_categories: tuple[str, ...]
    allowed_scene_types: tuple[str, ...]
    style: tuple[str, ...] = ()
    language: str | None = None
    source_kind: str = ""

    @property
    def selection_key(self) -> str:
        return str(self.path)


@dataclass(slots=True, frozen=True)
class AssetCatalog:
    entries: list[AssetCatalogEntry]
    source_key: str


@dataclass(slots=True, frozen=True)
class SceneVisualIntent:
    scene_id: str
    scene_type: str
    keywords: tuple[str, ...]
    required_entities: tuple[str, ...]
    preferred_tags: tuple[str, ...]
    preferred_visual_categories: tuple[str, ...]
    forbidden_tags: tuple[str, ...]
    match_mode: str
    reuse_penalty_enabled: bool


@dataclass(slots=True)
class AssetSelectionContext:
    current_video_usage_counts: dict[str, int] = field(default_factory=dict)
    recent_asset_keys: list[str] = field(default_factory=list)
    historical_usage_counts: dict[str, int] = field(default_factory=dict)
    recent_window_size: int = RECENT_REUSE_WINDOW


@dataclass(slots=True, frozen=True)
class AssetCandidate:
    entry: AssetCatalogEntry
    score: int
    matched_tags: tuple[str, ...]
    matched_entities: tuple[str, ...]
    matched_visual_categories: tuple[str, ...]
    reuse_rank: int


def load_asset_catalog(job: VideoJob) -> AssetCatalog | None:
    for key in ASSET_CATALOG_INLINE_KEYS:
        raw_value = job.metadata.get(key)
        if raw_value is None:
            continue
        entries = _parse_catalog_payload(job, raw_value, source_key=key)
        return AssetCatalog(entries=entries, source_key=key)

    for key in ASSET_CATALOG_PATH_KEYS:
        raw_value = job.metadata.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        catalog_path = resolve_job_path(job, raw_value)
        if not catalog_path.exists():
            raise AssetCatalogError(f"asset catalog '{key}' does not exist: {catalog_path}")
        if not catalog_path.is_file():
            raise AssetCatalogError(f"asset catalog '{key}' is not a file: {catalog_path}")
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        entries = _parse_catalog_payload(job, payload, source_key=key)
        return AssetCatalog(entries=entries, source_key=key)

    return None


def load_implicit_automation_asset_catalog(job: VideoJob) -> AssetCatalog | None:
    entries: list[AssetCatalogEntry] = []
    seen_paths: set[Path] = set()
    for root in _iter_automation_asset_roots(job):
        for relative_dir in AUTOMATION_IMPLICIT_ASSET_DIRS:
            asset_dir = (root / relative_dir).resolve()
            if not asset_dir.exists() or not asset_dir.is_dir():
                continue
            for asset_path in sorted(asset_dir.iterdir()):
                if not asset_path.is_file():
                    continue
                resolved_path = asset_path.resolve()
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)
                asset_type = _infer_asset_type(resolved_path)
                normalized_stem = _normalize_phrase(resolved_path.stem)
                tags = _unique(
                    [
                        "automation",
                        "business",
                        *(_extract_terms(normalized_stem)),
                        *_derive_implicit_automation_tags(normalized_stem, asset_type=asset_type),
                    ]
                )
                entities = _unique(
                    entity
                    for entity in AUTOMATION_TOOL_ENTITIES
                    if _scene_mentions_phrase(
                        entity,
                        scene_text=normalized_stem,
                        scene_terms=tags,
                    )
                )
                visual_categories = _derive_implicit_automation_visual_categories(
                    asset_type=asset_type,
                    tags=tags,
                    entities=entities,
                )
                entries.append(
                    AssetCatalogEntry(
                        path=resolved_path,
                        asset_type=asset_type,
                        tags=tuple(tags),
                        entities=tuple(entities),
                        visual_categories=tuple(visual_categories),
                        allowed_scene_types=("hook", "explain", "workflow", "proof", "interface"),
                        source_kind=f"implicit_automation_catalog[{len(entries) + 1}]",
                    )
                )

    if not entries:
        return None
    return AssetCatalog(entries=entries, source_key="implicit_automation_catalog")


def build_asset_selection_context(job: VideoJob) -> AssetSelectionContext:
    historical_usage_counts: dict[str, int] = {}
    memory = load_diversity_memory(job)
    for recent_job in memory.recent_jobs:
        for asset_key in _extract_recent_asset_keys(recent_job):
            historical_usage_counts[asset_key] = historical_usage_counts.get(asset_key, 0) + 1
    return AssetSelectionContext(historical_usage_counts=historical_usage_counts)


def register_selected_asset(selection_context: AssetSelectionContext, asset_key: str) -> int:
    normalized_key = asset_key.strip()
    if not normalized_key:
        return 0

    reuse_rank = selection_context.current_video_usage_counts.get(normalized_key, 0)
    selection_context.current_video_usage_counts[normalized_key] = reuse_rank + 1
    selection_context.recent_asset_keys.append(normalized_key)
    if len(selection_context.recent_asset_keys) > selection_context.recent_window_size:
        selection_context.recent_asset_keys = selection_context.recent_asset_keys[
            -selection_context.recent_window_size :
        ]
    return reuse_rank


def build_scene_visual_intent(
    scene: SceneSegment,
    asset_catalog: AssetCatalog | None = None,
    *,
    global_topic: str | None = None,
) -> SceneVisualIntent:
    scene_keywords = _unique(
        _normalize_term(keyword)
        for keyword in scene.keywords
    )
    global_topic_text = _normalize_phrase(global_topic or "")
    strict_source_text = _normalize_phrase(
        " ".join(
            part
            for part in (
                *scene_keywords,
                scene.title_text,
                scene.highlight_text,
                global_topic_text,
            )
            if part
        )
    )
    soft_source_text = _normalize_phrase(
        " ".join(
            part
            for part in (
                scene.source_text,
                *scene_keywords,
                scene.title_text,
                scene.highlight_text,
                global_topic_text,
            )
            if part
        )
    )
    strict_source_terms = _unique([*scene_keywords, *_extract_terms(strict_source_text)])
    soft_source_terms = _unique([*scene_keywords, *_extract_terms(soft_source_text)])

    candidate_entities = _unique(
        [
            *_extract_concrete_entities_from_values(scene_keywords),
            *_extract_concrete_entities_from_values(
                [scene.title_text, scene.highlight_text, global_topic_text]
            ),
        ]
    )
    catalog_backed_entities = _extract_catalog_backed_entities(
        asset_catalog=asset_catalog,
        candidate_entities=candidate_entities,
        scene_text=strict_source_text,
        scene_terms=strict_source_terms,
    )
    whitelisted_entities = _extract_explicit_visual_entities(candidate_entities)
    required_entities = [*catalog_backed_entities, *whitelisted_entities]
    preferred_tags: list[str] = list(_unique([*soft_source_terms, *candidate_entities]))
    preferred_visual_categories: list[str] = []

    if asset_catalog is not None:
        for entry in asset_catalog.entries:
            entity_match = False
            for entity in entry.entities:
                normalized_entity = _normalize_term(entity)
                if _scene_mentions_phrase(
                    normalized_entity,
                    scene_text=soft_source_text,
                    scene_terms=soft_source_terms,
                ):
                    entity_match = True
            tag_match = False
            for tag in entry.tags:
                if _scene_mentions_phrase(
                    tag,
                    scene_text=soft_source_text,
                    scene_terms=soft_source_terms,
                ):
                    preferred_tags.append(tag)
                    tag_match = True
            if entity_match or tag_match:
                preferred_visual_categories.extend(entry.visual_categories)

    preferred_visual_categories.extend(SCENE_TYPE_CATEGORY_HINTS.get(_normalize_term(scene.scene_type), ()))
    preferred_visual_categories.extend(
        VISUAL_MODE_CATEGORY_HINTS.get(_normalize_term(scene.visual_asset_mode), ())
    )

    required_entities = _unique(required_entities)
    preferred_tags = _unique(tag for tag in preferred_tags if tag and tag not in required_entities)
    preferred_visual_categories = _unique(preferred_visual_categories)

    match_mode = "strict" if required_entities else "soft"
    if catalog_backed_entities and whitelisted_entities:
        decision_reason = "strict: catalog-backed and whitelisted concrete entities found"
    elif catalog_backed_entities:
        decision_reason = "strict: catalog-backed concrete entities found"
    elif whitelisted_entities:
        decision_reason = "strict: whitelisted concrete entities found"
    else:
        decision_reason = "soft: no catalog-backed or whitelisted concrete entity found"

    LOGGER.debug(
        (
            "scene_visual_intent scene_id=%s global_topic=%s retained_entity_source=%s "
            "candidate_entities=%s catalog_backed_entities=%s required_entities=%s "
            "match_mode=%s reason=%s"
        ),
        scene.scene_id,
        global_topic_text,
        strict_source_text,
        candidate_entities,
        catalog_backed_entities,
        required_entities,
        match_mode,
        decision_reason,
    )

    return SceneVisualIntent(
        scene_id=scene.scene_id,
        scene_type=_normalize_term(scene.scene_type) or "explain",
        keywords=tuple(scene_keywords),
        required_entities=tuple(required_entities),
        preferred_tags=tuple(preferred_tags),
        preferred_visual_categories=tuple(preferred_visual_categories),
        forbidden_tags=(),
        match_mode=match_mode,
        reuse_penalty_enabled=True,
    )


def resolve_scene_asset(
    scene_intent: SceneVisualIntent,
    asset_catalog: AssetCatalog,
    selection_context: AssetSelectionContext,
) -> AssetCandidate | None:
    candidates: list[AssetCandidate] = []
    for entry in asset_catalog.entries:
        candidate = _score_catalog_entry(scene_intent, entry, selection_context)
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        if scene_intent.match_mode == "strict":
            raise AssetCatalogError(
                "no catalog asset matches strict scene intent "
                f"for scene '{scene_intent.scene_id}' "
                f"(required_entities={list(scene_intent.required_entities)})"
            )
        return None

    if scene_intent.match_mode == "soft":
        candidates = [
            candidate
            for candidate in candidates
            if _is_soft_scene_candidate_eligible(candidate)
        ]
        if not candidates:
            return None
        evidence_first_candidates = [
            candidate
            for candidate in candidates
            if not _is_generic_asset_entry(candidate.entry)
        ]
        if evidence_first_candidates:
            candidates = evidence_first_candidates

    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.score,
            candidate.reuse_rank,
            selection_context.historical_usage_counts.get(candidate.entry.selection_key, 0),
            str(candidate.entry.path),
        ),
    )[0]


def _parse_catalog_payload(
    job: VideoJob,
    payload: object,
    *,
    source_key: str,
) -> list[AssetCatalogEntry]:
    raw_entries: object
    if isinstance(payload, Mapping):
        raw_entries = payload.get("assets", payload.get("entries"))
    else:
        raw_entries = payload

    if not isinstance(raw_entries, list):
        raise AssetCatalogError(
            f"asset catalog '{source_key}' must be a list or an object with 'assets'"
        )
    if not raw_entries:
        raise AssetCatalogError(f"asset catalog '{source_key}' must not be empty")

    entries: list[AssetCatalogEntry] = []
    for index, item in enumerate(raw_entries, start=1):
        if not isinstance(item, Mapping):
            raise AssetCatalogError(
                f"asset catalog '{source_key}' entry #{index} must be an object"
            )
        entries.append(
            _parse_catalog_entry(
                job,
                item,
                source_kind=f"{source_key}[{index}]",
            )
        )
    return entries


def _iter_automation_asset_roots(job: VideoJob) -> list[Path]:
    candidates: list[Path] = []
    raw_config_path = _read_optional_text(job.metadata.get("config_path"))
    if raw_config_path is not None:
        config_path = Path(raw_config_path).expanduser().resolve()
        candidates.extend([config_path.parent, config_path.parent.parent])
    candidates.append(Path.cwd().resolve())

    roots: list[Path] = []
    for candidate in candidates:
        if candidate in roots:
            continue
        roots.append(candidate)
    return roots


def _derive_implicit_automation_tags(
    normalized_stem: str,
    *,
    asset_type: str,
) -> list[str]:
    tags: list[str] = []
    if asset_type == "video":
        tags.extend(["workflow", "process", "system"])
    else:
        tags.extend(["dashboard", "ui", "software", "screenshot"])

    if any(token in normalized_stem for token in ("dashboard", "graph", "graphs", "statistics", "monitoring")):
        tags.extend(["dashboard", "analytics", "metrics", "proof"])
    if any(token in normalized_stem for token in ("server", "systems", "support")):
        tags.extend(["ops", "system", "monitoring"])
    if any(token in normalized_stem for token in ("laptop", "computer", "screen", "office", "startup")):
        tags.extend(["ui", "software", "workspace", "screen"])
    if any(token in normalized_stem for token in ("strategy", "planning", "financial", "marketing")):
        tags.extend(["business", "ops", "planning"])
    return tags


def _derive_implicit_automation_visual_categories(
    *,
    asset_type: str,
    tags: Sequence[str],
    entities: Sequence[str],
) -> list[str]:
    categories: list[str] = ["automation", "business"]
    if asset_type == "video":
        categories.extend(["workflow", "process", "system"])
    else:
        categories.extend(["dashboard", "ui", "software", "screenshot"])

    tag_set = set(tags)
    if {"dashboard", "analytics", "metrics", "proof"}.intersection(tag_set):
        categories.extend(["dashboard", "analytics", "metrics", "proof", "result"])
    if {"ui", "software", "screen", "screenshot"}.intersection(tag_set):
        categories.extend(["ui", "software", "screenshot", "product"])
    if {"workflow", "process", "system", "ops"}.intersection(tag_set):
        categories.extend(["workflow", "process", "system", "automation"])
    if entities:
        categories.extend(["software", "product", "ui"])
    return _unique(categories)


def _parse_catalog_entry(
    job: VideoJob,
    payload: Mapping[str, Any],
    *,
    source_kind: str,
) -> AssetCatalogEntry:
    raw_path = _read_optional_text(payload.get("path")) or _read_optional_text(payload.get("asset_path"))
    if raw_path is None:
        raise AssetCatalogError(f"asset catalog entry '{source_kind}' is missing 'path'")

    resolved_path = resolve_job_path(job, raw_path)
    if not resolved_path.exists():
        raise AssetCatalogError(f"asset catalog entry '{source_kind}' does not exist: {resolved_path}")
    if not resolved_path.is_file():
        raise AssetCatalogError(f"asset catalog entry '{source_kind}' is not a file: {resolved_path}")

    raw_asset_type = _normalize_term(_read_optional_text(payload.get("asset_type")) or "")
    asset_type = raw_asset_type or _infer_asset_type(resolved_path)
    if asset_type not in {"image", "video"}:
        raise AssetCatalogError(
            f"asset catalog entry '{source_kind}' has an invalid asset_type: {asset_type}"
        )

    tags = _unique(
        [
            *_read_string_list(payload.get("tags")),
            *_read_string_list(payload.get("topics")),
        ]
    )
    entities = _unique(_read_string_list(payload.get("entities")))
    visual_categories = _unique(_read_string_list(payload.get("visual_categories")))
    allowed_scene_types = _unique(_read_string_list(payload.get("allowed_scene_types")))
    style = tuple(_unique(_read_string_list(payload.get("style"))))
    language = _normalize_term(_read_optional_text(payload.get("language")) or "")

    return AssetCatalogEntry(
        path=resolved_path.resolve(),
        asset_type=asset_type,
        tags=tuple(tags),
        entities=tuple(entities),
        visual_categories=tuple(visual_categories),
        allowed_scene_types=tuple(allowed_scene_types),
        style=style,
        language=language or None,
        source_kind=source_kind,
    )


def _score_catalog_entry(
    scene_intent: SceneVisualIntent,
    entry: AssetCatalogEntry,
    selection_context: AssetSelectionContext,
) -> AssetCandidate | None:
    entry_tags = set(entry.tags)
    entry_entities = set(entry.entities)
    entry_categories = set(entry.visual_categories)
    forbidden_tags = set(scene_intent.forbidden_tags)

    if forbidden_tags and forbidden_tags.intersection(entry_tags):
        return None

    required_entity_set = set(scene_intent.required_entities)
    matched_entities = tuple(sorted(required_entity_set.intersection(entry_entities)))
    strict_tag_matches = tuple(sorted(required_entity_set.intersection(entry_tags)))
    strict_matches = tuple(sorted(set(matched_entities).union(strict_tag_matches)))
    if scene_intent.match_mode == "strict" and scene_intent.required_entities and not strict_matches:
        return None

    matched_tags = tuple(
        sorted(set(scene_intent.preferred_tags).intersection(entry_tags).union(strict_tag_matches))
    )
    matched_visual_categories = tuple(
        sorted(set(scene_intent.preferred_visual_categories).intersection(entry_categories))
    )

    score = 0
    if strict_matches:
        score += 50
    if matched_tags:
        score += 20
    if matched_visual_categories:
        score += 20
    if entry.allowed_scene_types and scene_intent.scene_type in entry.allowed_scene_types:
        score += 10
    score += _score_evidence_priority(entry)
    if _is_generic_asset_entry(entry):
        score -= 18
        if scene_intent.match_mode == "soft":
            score -= 8

    asset_key = entry.selection_key
    reuse_rank = selection_context.current_video_usage_counts.get(asset_key, 0)
    if scene_intent.reuse_penalty_enabled:
        if asset_key in selection_context.recent_asset_keys[-selection_context.recent_window_size :]:
            score -= 30
        if reuse_rank:
            score -= 15 * reuse_rank
        else:
            score += 8
        historical_usage = selection_context.historical_usage_counts.get(asset_key, 0)
        score -= 5 * historical_usage

    return AssetCandidate(
        entry=entry,
        score=score,
        matched_tags=matched_tags,
        matched_entities=matched_entities,
        matched_visual_categories=matched_visual_categories,
        reuse_rank=reuse_rank,
    )


def _extract_recent_asset_keys(job: VideoJob) -> list[str]:
    asset_keys: list[str] = []
    raw_visual_assets = job.metadata.get("visual_scene_assets")
    if isinstance(raw_visual_assets, list):
        for item in raw_visual_assets:
            if not isinstance(item, Mapping):
                continue
            if _normalize_term(_read_optional_text(item.get("selected_by")) or "") == "generated":
                continue
            asset_key = _read_optional_text(item.get("asset_origin_path")) or _read_optional_text(
                item.get("asset_path")
            )
            if asset_key:
                asset_keys.append(asset_key)
    elif isinstance(job.metadata.get("visual_manifest"), Mapping):
        raw_manifest = job.metadata.get("visual_manifest")
        if isinstance(raw_manifest, Mapping):
            raw_scenes = raw_manifest.get("scenes")
            if isinstance(raw_scenes, list):
                for item in raw_scenes:
                    if not isinstance(item, Mapping):
                        continue
                    if _normalize_term(_read_optional_text(item.get("selected_by")) or "") == "generated":
                        continue
                    asset_key = _read_optional_text(item.get("asset_origin_path")) or _read_optional_text(
                        item.get("asset_path")
                    )
                    if asset_key:
                        asset_keys.append(asset_key)

    raw_single_source = _read_optional_text(job.metadata.get("visual_source_origin"))
    if raw_single_source:
        asset_keys.append(raw_single_source)

    return asset_keys


def _has_semantic_match(candidate: AssetCandidate) -> bool:
    return bool(
        candidate.matched_entities
        or candidate.matched_tags
        or candidate.matched_visual_categories
    )


def _is_soft_scene_candidate_eligible(candidate: AssetCandidate) -> bool:
    if not _has_semantic_match(candidate):
        return False
    minimum_score = SOFT_GENERIC_MIN_SCORE if _is_generic_asset_entry(candidate.entry) else SOFT_MATCH_MIN_SCORE
    return candidate.score >= minimum_score


def _score_evidence_priority(entry: AssetCatalogEntry) -> int:
    evidence_rank = _classify_asset_evidence_rank(entry)
    if evidence_rank == 0:
        return 36
    if evidence_rank == 1:
        return 24
    if evidence_rank == 2:
        return 16
    return 0


def _classify_asset_evidence_rank(entry: AssetCatalogEntry) -> int:
    signal_pool = {
        *entry.tags,
        *entry.entities,
        *entry.visual_categories,
    }
    if signal_pool.intersection(UI_EVIDENCE_TAGS):
        return 0
    if signal_pool.intersection(WORKFLOW_EVIDENCE_TAGS):
        return 1
    if signal_pool.intersection(DASHBOARD_EVIDENCE_TAGS):
        return 2
    return 3


def _is_generic_asset_entry(entry: AssetCatalogEntry) -> bool:
    if entry.entities:
        return False
    signal_pool = {
        *entry.tags,
        *entry.visual_categories,
    }
    evidence_tags = UI_EVIDENCE_TAGS | WORKFLOW_EVIDENCE_TAGS | DASHBOARD_EVIDENCE_TAGS
    if signal_pool.intersection(evidence_tags):
        return False
    return not signal_pool or bool(signal_pool.intersection(GENERIC_STOCK_TAGS))


def _scene_mentions_phrase(
    phrase: str,
    *,
    scene_text: str,
    scene_terms: Sequence[str],
) -> bool:
    normalized_phrase = _normalize_phrase(phrase)
    if not normalized_phrase:
        return False
    if " " in normalized_phrase:
        return normalized_phrase in scene_text
    return normalized_phrase in scene_terms


def _extract_terms(text: str) -> list[str]:
    return _unique(
        term
        for term in (_normalize_extracted_term(match) for match in WORD_RE.findall(text))
        if term and len(term) > 1 and term not in TERM_STOP_WORDS
    )


def _extract_concrete_entities_from_values(values: Sequence[str]) -> list[str]:
    candidates: list[str] = []
    for value in values:
        normalized_value = _normalize_term(value)
        if not normalized_value:
            continue
        if _is_reliable_required_entity_phrase(normalized_value):
            candidates.append(_normalize_entity_phrase_candidate(normalized_value))
        candidates.extend(
            term
            for term in _extract_terms(normalized_value)
            if is_concrete_entity_candidate(term)
        )
    return _unique(candidates)


def _extract_catalog_backed_entities(
    *,
    asset_catalog: AssetCatalog | None,
    candidate_entities: Sequence[str],
    scene_text: str,
    scene_terms: Sequence[str],
) -> list[str]:
    if asset_catalog is None:
        return []

    candidate_entity_set = {
        normalized_candidate
        for normalized_candidate in (_normalize_term(candidate) for candidate in candidate_entities)
        if normalized_candidate
    }
    candidates: list[str] = []
    for entry in asset_catalog.entries:
        for phrase in [*entry.entities, *entry.tags]:
            normalized_phrase = _normalize_term(phrase)
            if not _is_reliable_required_entity_phrase(normalized_phrase):
                continue
            if _scene_mentions_phrase(
                normalized_phrase,
                scene_text=scene_text,
                scene_terms=scene_terms,
            ) and (
                normalized_phrase in candidate_entity_set or " " in normalized_phrase
            ):
                candidates.append(normalized_phrase)
    return _unique(candidates)


def _extract_explicit_visual_entities(candidate_entities: Sequence[str]) -> list[str]:
    return _unique(
        normalized_entity
        for normalized_entity in (_normalize_term(entity) for entity in candidate_entities)
        if normalized_entity in EXPLICIT_CONCRETE_VISUAL_ENTITY_WHITELIST
    )


def _is_reliable_required_entity_phrase(value: str) -> bool:
    normalized_value = _normalize_term(value)
    if not normalized_value:
        return False
    tokens = normalized_value.split()
    if not tokens:
        return False
    return all(is_concrete_entity_candidate(token) for token in tokens)


def is_concrete_entity_candidate(value: str) -> bool:
    normalized_value = _normalize_extracted_term(value)
    if not normalized_value:
        return False
    if len(normalized_value) < REQUIRED_ENTITY_MIN_LENGTH:
        return False
    if normalized_value in ABSTRACT_HINTS:
        return False
    if normalized_value in NON_CONCRETE_ENTITY_BLACKLIST:
        return False
    if normalized_value in GENERIC_ENTITY_BLACKLIST:
        return False
    if normalized_value in REQUIRED_ENTITY_EXTRA_STOP_WORDS:
        return False
    if normalized_value in TERM_STOP_WORDS:
        return False
    if not ENTITY_TOKEN_RE.fullmatch(normalized_value):
        return False
    return True


def _read_string_list(value: object) -> list[str]:
    raw_items: list[object]
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, Sequence):
        raw_items = list(value)
    else:
        return []

    items: list[str] = []
    for item in raw_items:
        if isinstance(item, str):
            normalized = _normalize_term(item)
            if normalized:
                items.append(normalized)
    return items


def _read_optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _infer_asset_type(path: Path) -> str:
    return "image" if path.suffix.lower() in IMAGE_SUFFIXES else "video"


def _normalize_phrase(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value)
    folded_value = "".join(character for character in ascii_value if not unicodedata.combining(character))
    return " ".join(folded_value.lower().strip().split())


def _normalize_term(value: str) -> str:
    normalized = _normalize_phrase(value)
    normalized = normalized.replace("/", " ").replace("_", " ")
    normalized = re.sub(r"[^\w\s'-]+", " ", normalized)
    return " ".join(normalized.split())


def _normalize_extracted_term(value: str) -> str:
    normalized = _normalize_term(value).strip("'-")
    prefix, separator, suffix = normalized.partition("'")
    if separator and prefix in FRENCH_ELISION_PREFIXES and suffix:
        normalized = suffix.strip("'-")
    return normalized


def _normalize_entity_phrase_candidate(value: str) -> str:
    return " ".join(
        normalized_token
        for normalized_token in (_normalize_extracted_term(token) for token in _normalize_term(value).split())
        if normalized_token
    )


def _unique(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


__all__ = [
    "AssetCandidate",
    "AssetCatalog",
    "AssetCatalogEntry",
    "AssetCatalogError",
    "AssetSelectionContext",
    "SceneVisualIntent",
    "build_asset_selection_context",
    "build_scene_visual_intent",
    "load_asset_catalog",
    "load_implicit_automation_asset_catalog",
    "register_selected_asset",
    "resolve_scene_asset",
]
