from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
from typing import Mapping

from pipeline.diversity_memory import DiversityCandidate, DiversityDecision, load_diversity_memory
from schemas import JSONValue, VideoJob

LOGGER = logging.getLogger(__name__)

SCRIPT_FILENAME = "script.txt"
DEFAULT_POINT_COUNT = 3
MIN_POINT_COUNT = 2
MAX_POINT_COUNT = 3
DEFAULT_CTA_MODE = "soft"
DEFAULT_PROOF_MODE = "auto"
DEFAULT_PLATFORM_LABEL = "TikTok"
DEFAULT_AUDIENCE_LABEL = "les createurs"
WORD_RE = re.compile(r"\b[\w'-]+\b", flags=re.UNICODE)
NUMBER_RE = re.compile(r"\b\d+\b")
STOP_WORDS = {
    "a",
    "au",
    "aux",
    "ce",
    "ces",
    "dans",
    "de",
    "des",
    "du",
    "en",
    "et",
    "for",
    "how",
    "into",
    "l",
    "la",
    "le",
    "les",
    "one",
    "pour",
    "sur",
    "the",
    "to",
    "ton",
    "ta",
    "tes",
    "that",
    "under",
    "une",
    "video",
    "videos",
    "your",
}
PROFILE_KEYWORDS = (
    ("subtitles", ("subtitle", "subtitles", "caption", "captions", "srt")),
    ("hooks", ("hook", "hooks", "scroll", "retention", "watch", "intro", "opening")),
    ("repurpose", ("idea", "ideas", "videos", "repurpose", "content", "clips")),
    ("automation", ("automation", "automatisation", "ai", "ia", "workflow", "prompt", "pipeline")),
)
TOOL_HINTS = (
    ("google sheets", "Google Sheets"),
    ("make.com", "Make"),
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
    ("premiere", "Premiere Pro"),
    ("chatgpt", "ChatGPT"),
    ("openai", "OpenAI"),
    ("claude", "Claude"),
    ("gmail", "Gmail"),
    ("excel", "Excel"),
)
PROOF_MODE_ALIASES = {
    "auto": "auto",
    "example": "example",
    "exemple": "example",
    "real_example": "example",
    "case": "example",
    "cas": "example",
    "tool": "tool",
    "outil": "tool",
    "result": "result",
    "results": "result",
    "metric": "result",
    "outcome": "result",
    "mesurable": "result",
    "field_error": "field_error",
    "terrain": "field_error",
    "error": "field_error",
    "erreur": "field_error",
}
ERROR_SIGNAL_KEYWORDS = (
    "mistake",
    "mistakes",
    "error",
    "errors",
    "erreur",
    "erreurs",
    "fail",
    "fails",
    "failed",
    "failure",
    "problem",
    "problems",
    "kill",
)
EXAMPLE_SIGNAL_KEYWORDS = ("example", "exemple", "case", "cas", "client")
RESULT_SIGNAL_KEYWORDS = (
    "second",
    "seconds",
    "seconde",
    "secondes",
    "minute",
    "minutes",
    "percent",
    "pourcent",
    "%",
)
DEFAULT_PROOF_MODE_BY_PROFILE = {
    "hooks": "result",
    "subtitles": "field_error",
    "repurpose": "example",
    "automation": "tool",
    "generic": "example",
}
PROOF_TONE_BY_MODE = {
    "example": "narrative",
    "tool": "operational",
    "result": "measurable",
    "field_error": "direct",
}
PROFILE_DEFAULT_TOOLS = {
    "hooks": "TikTok Analytics",
    "subtitles": "CapCut",
    "repurpose": "Notion",
    "automation": "Zapier",
    "generic": "Notion",
}
PROOF_HOOK_BRIDGES = {
    "example": (
        "La preuve la plus rapide, c'est un cas reel que l'on visualise tout de suite.",
        "Le plus credible, c'est le moment ou tu montres un exemple que l'on peut presque voir.",
    ),
    "tool": (
        "Le plus concret, c'est de nommer l'outil exact au lieu de rester abstrait.",
        "Un seul outil cite au bon moment rend deja le script plus credible.",
    ),
    "result": (
        "Le plus convaincant, c'est un resultat mesurable compris en une seconde.",
        "Un chiffre simple donne tout de suite du poids a la promesse.",
    ),
    "field_error": (
        "Le plus credible, c'est l'erreur terrain que tout le monde reconnait en vrai.",
        "La theorie marque moins qu'une erreur terrain qu'on a deja subie.",
    ),
}
PROOF_POINT_LIBRARY = {
    "example": (
        "Exemple reel : {proof_example}.",
        "Cas reel : {proof_example}.",
    ),
    "tool": (
        "Outil concret : {proof_tool}. C'est ce genre de detail qui fait sortir le script de la theorie.",
        "Nomme {proof_tool} dans la video et le niveau de concret monte tout de suite.",
    ),
    "result": (
        "Resultat mesurable : {proof_result}.",
        "Chiffre concret : {proof_result}.",
    ),
    "field_error": (
        "Erreur terrain : {proof_error}.",
        "Erreur qu'on retrouve en vrai : {proof_error}.",
    ),
}
PROOF_CTA_LIBRARY = {
    "example": {
        "soft": (
            "Si tu veux, je peux te sortir 3 autres exemples reels sur {topic_reference}.",
            "Je peux te proposer d'autres cas reels pour rendre {topic_reference} encore plus concret.",
        ),
        "subscribe": (
            "Abonne-toi, je te montre d'autres exemples reels comme celui-ci.",
            "Abonne-toi, je detaille les cas reels que j'utiliserais sur {topic_reference}.",
        ),
        "comment": (
            "Commente exemple et je te donne 3 cas reels supplementaires.",
            "Commente cas reel et je te renvoie d'autres exemples tres concrets.",
        ),
    },
    "tool": {
        "soft": (
            "Si tu veux, je peux te donner le stack exact autour de {proof_tool}.",
            "Je peux te sortir la version precise du workflow avec {proof_tool}.",
        ),
        "subscribe": (
            "Abonne-toi, je te montre les outils exacts que j'utiliserais ici.",
            "Abonne-toi, je detaille le stack reel derriere ce type de script.",
        ),
        "comment": (
            "Commente outil et je te donne le stack complet.",
            "Commente stack et je te renvoie l'ordre exact des outils.",
        ),
    },
    "result": {
        "soft": (
            "Si tu veux, je peux te sortir d'autres resultats mesurables sur {topic_reference}.",
            "Je peux te proposer d'autres chiffres concrets a mettre dans ce script.",
        ),
        "subscribe": (
            "Abonne-toi, je te montre quels chiffres garder pour rendre le script credible.",
            "Abonne-toi, je detaille les resultats mesurables que je mettrais en avant.",
        ),
        "comment": (
            "Commente chiffre et je te donne d'autres resultats mesurables.",
            "Commente resultat et je te renvoie des preuves plus chiffrees.",
        ),
    },
    "field_error": {
        "soft": (
            "Si tu veux, je peux te lister 3 autres erreurs terrain sur {topic_reference}.",
            "Je peux te donner les erreurs terrain qui reviennent le plus souvent ici.",
        ),
        "subscribe": (
            "Abonne-toi, je te montre les erreurs terrain a eliminer en premier.",
            "Abonne-toi, je detaille les erreurs qui flinguent ce type de video.",
        ),
        "comment": (
            "Commente erreur et je te donne d'autres erreurs terrain concretes.",
            "Commente terrain et je te renvoie la checklist des erreurs a enlever.",
        ),
    },
}
PROFILE_LIBRARY = {
    "hooks": {
        "hooks": (
            "Ton hook ne doit pas presenter le sujet. Il doit vendre la deuxieme seconde.",
            "Sur {platform_label}, le probleme n'est souvent pas le sujet. C'est la premiere phrase.",
            "Si la retention casse trop vite, le souci n'est pas le montage. C'est l'ouverture.",
        ),
        "points": (
            "La premiere ligne doit promettre un resultat, pas annoncer un theme.",
            "Un hook credible donne un angle precis : conflit, delai ou benefice concret.",
            "Refais {test_count} versions et garde seulement celle qu'on comprend en moins d'une seconde.",
        ),
        "ctas": {
            "soft": (
                "Si tu veux, je peux te sortir 10 hooks plus credibles dans cette niche.",
                "Je peux te montrer le template exact de hook que j'utiliserais sur ce sujet.",
            ),
            "subscribe": (
                "Abonne-toi, je te montre comment j'ecris les hooks qui ouvrent une boucle nette.",
                "Abonne-toi, je detaille le systeme de hook que j'utiliserais sur ce sujet.",
            ),
            "comment": (
                "Commente hook et je te donne une version plus agressive pour ce sujet.",
                "Commente ouverture et je te renvoie une version plus forte de ce hook.",
            ),
        },
    },
    "subtitles": {
        "hooks": (
            "Tes sous-titres peuvent etre propres et quand meme tuer la retention.",
            "Le souci ne vient pas seulement du style. Il vient du rythme.",
            "Sur {platform_label}, des sous-titres lisibles ne suffisent pas. Il faut qu'ils respirent.",
        ),
        "points": (
            "Si on doit finir la ligne pour comprendre l'idee, tu es deja trop lent.",
            "Une phrase = une idee, avec un mot fort qui ressort tout de suite a l'ecran.",
            "Coupe plus court, change de bloc plus souvent et retire tout ce qui n'apporte pas d'image mentale.",
        ),
        "ctas": {
            "soft": (
                "Si tu veux, je peux te montrer la structure exacte de sous-titres que j'utiliserais ici.",
                "Je peux te sortir un preset de sous-titres pense pour la retention sur {platform_label}.",
            ),
            "subscribe": (
                "Abonne-toi, je te montre le preset de sous-titres que j'utiliserais pour cette niche.",
                "Abonne-toi, je detaille ma structure de sous-titres pour retenir plus longtemps.",
            ),
            "comment": (
                "Commente sous-titres et je te donne une version plus nerveuse.",
                "Commente retention et je te montre comment je couperais ces sous-titres.",
            ),
        },
    },
    "repurpose": {
        "hooks": (
            "Une idee ne donne pas une video. Elle donne plusieurs angles si tu changes la promesse.",
            "Le vrai levier n'est pas l'idee seule. C'est la variation d'angle.",
            "Le plus gros manque, c'est souvent l'angle, pas l'idee.",
        ),
        "points": (
            "Une version pour l'erreur, une pour la methode, une pour l'exemple concret.",
            "Le sujet reste le meme, mais la tension editoriale change a chaque video.",
            "Avant d'ecrire, liste {test_count} promesses distinctes puis tourne seulement les plus nettes.",
        ),
        "ctas": {
            "soft": (
                "Si tu veux, je peux te sortir le decoupage exact en {test_count} angles pour ce sujet.",
                "Je peux te montrer comment je transformerais cette idee en serie de videos.",
            ),
            "subscribe": (
                "Abonne-toi, je te montre comment je transforme une idee en mini serie credible.",
                "Abonne-toi, je detaille le systeme qui fait sortir plusieurs angles d'un meme sujet.",
            ),
            "comment": (
                "Commente angles et je te donne {test_count} variations plus precises.",
                "Commente serie et je te propose un decoupage concret pour ce sujet.",
            ),
        },
    },
    "automation": {
        "hooks": (
            "En automatisation IA, on voit tout de suite quand le rendu sonne trop automate.",
            "Le rendu fait amateur quand chaque etape sonne generique.",
            "Le vrai probleme, ce n'est pas l'outil. C'est l'intention editoriale.",
        ),
        "points": (
            "Ce qui fait pro, ce n'est pas l'outil. C'est la clarte editoriale a chaque etape.",
            "Script, voix, visuel et sous-titres doivent vendre la meme promesse, au meme rythme.",
            "Si une seule couche sonne template, le spectateur sent le pipeline avant de sentir l'idee.",
        ),
        "ctas": {
            "soft": (
                "Si tu veux, je peux te sortir le workflow complet pour rendre ce pipeline plus credible.",
                "Je peux te montrer la version du pipeline pensee pour la retention, pas juste pour la prod.",
            ),
            "subscribe": (
                "Abonne-toi, je detaille le workflow qui rend un pipeline plus credible sur {platform_label}.",
                "Abonne-toi, je te montre comment faire passer un pipeline de correct a credible.",
            ),
            "comment": (
                "Commente pipeline et je te donne la checklist de rendu que j'utiliserais.",
                "Commente credibilite et je te renvoie le workflow exact que je corrigerais.",
            ),
        },
    },
    "generic": {
        "hooks": (
            "Le rendu fait amateur quand la promesse reste floue des la premiere seconde.",
            "Sur {platform_label}, on perd rarement a cause du sujet. On perd a cause du cadrage de l'idee.",
            "Si ca ne perce pas, regarde d'abord la promesse, pas les effets.",
        ),
        "points": (
            "Chaque phrase doit pousser la suivante au lieu de simplement empiler de l'information.",
            "Plus c'est concret, plus la video parait credible vite.",
            "Garde une idee forte par bloc et retire tout ce qui ne change pas la decision du spectateur.",
        ),
        "ctas": {
            "soft": (
                "Si tu veux, je peux te montrer comment je resserrerais ce script pour {platform_label}.",
                "Je peux te sortir une version encore plus concise et plus forte de ce sujet.",
            ),
            "subscribe": (
                "Abonne-toi, je detaille comment je rends ces scripts plus credibles sur {platform_label}.",
                "Abonne-toi, je te montre la version plus nerveuse de ce script.",
            ),
            "comment": (
                "Commente script et je te donne une version plus directe.",
                "Commente angle et je te propose une ouverture plus forte.",
            ),
        },
    },
}
PROFILE_HOOK_FAMILIES = {
    "hooks": ("sell_second", "problem_first", "retention_break"),
    "subtitles": ("retention_loss", "style_vs_rhythm", "readability_check"),
    "repurpose": ("multi_angle", "angle_shift", "idea_vs_promise"),
    "automation": ("automation_tone", "generic_steps", "editorial_intent"),
    "generic": ("promise_blur", "idea_framing", "promise_before_fx"),
}
PROOF_HOOK_BRIDGE_FAMILIES = {
    "example": ("case_visual", "case_moment"),
    "tool": ("name_the_tool", "tool_specificity"),
    "result": ("fast_metric", "simple_number"),
    "field_error": ("field_pattern", "pain_memory"),
}
BODY_STRUCTURE_OPTIONS = {
    2: (
        {"name": "lead_proof", "order": (0, "proof")},
        {"name": "proof_close", "order": ("proof", -1)},
    ),
    3: (
        {"name": "lead_proof_close", "order": (0, "proof", -1)},
        {"name": "lead_mid_proof", "order": (0, 1, "proof")},
        {"name": "proof_mid_close", "order": ("proof", 1, -1)},
    ),
}


