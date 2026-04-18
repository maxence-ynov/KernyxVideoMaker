from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata
from typing import Sequence

WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;:])\s+|(?<=,)\s+|\n+", flags=re.UNICODE)
EMPHASIS_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9%+./]+(?:[-'][A-Za-zÀ-ÿ0-9%+./]+)*", flags=re.UNICODE)
NUMBER_TOKEN_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?(?:[%x]|k|m|ms|s)?$", flags=re.IGNORECASE)
NUMBER_PHRASE_RE = re.compile(
    r"^\d+(?:[.,]\d+)?(?:%|x|k|m|ms|s|min|mn|h|j)?$",
    flags=re.IGNORECASE,
)
DEFAULT_CAPTION_MAX_WORDS_PER_CHUNK = 5
DEFAULT_CAPTION_MAX_CHARS_PER_LINE = 18
DEFAULT_CAPTION_MAX_LINES = 2
DEFAULT_CAPTION_MIN_DURATION_MS = 650
DEFAULT_CAPTION_MAX_DURATION_MS = 2200
DEFAULT_CAPTION_GAP_MS = 30
DEFAULT_CAPTION_SAFE_BOTTOM_MARGIN = 260
DEFAULT_CAPTION_SAFE_SIDE_MARGIN = 100
DEFAULT_CAPTION_FONT_NAME = "Noto Sans"
DEFAULT_FALLBACK_SUBTITLE_FONT_SIZE = 28
DEFAULT_CAPTION_HIGHLIGHT_COLOR = "&H0038C8FF"
TOOL_TOKENS = frozenset(
    {
        "airtable",
        "analytics",
        "api",
        "ass",
        "capcut",
        "chatgpt",
        "crm",
        "dashboard",
        "edge-tts",
        "figma",
        "ffmpeg",
        "hubspot",
        "make",
        "n8n",
        "notion",
        "openai",
        "srt",
        "slack",
        "stack",
        "typeform",
        "ui",
        "workflow",
        "zapier",
    }
)
PROOF_TOKENS = frozenset(
    {
        "analytics",
        "audit",
        "capture",
        "captures",
        "chiffre",
        "chiffres",
        "concret",
        "concrets",
        "conversion",
        "demo",
        "gain",
        "mesurable",
        "mesurables",
        "preuve",
        "preuves",
        "proof",
        "retention",
        "resultat",
        "resultats",
        "result",
        "results",
        "screen",
        "screenshot",
        "temps",
    }
)
RESULT_TOKENS = frozenset(
    {
        "augmente",
        "baisse",
        "double",
        "gagne",
        "grimper",
        "grimpe",
        "impact",
        "moins",
        "plus",
        "ratio",
        "reduce",
        "reduire",
        "retention",
        "resultat",
        "resultats",
        "result",
        "score",
        "seconde",
        "secondes",
        "temps",
        "x2",
    }
)
OPPOSITION_TOKENS = frozenset({"mais", "pas", "pourtant", "sauf", "sinon", "vraiment"})
ACTION_TOKENS = frozenset(
    {
        "abonne-toi",
        "commente",
        "copie",
        "copiez",
        "essaie",
        "essaies",
        "garde",
        "like",
        "partage",
        "regarde",
        "teste",
        "utilise",
        "viens",
    }
)
WEAK_TOKENS = frozenset(
    {
        "a",
        "ai",
        "as",
        "avec",
        "avoir",
        "ca",
        "ce",
        "ces",
        "cette",
        "cest",
        "comment",
        "dans",
        "de",
        "des",
        "du",
        "elle",
        "en",
        "et",
        "etre",
        "fait",
        "faire",
        "faut",
        "il",
        "je",
        "juste",
        "la",
        "le",
        "les",
        "leur",
        "mais",
        "mes",
        "mon",
        "ne",
        "nous",
        "on",
        "ou",
        "par",
        "pas",
        "plus",
        "pour",
        "que",
        "qui",
        "sa",
        "se",
        "ses",
        "son",
        "sur",
        "ta",
        "te",
        "tes",
        "ton",
        "trop",
        "tu",
        "un",
        "une",
        "vrai",
        "vraie",
        "voir",
        "votre",
        "vos",
    }
)


@dataclass(slots=True, frozen=True)
class CaptionTimingSettings:
    max_words_per_chunk: int
    max_chars_per_line: int
    max_lines: int
    min_duration_ms: int
    max_duration_ms: int
    gap_ms: int = DEFAULT_CAPTION_GAP_MS

    @property
    def max_chars_per_chunk(self) -> int:
        return self.max_chars_per_line * max(1, self.max_lines)

    @property
    def min_duration_seconds(self) -> float:
        return self.min_duration_ms / 1000.0

    @property
    def max_duration_seconds(self) -> float:
        return self.max_duration_ms / 1000.0

    @property
    def gap_seconds(self) -> float:
        return self.gap_ms / 1000.0


@dataclass(slots=True, frozen=True)
class CaptionRenderSettings:
    max_chars_per_line: int
    max_lines: int
    safe_bottom_margin: int
    safe_side_margin: int


@dataclass(slots=True, frozen=True)
class CaptionWordBoundary:
    index: int
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(slots=True, frozen=True)
class CaptionCuePlan:
    index: int
    text: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    word_count: int
    timing_source: str

    def to_metadata(self) -> dict[str, str | int | float]:
        return {
            "index": self.index,
            "text": self.text,
            "start_seconds": round(self.start_seconds, 3),
            "end_seconds": round(self.end_seconds, 3),
            "duration_seconds": round(self.duration_seconds, 3),
            "word_count": self.word_count,
            "timing_source": self.timing_source,
        }


@dataclass(slots=True, frozen=True)
class CaptionKeywordCandidate:
    token: str
    normalized_token: str
    score: int
    reasons: tuple[str, ...]
    position: int


@dataclass(slots=True, frozen=True)
class CaptionEmphasisDecision:
    tokens: tuple[str, ...]
    skipped_reason: str | None
    candidates: tuple[CaptionKeywordCandidate, ...] = ()


@dataclass(slots=True)
class _BoundaryChunk:
    text: str
    word_count: int
    start_index: int
    boundary_count: int


def normalize_caption_text(value: str) -> str:
    return " ".join(value.replace("\u2019", "'").split()).strip()


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def parse_edge_tts_word_boundaries(path: Path) -> list[CaptionWordBoundary]:
    if not path.exists():
        return []

    boundaries: list[CaptionWordBoundary] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("type") != "WordBoundary":
                continue
            offset = payload.get("offset")
            duration = payload.get("duration")
            text = normalize_caption_text(str(payload.get("text", "")))
            if not isinstance(offset, (int, float)) or not isinstance(duration, (int, float)) or not text:
                continue
            start_seconds = float(offset) / 10_000_000.0
            end_seconds = max(start_seconds, (float(offset) + float(duration)) / 10_000_000.0)
            boundaries.append(
                CaptionWordBoundary(
                    index=len(boundaries) + 1,
                    text=text,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
    except (OSError, json.JSONDecodeError):
        return []
    return boundaries


def build_caption_chunks(
    text: str,
    *,
    settings: CaptionTimingSettings,
) -> list[str]:
    normalized = normalize_caption_text(text)
    if not normalized:
        return []

    parts = [part.strip(" ,") for part in CLAUSE_SPLIT_RE.split(normalized) if part.strip(" ,")]
    if not parts:
        parts = [normalized]

    chunks: list[str] = []
    for part in parts:
        words = part.split()
        current_words: list[str] = []
        for word in words:
            tentative_words = current_words + [word]
            tentative_text = " ".join(tentative_words)
            if current_words and (
                len(tentative_words) > settings.max_words_per_chunk
                or len(tentative_text) > settings.max_chars_per_chunk
            ):
                chunks.append(" ".join(current_words))
                current_words = [word]
            else:
                current_words = tentative_words
        if current_words:
            chunks.append(" ".join(current_words))

    cleaned_chunks = [normalize_caption_text(chunk) for chunk in chunks if normalize_caption_text(chunk)]
    merged_chunks: list[str] = []
    for chunk in cleaned_chunks:
        if merged_chunks and _chunk_fits(f"{merged_chunks[-1]} {chunk}", settings=settings):
            previous_word_count = count_words(merged_chunks[-1])
            current_word_count = count_words(chunk)
            if previous_word_count <= 2 or current_word_count <= 2:
                merged_chunks[-1] = normalize_caption_text(f"{merged_chunks[-1]} {chunk}")
                continue
        merged_chunks.append(chunk)
    return merged_chunks or [normalized]


def build_timed_caption_cues(
    text: str,
    total_duration_seconds: float,
    *,
    settings: CaptionTimingSettings,
    word_boundaries: Sequence[CaptionWordBoundary] | None = None,
) -> tuple[list[CaptionCuePlan], str]:
    chunks = build_caption_chunks(text, settings=settings)
    if not chunks or total_duration_seconds <= 0:
        return [], "empty"

    normalized_boundaries = [boundary for boundary in (word_boundaries or []) if boundary.text.strip()]
    if len(normalized_boundaries) >= len(chunks):
        boundary_cues = _build_boundary_timed_cues(
            chunks,
            normalized_boundaries,
            total_duration_seconds=total_duration_seconds,
            settings=settings,
        )
        if boundary_cues:
            return boundary_cues, "word_boundaries"

    return _build_proportional_cues(chunks, total_duration_seconds=total_duration_seconds, settings=settings), "proportional_fallback"


def serialize_srt_cues(
    cues: Sequence[CaptionCuePlan],
    *,
    max_chars_per_line: int,
    max_lines: int,
) -> str:
    blocks: list[str] = []
    for cue in cues:
        wrapped_text = wrap_caption_lines(
            cue.text,
            max_chars_per_line=max_chars_per_line,
            max_lines=max_lines,
        )
        blocks.append(
            f"{cue.index}\n{_format_srt_timestamp(cue.start_seconds)} --> {_format_srt_timestamp(cue.end_seconds)}\n{wrapped_text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def wrap_caption_lines(
    text: str,
    *,
    max_chars_per_line: int,
    max_lines: int,
) -> str:
    normalized = normalize_caption_text(text)
    if not normalized:
        return ""
    if max_lines <= 1 or len(normalized) <= max_chars_per_line:
        return normalized

    words = normalized.split()
    best_break_index: int | None = None
    best_score: tuple[int, int] | None = None
    for break_index in range(1, len(words)):
        first_line = " ".join(words[:break_index])
        second_line = " ".join(words[break_index:])
        if len(first_line) > max_chars_per_line or len(second_line) > max_chars_per_line:
            continue
        score = (abs(len(first_line) - len(second_line)), max(len(first_line), len(second_line)))
        if best_score is None or score < best_score:
            best_score = score
            best_break_index = break_index

    if best_break_index is not None:
        return f"{' '.join(words[:best_break_index])}\n{' '.join(words[best_break_index:])}"

    lines: list[str] = []
    current_words: list[str] = []
    current_length = 0
    for word in words:
        projected_length = current_length + len(word) + (1 if current_words else 0)
        if current_words and projected_length > max_chars_per_line:
            lines.append(" ".join(current_words))
            current_words = [word]
            current_length = len(word)
        else:
            current_words.append(word)
            current_length = projected_length
    if current_words:
        lines.append(" ".join(current_words))

    if len(lines) <= max_lines:
        return "\n".join(lines)

    tail_words = " ".join(lines[max_lines - 1 :]).split()
    return "\n".join([*lines[: max_lines - 1], " ".join(tail_words)])


def extract_caption_emphasis_tokens(text: str) -> list[str]:
    return [match.group(0) for match in EMPHASIS_TOKEN_RE.finditer(normalize_caption_text(text))]


def select_caption_keywords(
    text: str,
    *,
    scene_keywords: Sequence[str] = (),
    pace_role: str = "explain",
    scene_type: str = "explain",
) -> list[CaptionKeywordCandidate]:
    keyword_set = {_normalize_token(keyword) for keyword in scene_keywords if _normalize_token(keyword)}
    candidates: list[CaptionKeywordCandidate] = []
    seen_tokens: set[str] = set()

    for position, token in enumerate(extract_caption_emphasis_tokens(text)):
        normalized_token = _normalize_token(token)
        if not normalized_token or normalized_token in seen_tokens:
            continue
        if normalized_token in WEAK_TOKENS or len(normalized_token) <= 2:
            continue
        score, reasons = _score_keyword_candidate(
            token=token,
            normalized_token=normalized_token,
            keyword_set=keyword_set,
            pace_role=pace_role,
            scene_type=scene_type,
        )
        if score <= 0:
            continue
        seen_tokens.add(normalized_token)
        candidates.append(
            CaptionKeywordCandidate(
                token=token,
                normalized_token=normalized_token,
                score=score,
                reasons=tuple(reasons),
                position=position,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.position))
    return candidates


def compute_caption_emphasis(
    text: str,
    *,
    scene_keywords: Sequence[str] = (),
    pace_role: str = "explain",
    scene_type: str = "explain",
) -> CaptionEmphasisDecision:
    candidates = select_caption_keywords(
        text,
        scene_keywords=scene_keywords,
        pace_role=pace_role,
        scene_type=scene_type,
    )
    if not candidates:
        return CaptionEmphasisDecision(tokens=(), skipped_reason="no_priority_tokens", candidates=())

    token_count = len(extract_caption_emphasis_tokens(text))
    if pace_role == "cta":
        max_tokens = 1
    elif token_count <= 4:
        max_tokens = 1
    elif token_count <= 8:
        max_tokens = 2
    else:
        max_tokens = 3

    selected = candidates[:max_tokens]
    selected.sort(key=lambda item: item.position)
    return CaptionEmphasisDecision(
        tokens=tuple(candidate.token for candidate in selected),
        skipped_reason=None,
        candidates=tuple(candidates),
    )


def _chunk_fits(text: str, *, settings: CaptionTimingSettings) -> bool:
    normalized = normalize_caption_text(text)
    return (
        count_words(normalized) <= settings.max_words_per_chunk
        and len(normalized) <= settings.max_chars_per_chunk
    )


def _build_boundary_timed_cues(
    chunks: Sequence[str],
    boundaries: Sequence[CaptionWordBoundary],
    *,
    total_duration_seconds: float,
    settings: CaptionTimingSettings,
) -> list[CaptionCuePlan]:
    if not chunks or not boundaries or len(boundaries) < len(chunks):
        return []

    word_counts = [max(1, count_words(chunk)) for chunk in chunks]
    boundary_counts = _allocate_boundary_counts(word_counts, len(boundaries))
    if sum(boundary_counts) != len(boundaries):
        return []

    draft_chunks: list[_BoundaryChunk] = []
    boundary_cursor = 0
    for chunk, boundary_count in zip(chunks, boundary_counts, strict=True):
        draft_chunks.append(
            _BoundaryChunk(
                text=chunk,
                word_count=max(1, count_words(chunk)),
                start_index=boundary_cursor,
                boundary_count=boundary_count,
            )
        )
        boundary_cursor += boundary_count

    draft_chunks = _merge_short_boundary_chunks(
        draft_chunks,
        boundaries=boundaries,
        total_duration_seconds=total_duration_seconds,
        settings=settings,
    )
    draft_chunks = _split_long_boundary_chunks(
        draft_chunks,
        boundaries=boundaries,
        total_duration_seconds=total_duration_seconds,
        settings=settings,
    )

    cues: list[CaptionCuePlan] = []
    for index, chunk in enumerate(draft_chunks):
        if index == 0:
            start_seconds = 0.0
        else:
            start_seconds = boundaries[chunk.start_index].start_seconds
        if index + 1 < len(draft_chunks):
            next_start_seconds = boundaries[draft_chunks[index + 1].start_index].start_seconds
            end_seconds = max(start_seconds + 0.05, next_start_seconds - settings.gap_seconds)
        else:
            end_seconds = total_duration_seconds
        end_seconds = min(total_duration_seconds, max(start_seconds + 0.05, end_seconds))
        cues.append(
            CaptionCuePlan(
                index=index + 1,
                text=normalize_caption_text(chunk.text),
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=max(0.05, end_seconds - start_seconds),
                word_count=max(1, count_words(chunk.text)),
                timing_source="word_boundaries",
            )
        )
    return cues


def _merge_short_boundary_chunks(
    chunks: list[_BoundaryChunk],
    *,
    boundaries: Sequence[CaptionWordBoundary],
    total_duration_seconds: float,
    settings: CaptionTimingSettings,
) -> list[_BoundaryChunk]:
    if len(chunks) <= 1:
        return chunks

    merged = list(chunks)
    index = 0
    while index < len(merged):
        current = merged[index]
        duration_seconds = _boundary_chunk_duration(
            current,
            next_chunk=merged[index + 1] if index + 1 < len(merged) else None,
            boundaries=boundaries,
            total_duration_seconds=total_duration_seconds,
            gap_seconds=settings.gap_seconds,
        )
        if duration_seconds >= settings.min_duration_seconds or len(merged) == 1:
            index += 1
            continue

        if index + 1 < len(merged):
            next_chunk = merged.pop(index + 1)
            merged[index] = _BoundaryChunk(
                text=normalize_caption_text(f"{current.text} {next_chunk.text}"),
                word_count=current.word_count + next_chunk.word_count,
                start_index=current.start_index,
                boundary_count=current.boundary_count + next_chunk.boundary_count,
            )
            continue

        if index > 0:
            previous = merged[index - 1]
            merged[index - 1] = _BoundaryChunk(
                text=normalize_caption_text(f"{previous.text} {current.text}"),
                word_count=previous.word_count + current.word_count,
                start_index=previous.start_index,
                boundary_count=previous.boundary_count + current.boundary_count,
            )
            merged.pop(index)
            index -= 1
            continue

        index += 1

    return merged


def _split_long_boundary_chunks(
    chunks: list[_BoundaryChunk],
    *,
    boundaries: Sequence[CaptionWordBoundary],
    total_duration_seconds: float,
    settings: CaptionTimingSettings,
) -> list[_BoundaryChunk]:
    split_chunks = list(chunks)
    index = 0
    while index < len(split_chunks):
        current = split_chunks[index]
        duration_seconds = _boundary_chunk_duration(
            current,
            next_chunk=split_chunks[index + 1] if index + 1 < len(split_chunks) else None,
            boundaries=boundaries,
            total_duration_seconds=total_duration_seconds,
            gap_seconds=settings.gap_seconds,
        )
        if (
            duration_seconds <= settings.max_duration_seconds
            or current.word_count <= 3
            or current.boundary_count <= 1
        ):
            index += 1
            continue

        left_text, right_text = _split_chunk_text(current.text)
        if not left_text or not right_text:
            index += 1
            continue

        left_word_count = max(1, count_words(left_text))
        right_word_count = max(1, count_words(right_text))
        split_boundary_count = max(
            1,
            min(
                current.boundary_count - 1,
                int(round(current.boundary_count * (left_word_count / max(1, current.word_count)))),
            ),
        )
        left_chunk = _BoundaryChunk(
            text=left_text,
            word_count=left_word_count,
            start_index=current.start_index,
            boundary_count=split_boundary_count,
        )
        right_chunk = _BoundaryChunk(
            text=right_text,
            word_count=right_word_count,
            start_index=current.start_index + split_boundary_count,
            boundary_count=current.boundary_count - split_boundary_count,
        )
        split_chunks[index : index + 1] = [left_chunk, right_chunk]
    return split_chunks


def _split_chunk_text(text: str) -> tuple[str, str]:
    words = normalize_caption_text(text).split()
    if len(words) <= 3:
        return "", ""

    best_index: int | None = None
    best_score: tuple[int, int] | None = None
    for break_index in range(1, len(words)):
        left_words = words[:break_index]
        right_words = words[break_index:]
        score = (abs(len(left_words) - len(right_words)), abs(len(" ".join(left_words)) - len(" ".join(right_words))))
        if best_score is None or score < best_score:
            best_score = score
            best_index = break_index
    if best_index is None:
        return "", ""
    return " ".join(words[:best_index]), " ".join(words[best_index:])


def _boundary_chunk_duration(
    chunk: _BoundaryChunk,
    *,
    next_chunk: _BoundaryChunk | None,
    boundaries: Sequence[CaptionWordBoundary],
    total_duration_seconds: float,
    gap_seconds: float,
) -> float:
    start_seconds = 0.0 if chunk.start_index == 0 else boundaries[chunk.start_index].start_seconds
    if next_chunk is None:
        end_seconds = total_duration_seconds
    else:
        end_seconds = max(start_seconds + 0.05, boundaries[next_chunk.start_index].start_seconds - gap_seconds)
    return max(0.05, end_seconds - start_seconds)


def _build_proportional_cues(
    chunks: Sequence[str],
    *,
    total_duration_seconds: float,
    settings: CaptionTimingSettings,
) -> list[CaptionCuePlan]:
    if not chunks:
        return []

    gap_total_seconds = settings.gap_seconds * max(0, len(chunks) - 1)
    available_duration_seconds = max(0.1, total_duration_seconds - gap_total_seconds)
    weights = [max(1, count_words(chunk)) for chunk in chunks]
    total_weight = sum(weights)
    cue_durations = [available_duration_seconds * (weight / total_weight) for weight in weights]
    cue_durations = [
        max(settings.min_duration_seconds, min(settings.max_duration_seconds, duration))
        for duration in cue_durations
    ]

    difference = available_duration_seconds - sum(cue_durations)
    for _ in range(12):
        if abs(difference) <= 0.001:
            break
        adjustable_indexes = [
            index
            for index, duration in enumerate(cue_durations)
            if (difference > 0 and duration < settings.max_duration_seconds - 0.001)
            or (difference < 0 and duration > settings.min_duration_seconds + 0.001)
        ]
        if not adjustable_indexes:
            break
        adjustable_weight_total = sum(weights[index] for index in adjustable_indexes)
        for index in adjustable_indexes:
            weight_share = weights[index] / max(1, adjustable_weight_total)
            cue_durations[index] = max(
                settings.min_duration_seconds,
                min(settings.max_duration_seconds, cue_durations[index] + (difference * weight_share)),
            )
        difference = available_duration_seconds - sum(cue_durations)

    if cue_durations:
        cue_durations[-1] += available_duration_seconds - sum(cue_durations)

    cues: list[CaptionCuePlan] = []
    elapsed_seconds = 0.0
    for index, (chunk, duration_seconds) in enumerate(zip(chunks, cue_durations, strict=True), start=1):
        start_seconds = elapsed_seconds
        end_seconds = min(total_duration_seconds, start_seconds + duration_seconds)
        if index == len(chunks):
            end_seconds = total_duration_seconds
        cues.append(
            CaptionCuePlan(
                index=index,
                text=normalize_caption_text(chunk),
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                duration_seconds=max(0.05, end_seconds - start_seconds),
                word_count=max(1, count_words(chunk)),
                timing_source="proportional_fallback",
            )
        )
        elapsed_seconds = min(total_duration_seconds, end_seconds + settings.gap_seconds)
    return cues


def _allocate_boundary_counts(word_counts: Sequence[int], boundary_count: int) -> list[int]:
    remaining_word_total = max(1, sum(word_counts))
    remaining_boundary_count = boundary_count
    allocation: list[int] = []
    for index, word_count in enumerate(word_counts):
        remaining_chunks = len(word_counts) - index
        if index == len(word_counts) - 1:
            count = remaining_boundary_count
        else:
            proportional_count = int(round(remaining_boundary_count * (word_count / max(1, remaining_word_total))))
            count = max(1, min(proportional_count, remaining_boundary_count - (remaining_chunks - 1)))
        allocation.append(count)
        remaining_boundary_count -= count
        remaining_word_total -= word_count
    return allocation


def _score_keyword_candidate(
    *,
    token: str,
    normalized_token: str,
    keyword_set: set[str],
    pace_role: str,
    scene_type: str,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if NUMBER_TOKEN_RE.match(normalized_token) or NUMBER_PHRASE_RE.match(normalized_token):
        score += 140
        reasons.append("numeric")
    if normalized_token in TOOL_TOKENS:
        score += 110
        reasons.append("tool")
    is_result_token = normalized_token in RESULT_TOKENS
    if normalized_token in PROOF_TOKENS and not is_result_token:
        score += 64
        reasons.append("proof")
    if is_result_token:
        score += 44
        reasons.append("result")
    if normalized_token in keyword_set:
        score += 24
        reasons.append("scene_keyword")
    if pace_role == "hook" and normalized_token in OPPOSITION_TOKENS:
        score += 40
        reasons.append("hook_contrast")
    if pace_role == "cta" and normalized_token in ACTION_TOKENS:
        score += 78
        reasons.append("cta_action")
    if scene_type == "proof" and normalized_token in {"capture", "captures", "dashboard", "screen", "screenshot"}:
        score += 28
        reasons.append("proof_concrete")
    if len(normalized_token) >= 7 and normalized_token not in WEAK_TOKENS:
        score += 12
        reasons.append("concrete_length")

    if score <= 0:
        return 0, []
    return score, reasons


def _normalize_token(value: str) -> str:
    lowered = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return lowered.strip(".,!?;:()[]{}\"'")


def _format_srt_timestamp(value: float) -> str:
    total_milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


__all__ = [
    "CaptionCuePlan",
    "CaptionEmphasisDecision",
    "CaptionKeywordCandidate",
    "CaptionRenderSettings",
    "CaptionTimingSettings",
    "CaptionWordBoundary",
    "DEFAULT_CAPTION_FONT_NAME",
    "DEFAULT_CAPTION_GAP_MS",
    "DEFAULT_CAPTION_HIGHLIGHT_COLOR",
    "DEFAULT_CAPTION_MAX_CHARS_PER_LINE",
    "DEFAULT_CAPTION_MAX_DURATION_MS",
    "DEFAULT_CAPTION_MAX_LINES",
    "DEFAULT_CAPTION_MAX_WORDS_PER_CHUNK",
    "DEFAULT_CAPTION_MIN_DURATION_MS",
    "DEFAULT_CAPTION_SAFE_BOTTOM_MARGIN",
    "DEFAULT_CAPTION_SAFE_SIDE_MARGIN",
    "DEFAULT_FALLBACK_SUBTITLE_FONT_SIZE",
    "build_caption_chunks",
    "build_timed_caption_cues",
    "compute_caption_emphasis",
    "count_words",
    "extract_caption_emphasis_tokens",
    "normalize_caption_text",
    "parse_edge_tts_word_boundaries",
    "select_caption_keywords",
    "serialize_srt_cues",
    "wrap_caption_lines",
]