class ScriptGenerationError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class TopicProfile:
    label: str
    topic: str
    topic_reference: str
    topic_handle: str
    keywords: list[str]
    keyword_phrase: str
    number_token: str | None
    platform_label: str
    audience_label: str


@dataclass(slots=True, frozen=True)
class ProfileResolution:
    label: str
    source: str
    reason: str
    locked: bool
    niche: str | None
    matched_rule_index: int | None
    analyzed_candidates: list[str]

    def to_metadata(self) -> dict[str, JSONValue]:
        payload: dict[str, JSONValue] = {
            "label": self.label,
            "source": self.source,
            "reason": self.reason,
            "locked": self.locked,
            "analyzed_candidates": list(self.analyzed_candidates),
        }
        if self.niche is not None:
            payload["niche"] = self.niche
        if self.matched_rule_index is not None:
            payload["matched_rule_index"] = self.matched_rule_index
        return payload


@dataclass(slots=True, frozen=True)
class EditorialTopicRuleMatch:
    label: str
    rule_index: int
    reason: str


@dataclass(slots=True, frozen=True)
class ScriptSections:
    hook: str
    points: list[str]
    cta: str


@dataclass(slots=True, frozen=True)
class ProofPayload:
    mode: str
    tone: str
    detail: str
    point: str


@dataclass(slots=True, frozen=True)
class HookCandidate:
    key: str
    text: str
    family: str
    signature: str


@dataclass(slots=True, frozen=True)
class CtaCandidate:
    key: str
    text: str
    family: str
    signature: str
    mode: str


async def generate_script_step(job: VideoJob, job_dir: Path) -> None:
    topic = _normalize_topic(job.topic)
    if not topic:
        raise ScriptGenerationError(f"job {job.job_id} has an empty topic")

    point_count = _resolve_point_count(job)
    memory = load_diversity_memory(job)
    analyzed_profile = _analyze_topic(job, topic)
    profile, profile_decision, profile_resolution = _select_profile(job, analyzed_profile, memory)
    seed = _build_script_seed(job, profile)
    context = _build_script_context(profile)
    proof, proof_decision = _build_proof_payload(
        job,
        profile,
        context=context,
        seed=seed,
        memory=memory,
    )
    hook_candidate, hook_decision = _select_hook_candidate(
        profile,
        context=context,
        proof=proof,
        seed=seed,
        memory=memory,
    )
    cta_candidate, cta_decision = _select_cta_candidate(
        job,
        PROFILE_LIBRARY[profile.label]["ctas"],
        proof=proof,
        seed=seed,
        context=context,
        memory=memory,
    )
    body_structure, body_decision = _select_body_structure(
        job,
        profile=profile,
        proof=proof,
        hook_candidate=hook_candidate,
        cta_candidate=cta_candidate,
        point_count=point_count,
        memory=memory,
    )
    sections = _build_script_sections(
        profile,
        proof=proof,
        hook_candidate=hook_candidate,
        cta_candidate=cta_candidate,
        body_structure=body_structure,
    )
    script_text = _render_script(sections)
    script_path = job_dir / SCRIPT_FILENAME
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_text + "\n", encoding="utf-8")

    word_count = _count_words(script_text)
    estimated_duration_seconds = _estimate_duration_seconds(word_count)

    job.set_script_text(script_text)
    job.set_script_path(str(script_path.resolve()))
    job.set_metadata("script_backend", "local-template-v4-memory")
    job.set_metadata("script_profile", profile.label)
    job.set_metadata("script_profile_locked", profile_resolution.locked)
    job.set_metadata("script_profile_source", profile_resolution.source)
    job.set_metadata("script_profile_resolution", profile_resolution.to_metadata())
    job.set_metadata("script_keywords", list(profile.keywords))
    job.set_metadata("script_platform_label", profile.platform_label)
    job.set_metadata("script_proof_mode", proof.mode)
    job.set_metadata("script_proof_tone", proof.tone)
    job.set_metadata("script_proof_detail", proof.detail)
    job.set_metadata("script_proof_line", proof.point)
    job.set_metadata("script_hook_family", hook_candidate.family)
    job.set_metadata("script_hook_signature", hook_candidate.signature)
    job.set_metadata("script_hook_text", sections.hook)
    job.set_metadata("script_cta_mode", cta_candidate.mode)
    job.set_metadata("script_cta_family", cta_candidate.family)
    job.set_metadata("script_cta_signature", cta_candidate.signature)
    job.set_metadata("script_cta_text", sections.cta)
    job.set_metadata("script_body_structure", body_structure)
    job.set_metadata(
        "script_signature",
        _build_script_signature(
            profile.label,
            proof.mode,
            hook_candidate.family,
            cta_candidate.family,
            body_structure,
        ),
    )
    job.set_metadata(
        "script_sections",
        {
            "hook": sections.hook,
            "points": list(sections.points),
            "cta": sections.cta,
        },
    )
    job.set_metadata(
        "anti_repetition_script",
        {
            "config": memory.config.to_metadata(),
            "window_job_ids": memory.window_job_ids,
            "decisions": {
                "profile": profile_decision.to_metadata(),
                "proof_mode": proof_decision.to_metadata(),
                "hook": hook_decision.to_metadata(),
                "cta": cta_decision.to_metadata(),
                "body_structure": body_decision.to_metadata(),
            },
            "profile_lock": profile_resolution.to_metadata(),
        },
    )
    job.set_metadata("script_point_count", len(sections.points))
    job.set_metadata("script_word_count", word_count)
    job.set_metadata("estimated_duration_seconds", estimated_duration_seconds)

    LOGGER.info(
        "job=%s script_gen finished output=%s words=%s points=%s profile=%s proof=%s hook=%s cta=%s body=%s",
        job.job_id,
        script_path,
        word_count,
        len(sections.points),
        profile.label,
        proof.mode,
        hook_candidate.family,
        cta_candidate.family,
        body_structure,
    )


def _normalize_topic(topic: str) -> str:
    normalized_topic = " ".join(topic.split()).strip()
    return normalized_topic.rstrip(" .,!?:;")


def _resolve_point_count(job: VideoJob) -> int:
    raw_value = job.metadata.get("script_point_count", DEFAULT_POINT_COUNT)
    if isinstance(raw_value, bool):
        return DEFAULT_POINT_COUNT
    if isinstance(raw_value, int):
        return max(MIN_POINT_COUNT, min(MAX_POINT_COUNT, raw_value))
    if isinstance(raw_value, float):
        return max(MIN_POINT_COUNT, min(MAX_POINT_COUNT, int(round(raw_value))))
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = int(raw_value.strip())
        except ValueError:
            return DEFAULT_POINT_COUNT
        return max(MIN_POINT_COUNT, min(MAX_POINT_COUNT, parsed))
    return DEFAULT_POINT_COUNT


def _build_script_seed(job: VideoJob, profile: TopicProfile) -> bytes:
    return hashlib.sha256(f"{profile.topic}|{job.template}|{job.account}".encode("utf-8")).digest()


def _build_script_context(profile: TopicProfile) -> dict[str, str]:
    return {
        "topic": profile.topic,
        "topic_reference": profile.topic_reference,
        "topic_handle": profile.topic_handle,
        "keywords": profile.keyword_phrase,
        "platform_label": profile.platform_label,
        "audience_label": profile.audience_label,
        "test_count": profile.number_token or "3",
    }


def _analyze_topic(job: VideoJob, topic: str) -> TopicProfile:
    normalized_topic = _normalize_topic(topic)
    ranked_labels = _rank_profile_labels(normalized_topic)
    return _build_topic_profile(job, normalized_topic, ranked_labels[0])


def _build_topic_profile(job: VideoJob, topic: str, label: str) -> TopicProfile:
    normalized_topic = _normalize_topic(topic)
    keywords = _extract_keywords(normalized_topic)
    number_match = NUMBER_RE.search(normalized_topic)
    platform_label = _resolve_platform_label(job)
    audience_label = _resolve_audience_label(job)
    return TopicProfile(
        label=label,
        topic=normalized_topic,
        topic_reference=_to_topic_reference(normalized_topic),
        topic_handle=_build_topic_handle(label, keywords),
        keywords=keywords,
        keyword_phrase=" / ".join(keywords[:3]) if keywords else normalized_topic,
        number_token=number_match.group(0) if number_match else None,
        platform_label=platform_label,
        audience_label=audience_label,
    )


def _rank_profile_labels(topic: str) -> list[str]:
    lowered_topic = topic.lower()
    scored_labels = [
        (
            candidate_label,
            sum(keyword in lowered_topic for keyword in candidate_keywords),
        )
        for candidate_label, candidate_keywords in PROFILE_KEYWORDS
    ]
    ordered_labels = [
        label
        for label, score in sorted(scored_labels, key=lambda item: (-item[1], item[0]))
        if score > 0
    ]
    if "generic" not in ordered_labels:
        ordered_labels.append("generic")
    return ordered_labels


def _select_profile(
    job: VideoJob,
    analyzed_profile: TopicProfile,
    memory,
) -> tuple[TopicProfile, DiversityDecision, ProfileResolution]:
    if memory.config.lock_script_profile:
        resolution = _resolve_locked_profile(job, analyzed_profile)
        decision = memory.choose(
            "script_profile",
            [
                DiversityCandidate(
                    key=resolution.label,
                    signals={"script_profile": resolution.label},
                    base_rank=0,
                )
            ],
        )
        return _build_topic_profile(job, analyzed_profile.topic, resolution.label), decision, resolution

    explicit_profile = _read_profile_label(job.metadata.get("script_profile"))
    if explicit_profile is not None:
        resolution = _build_profile_resolution(
            label=explicit_profile,
            source="script_profile",
            reason="metadata.script_profile",
            locked=True,
            analyzed_profile=analyzed_profile,
            niche=_read_optional_metadata_string(job, "niche"),
        )
        decision = memory.choose(
            "script_profile",
            [
                DiversityCandidate(
                    key=explicit_profile,
                    signals={"script_profile": explicit_profile},
                    base_rank=0,
                )
            ],
        )
        return _build_topic_profile(job, analyzed_profile.topic, explicit_profile), decision, resolution

    editorial_profile = _read_profile_label(job.metadata.get("editorial_profile"))
    if editorial_profile is not None:
        resolution = _build_profile_resolution(
            label=editorial_profile,
            source="editorial_profile",
            reason="metadata.editorial_profile",
            locked=True,
            analyzed_profile=analyzed_profile,
            niche=_read_optional_metadata_string(job, "niche"),
        )
        decision = memory.choose(
            "script_profile",
            [
                DiversityCandidate(
                    key=editorial_profile,
                    signals={"script_profile": editorial_profile},
                    base_rank=0,
                )
            ],
        )
        return _build_topic_profile(job, analyzed_profile.topic, editorial_profile), decision, resolution

    candidates = _rank_profile_labels(analyzed_profile.topic)
    if analyzed_profile.label not in candidates:
        candidates.insert(0, analyzed_profile.label)

    decision = memory.choose(
        "script_profile",
        [
            DiversityCandidate(
                key=label,
                signals={"script_profile": label},
                base_rank=index,
            )
            for index, label in enumerate(candidates)
        ],
    )
    resolution = _build_profile_resolution(
        label=decision.selected_key,
        source="anti_repetition_memory",
        reason="legacy multi-candidate profile selection",
        locked=False,
        analyzed_profile=analyzed_profile,
        niche=_read_optional_metadata_string(job, "niche"),
    )
    return _build_topic_profile(job, analyzed_profile.topic, decision.selected_key), decision, resolution


def _resolve_locked_profile(job: VideoJob, analyzed_profile: TopicProfile) -> ProfileResolution:
    niche = _read_optional_metadata_string(job, "niche")
    explicit_profile = _read_profile_label(job.metadata.get("script_profile"))
    if explicit_profile is not None:
        return _build_profile_resolution(
            label=explicit_profile,
            source="script_profile",
            reason="metadata.script_profile",
            locked=True,
            analyzed_profile=analyzed_profile,
            niche=niche,
        )

    editorial_profile = _read_profile_label(job.metadata.get("editorial_profile"))
    if editorial_profile is not None:
        return _build_profile_resolution(
            label=editorial_profile,
            source="editorial_profile",
            reason="metadata.editorial_profile",
            locked=True,
            analyzed_profile=analyzed_profile,
            niche=niche,
        )

    editorial_map = job.metadata.get("editorial_profile_map")
    if isinstance(editorial_map, Mapping):
        topic_match = _match_editorial_topic_rule(editorial_map.get("topic_rules"), analyzed_profile.topic)
        if topic_match is not None:
            return _build_profile_resolution(
                label=topic_match.label,
                source="editorial_profile_map.topic_rules",
                reason=topic_match.reason,
                locked=True,
                analyzed_profile=analyzed_profile,
                niche=niche,
                matched_rule_index=topic_match.rule_index,
            )

        niche_profile = _resolve_editorial_niche_profile(editorial_map.get("by_niche"), niche)
        if niche_profile is not None:
            return _build_profile_resolution(
                label=niche_profile,
                source="editorial_profile_map.by_niche",
                reason=f"niche:{niche}",
                locked=True,
                analyzed_profile=analyzed_profile,
                niche=niche,
            )

        default_profile = _read_profile_label(editorial_map.get("default_profile"))
        if default_profile is not None:
            return _build_profile_resolution(
                label=default_profile,
                source="editorial_profile_map.default_profile",
                reason="editorial map fallback",
                locked=True,
                analyzed_profile=analyzed_profile,
                niche=niche,
            )

    return _build_profile_resolution(
        label=analyzed_profile.label,
        source="topic_analysis",
        reason="profile inferred from topic keywords",
        locked=True,
        analyzed_profile=analyzed_profile,
        niche=niche,
    )


def _build_profile_resolution(
    *,
    label: str,
    source: str,
    reason: str,
    locked: bool,
    analyzed_profile: TopicProfile,
    niche: str | None,
    matched_rule_index: int | None = None,
) -> ProfileResolution:
    analyzed_candidates = _rank_profile_labels(analyzed_profile.topic)
    if analyzed_profile.label not in analyzed_candidates:
        analyzed_candidates.insert(0, analyzed_profile.label)
    if label not in analyzed_candidates:
        analyzed_candidates.insert(0, label)
    return ProfileResolution(
        label=label,
        source=source,
        reason=reason,
        locked=locked,
        niche=niche,
        matched_rule_index=matched_rule_index,
        analyzed_candidates=analyzed_candidates,
    )


def _match_editorial_topic_rule(raw_rules: object, topic: str) -> EditorialTopicRuleMatch | None:
    if not isinstance(raw_rules, list):
        return None

    lowered_topic = topic.lower()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, Mapping):
            continue
        label = _read_profile_label(raw_rule.get("profile"))
        if label is None:
            continue

        contains_any = _read_string_list(raw_rule.get("contains_any"))
        contains_all = _read_string_list(raw_rule.get("contains_all"))
        if not contains_any and not contains_all:
            continue
        if contains_any and not any(token in lowered_topic for token in contains_any):
            continue
        if contains_all and not all(token in lowered_topic for token in contains_all):
            continue

        reason = _read_optional_string(raw_rule.get("reason"))
        if reason is None:
            matched_tokens = contains_all if contains_all else contains_any
            reason = f"topic rule matched: {', '.join(matched_tokens[:3])}"
        return EditorialTopicRuleMatch(label=label, rule_index=index, reason=reason)
    return None


def _resolve_editorial_niche_profile(raw_mapping: object, niche: str | None) -> str | None:
    if niche is None or not isinstance(raw_mapping, Mapping):
        return None
    for raw_niche, raw_profile in raw_mapping.items():
        if _normalize_string(raw_niche) != niche.lower():
            continue
        profile = _read_profile_label(raw_profile)
        if profile is not None:
            return profile
    return None


def _read_profile_label(value: object) -> str | None:
    normalized_value = _read_optional_string(value)
    if normalized_value is None:
        return None
    if normalized_value not in PROFILE_LIBRARY:
        return None
    return normalized_value


def _read_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    collected: list[str] = []
    for item in value:
        normalized_item = _normalize_string(item)
        if normalized_item is not None and normalized_item not in collected:
            collected.append(normalized_item)
    return collected


def _extract_keywords(topic: str) -> list[str]:
    keywords: list[str] = []
    for match in WORD_RE.findall(topic):
        normalized_word = match.lower()
        if normalized_word.isdigit():
            continue
        if normalized_word in STOP_WORDS:
            continue
        if len(normalized_word) <= 2 and not normalized_word.isdigit():
            continue
        if normalized_word not in keywords:
            keywords.append(normalized_word)
        if len(keywords) == 4:
            break
    return keywords


def _resolve_platform_label(job: VideoJob) -> str:
    platform_value = job.metadata.get("platform")
    if isinstance(platform_value, str) and platform_value.strip():
        normalized_platform = platform_value.strip().lower()
        if normalized_platform == "short_video":
            return DEFAULT_PLATFORM_LABEL
        if normalized_platform == "instagram_reels":
            return "Instagram Reels"
        if normalized_platform == "youtube_shorts":
            return "YouTube Shorts"
        return " ".join(part.capitalize() for part in normalized_platform.replace("_", " ").split())
    return DEFAULT_PLATFORM_LABEL


def _resolve_audience_label(job: VideoJob) -> str:
    audience_value = job.metadata.get("audience")
    if isinstance(audience_value, str) and audience_value.strip():
        return " ".join(audience_value.strip().split())
    return DEFAULT_AUDIENCE_LABEL


def _build_topic_handle(label: str, keywords: list[str]) -> str:
    if label == "hooks":
        return "tes hooks"
    if label == "subtitles":
        return "tes sous-titres"
    if label == "repurpose":
        return "cette idee"
    if label == "automation":
        return "ton pipeline"
    if keywords:
        return "ce sujet"
    return "cette video"


def _build_script_sections(
    profile: TopicProfile,
    *,
    proof: ProofPayload,
    hook_candidate: HookCandidate,
    cta_candidate: CtaCandidate,
    body_structure: str,
) -> ScriptSections:
    templates = PROFILE_LIBRARY[profile.label]
    base_points = [template.format(**_build_script_context(profile)) for template in templates["points"]]
    points = _compose_points(base_points, proof.point, structure_name=body_structure)
    return ScriptSections(
        hook=_ensure_sentence(hook_candidate.text),
        points=[_ensure_sentence(point) for point in points],
        cta=_ensure_sentence(cta_candidate.text) if cta_candidate.text else "",
    )


def _build_proof_payload(
    job: VideoJob,
    profile: TopicProfile,
    *,
    context: dict[str, str],
    seed: bytes,
    memory,
) -> tuple[ProofPayload, DiversityDecision]:
    mode_candidates = _resolve_proof_mode_candidates(job, profile)
    mode_decision = memory.choose(
        "script_proof_mode",
        [
            DiversityCandidate(
                key=mode,
                signals={"script_proof_mode": mode},
                base_rank=index,
            )
            for index, mode in enumerate(mode_candidates)
        ],
    )
    mode = mode_decision.selected_key
    tool = _resolve_proof_tool(job, profile)
    result = _resolve_proof_result(job, profile)
    example = _resolve_proof_example(job, profile, tool=tool, result=result)
    field_error = _resolve_proof_error(job, profile, tool=tool)
    proof_context = dict(context)
    proof_context.update(
        {
            "proof_tool": tool,
            "proof_result": result,
            "proof_example": example,
            "proof_error": field_error,
        }
    )
    detail = {
        "example": example,
        "tool": tool,
        "result": result,
        "field_error": field_error,
    }[mode]
    point_options = PROOF_POINT_LIBRARY[mode]
    point = point_options[seed[11] % len(point_options)].format(**proof_context)
    return (
        ProofPayload(
            mode=mode,
            tone=PROOF_TONE_BY_MODE[mode],
            detail=detail,
            point=point,
        ),
        mode_decision,
    )


def _resolve_proof_mode_candidates(job: VideoJob, profile: TopicProfile) -> list[str]:
    raw_value = job.metadata.get("script_proof_mode", DEFAULT_PROOF_MODE)
    explicit_mode = None
    if isinstance(raw_value, str) and raw_value.strip():
        explicit_mode = PROOF_MODE_ALIASES.get(raw_value.strip().lower())
    if explicit_mode and explicit_mode != "auto":
        return [explicit_mode]

    lowered_topic = profile.topic.lower()
    candidates: list[str] = []
    if any(keyword in lowered_topic for keyword in ERROR_SIGNAL_KEYWORDS):
        candidates.append("field_error")
    if any(signal in lowered_topic for signal, _ in TOOL_HINTS):
        candidates.append("tool")
    if _has_result_signal(profile, lowered_topic):
        candidates.append("result")
    if any(keyword in lowered_topic for keyword in EXAMPLE_SIGNAL_KEYWORDS):
        candidates.append("example")
    candidates.append(DEFAULT_PROOF_MODE_BY_PROFILE[profile.label])
    for mode in ("result", "example", "tool", "field_error"):
        if mode not in candidates:
            candidates.append(mode)
    return candidates


def _select_hook_candidate(
    profile: TopicProfile,
    *,
    context: dict[str, str],
    proof: ProofPayload,
    seed: bytes,
    memory,
) -> tuple[HookCandidate, DiversityDecision]:
    base_hooks = PROFILE_LIBRARY[profile.label]["hooks"]
    hook_families = PROFILE_HOOK_FAMILIES[profile.label]
    bridge_templates = PROOF_HOOK_BRIDGES[proof.mode]
    bridge_families = PROOF_HOOK_BRIDGE_FAMILIES[proof.mode]

    candidates: list[HookCandidate] = []
    diversity_candidates: list[DiversityCandidate] = []
    rank = 0
    for base_index in _ordered_indices(len(base_hooks), seed[0]):
        for bridge_index in _ordered_indices(len(bridge_templates), seed[5]):
            hook_family = hook_families[base_index]
            bridge_family = bridge_families[bridge_index]
            base_hook = base_hooks[base_index].format(**context)
            bridge = bridge_templates[bridge_index]
            text = f"{_ensure_sentence(base_hook)} {_ensure_sentence(bridge)}"
            signature = f"{hook_family}|{bridge_family}"
            candidate = HookCandidate(
                key=signature,
                text=text,
                family=hook_family,
                signature=signature,
            )
            candidates.append(candidate)
            diversity_candidates.append(
                DiversityCandidate(
                    key=candidate.key,
                    signals={
                        "script_hook_family": hook_family,
                        "script_hook_signature": signature,
                        "script_hook_text": text,
                    },
                    base_rank=rank,
                )
            )
            rank += 1

    decision = memory.choose("script_hook", diversity_candidates)
    selected = next(candidate for candidate in candidates if candidate.key == decision.selected_key)
    return selected, decision


def _resolve_proof_tool(job: VideoJob, profile: TopicProfile) -> str:
    custom_tool = _read_optional_metadata_string(job, "script_proof_tool")
    if custom_tool:
        return custom_tool

    lowered_topic = profile.topic.lower()
    for hint, tool_name in TOOL_HINTS:
        if hint in lowered_topic:
            return tool_name
    return PROFILE_DEFAULT_TOOLS[profile.label]


def _resolve_proof_result(job: VideoJob, profile: TopicProfile) -> str:
    custom_result = _read_optional_metadata_string(job, "script_proof_result")
    if custom_result:
        return custom_result

    if profile.label == "hooks":
        return f"{profile.number_token or '2'} secondes de retention gagnees sur l'ouverture"
    if profile.label == "subtitles":
        return "une lecture comprise sans pause des les 3 premiers mots"
    if profile.label == "repurpose":
        return f"{profile.number_token or '3'} angles utiles tires d'une seule idee"
    if profile.label == "automation":
        return "30 minutes de suivi manuel evitees par jour"
    return "un message compris en moins de 5 secondes"


def _resolve_proof_example(
    job: VideoJob,
    profile: TopicProfile,
    *,
    tool: str,
    result: str,
) -> str:
    custom_example = _read_optional_metadata_string(job, "script_proof_example")
    if custom_example:
        return custom_example

    if profile.label == "hooks":
        return f"un createur garde le meme sujet, change juste l'ouverture et recupere {result}"
    if profile.label == "subtitles":
        return f"sur un tuto monte dans {tool}, des lignes plus courtes rendent l'idee lisible sans revenir en arriere"
    if profile.label == "repurpose":
        return "une note Notion devient une video erreur, une video methode et une video checklist"
    if profile.label == "automation":
        return (
            f"un lead sort de Typeform, passe dans {tool} puis arrive dans le CRM avec le mauvais champ "
            "si le tri est mal pense"
        )
    return "un freelance remplace une intro floue par une promesse nette et le message se comprend tout de suite"


def _resolve_proof_error(job: VideoJob, profile: TopicProfile, *, tool: str) -> str:
    custom_error = _read_optional_metadata_string(job, "script_proof_error")
    if custom_error:
        return custom_error

    if profile.label == "hooks":
        return "une ouverture qui annonce le sujet mais ne promet rien de concret"
    if profile.label == "subtitles":
        return "des lignes trop longues qui obligent a finir de lire avant de comprendre"
    if profile.label == "repurpose":
        return "recycler la meme idee sans changer ni la promesse ni l'angle"
    if profile.label == "automation":
        return f"un scenario {tool} qui part avant qu'un champ critique soit rempli"
    return "un script qui empile des conseils sans une seule preuve concrete"


def _select_cta_candidate(
    job: VideoJob,
    template_group: dict[str, tuple[str, ...]],
    *,
    proof: ProofPayload,
    seed: bytes,
    context: dict[str, str],
    memory,
) -> tuple[CtaCandidate, DiversityDecision]:
    render_context = _build_cta_render_context(context, proof)
    candidates: list[CtaCandidate] = []
    diversity_candidates: list[DiversityCandidate] = []
    rank = 0
    for mode_rank, requested_mode in enumerate(_resolve_cta_mode_candidates(job)):
        if requested_mode == "none":
            candidate = CtaCandidate(
                key="none",
                text="",
                family="none",
                signature="none",
                mode="none",
            )
            candidates.append(candidate)
            diversity_candidates.append(
                DiversityCandidate(
                    key=candidate.key,
                    signals={
                        "script_cta_family": candidate.family,
                        "script_cta_signature": candidate.signature,
                    },
                    base_rank=rank,
                )
            )
            rank += 1
            continue

        proof_templates = PROOF_CTA_LIBRARY.get(proof.mode, {})
        options = proof_templates.get(requested_mode)
        source_kind = "proof"
        resolved_mode = requested_mode
        if options is None:
            options = template_group.get(requested_mode)
            source_kind = "profile"
        if options is None:
            resolved_mode = "soft"
            options = template_group["soft"]
            source_kind = "profile"

        for option_index in _ordered_indices(len(options), seed[23] + mode_rank):
            text = options[option_index].format(**render_context)
            family = f"{resolved_mode}:{source_kind}"
            signature = f"{family}:{option_index}"
            candidate = CtaCandidate(
                key=signature,
                text=text,
                family=family,
                signature=signature,
                mode=resolved_mode,
            )
            candidates.append(candidate)
            diversity_candidates.append(
                DiversityCandidate(
                    key=candidate.key,
                    signals={
                        "script_cta_family": family,
                        "script_cta_signature": signature,
                        "script_cta_text": text,
                    },
                    base_rank=rank,
                )
            )
            rank += 1

    decision = memory.choose("script_cta", diversity_candidates)
    selected = next(candidate for candidate in candidates if candidate.key == decision.selected_key)
    return selected, decision


def _select_body_structure(
    job: VideoJob,
    *,
    profile: TopicProfile,
    proof: ProofPayload,
    hook_candidate: HookCandidate,
    cta_candidate: CtaCandidate,
    point_count: int,
    memory,
) -> tuple[str, DiversityDecision]:
    structure_options = _resolve_body_structure_options(job, point_count)
    decision = memory.choose(
        "script_body_structure",
        [
            DiversityCandidate(
                key=option["name"],
                signals={
                    "script_body_structure": option["name"],
                    "script_signature": _build_script_signature(
                        profile.label,
                        proof.mode,
                        hook_candidate.family,
                        cta_candidate.family,
                        option["name"],
                    ),
                },
                base_rank=index,
            )
            for index, option in enumerate(structure_options)
        ],
    )
    return decision.selected_key, decision


def _resolve_body_structure_options(job: VideoJob, point_count: int) -> list[dict[str, tuple[int | str, ...] | str]]:
    raw_value = _read_optional_metadata_string(job, "script_body_structure")
    available_options = list(BODY_STRUCTURE_OPTIONS[point_count])
    if raw_value:
        matching = [option for option in available_options if option["name"] == raw_value]
        if matching:
            return matching
    return available_options


def _resolve_body_structure_option(structure_name: str) -> dict[str, tuple[int | str, ...] | str]:
    for options in BODY_STRUCTURE_OPTIONS.values():
        for option in options:
            if option["name"] == structure_name:
                return option
    raise ScriptGenerationError(f"unknown body structure: {structure_name}")


def _compose_points(base_points: list[str], proof_point: str, *, structure_name: str) -> list[str]:
    if not base_points:
        raise ScriptGenerationError("profile points are empty")
    option = _resolve_body_structure_option(structure_name)
    lookup = {
        0: base_points[0],
        1: base_points[1 if len(base_points) > 1 else 0],
        -1: base_points[-1],
        "proof": proof_point,
    }
    return [lookup[item] for item in option["order"]]


def _read_optional_metadata_string(job: VideoJob, key: str) -> str | None:
    return _read_optional_string(job.metadata.get(key))


def _read_optional_string(value: object) -> str | None:
    normalized_value = _normalize_string(value, lower=False)
    if normalized_value is None:
        return None
    return normalized_value


def _normalize_string(value: object, *, lower: bool = True) -> str | None:
    if not isinstance(value, str):
        return None
    normalized_value = " ".join(value.split()).strip()
    if not normalized_value:
        return None
    if lower:
        return normalized_value.lower()
    return normalized_value


def _has_result_signal(profile: TopicProfile, lowered_topic: str) -> bool:
    if any(keyword in lowered_topic for keyword in RESULT_SIGNAL_KEYWORDS):
        return True
    if profile.number_token is None:
        return False
    return len(profile.number_token) <= 3


def _build_cta_render_context(context: dict[str, str], proof: ProofPayload) -> dict[str, str]:
    render_context = dict(context)
    render_context["proof_tool"] = proof.detail if proof.mode == "tool" else context["topic_reference"]
    render_context["proof_result"] = proof.detail if proof.mode == "result" else context["topic_reference"]
    render_context["proof_example"] = proof.detail if proof.mode == "example" else context["topic_reference"]
    render_context["proof_error"] = proof.detail if proof.mode == "field_error" else context["topic_reference"]
    return render_context


def _resolve_cta_mode_candidates(job: VideoJob) -> list[str]:
    raw_value = job.metadata.get("script_cta_mode")
    if isinstance(raw_value, bool):
        return [DEFAULT_CTA_MODE, "comment", "subscribe"] if raw_value else ["none"]
    if not isinstance(raw_value, str):
        return [DEFAULT_CTA_MODE, "comment", "subscribe"]
    normalized_value = raw_value.strip().lower()
    if normalized_value in {"soft", "subscribe", "comment", "none"}:
        return [normalized_value]
    return [DEFAULT_CTA_MODE, "comment", "subscribe"]


def _ordered_indices(length: int, seed_value: int) -> list[int]:
    if length <= 0:
        return []
    start = seed_value % length
    return [((start + offset) % length) for offset in range(length)]


def _build_script_signature(
    profile: str,
    proof_mode: str,
    hook_family: str,
    cta_family: str,
    body_structure: str,
) -> str:
    return "|".join((profile, proof_mode, hook_family, cta_family, body_structure))


def _pick(options: tuple[str, ...], seed_value: int) -> str:
    return options[seed_value % len(options)]


def _to_topic_reference(topic: str) -> str:
    if not topic:
        return "ce sujet"
    if len(topic) == 1:
        return topic.lower()
    return topic[:1].lower() + topic[1:]


def _ensure_sentence(text: str) -> str:
    normalized_text = " ".join(text.split()).strip()
    if not normalized_text:
        raise ScriptGenerationError("generated script section is empty")
    if normalized_text[-1] not in ".!?":
        normalized_text += "."
    return normalized_text


def _render_script(sections: ScriptSections) -> str:
    blocks = [sections.hook, *sections.points]
    if sections.cta:
        blocks.append(sections.cta)
    return "\n\n".join(blocks)


def _count_words(text: str) -> int:
    return len(WORD_RE.findall(text))


def _estimate_duration_seconds(word_count: int) -> int:
    if word_count <= 0:
        return 15
    return max(15, min(45, round(word_count / 2.8)))


__all__ = ["ScriptGenerationError", "generate_script_step"]
