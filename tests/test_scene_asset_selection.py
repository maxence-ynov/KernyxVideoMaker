from __future__ import annotations

from pathlib import Path
import unittest

from pipeline.scene_asset_selector import (
    AssetCatalog,
    AssetCatalogEntry,
    AssetCatalogError,
    AssetSelectionContext,
    build_scene_visual_intent,
    load_asset_catalog,
    register_selected_asset,
    resolve_scene_asset,
)
from pipeline.scene_planner import SceneSegment
from pipeline.visual_gen import ConfiguredSceneAsset, SceneAssetSelectionState, _select_scene_asset_from_pool
from pipeline.visual_manifest import SceneVisualAsset
from schemas import VideoJob

EXAMPLE_ROOT = Path(__file__).resolve().parents[1] / "config" / "examples"
HIPPO_ASSET = EXAMPLE_ROOT / "assets" / "hippopotamus.ppm"
AUTOMATION_ASSET = EXAMPLE_ROOT / "assets" / "automation_workflow.ppm"
DASHBOARD_ASSET = EXAMPLE_ROOT / "assets" / "business_dashboard.ppm"
FALLBACK_ASSET = EXAMPLE_ROOT / "assets" / "fallback_pool.ppm"


def _build_scene(
    *,
    scene_id: str,
    scene_type: str,
    source_text: str,
    title_text: str,
    highlight_text: str,
    keywords: list[str],
    visual_asset_mode: str,
) -> SceneSegment:
    return SceneSegment(
        scene_id=scene_id,
        index=1,
        role="body",
        scene_type=scene_type,
        source_text=source_text,
        title_text=title_text,
        highlight_text=highlight_text,
        keywords=keywords,
        word_count=max(1, len(source_text.split())),
        proof_mode="generic",
        visual_asset_mode=visual_asset_mode,
        motion_effect="static",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=0.0,
        end_seconds=1.0,
        duration_seconds=1.0,
    )


class SceneAssetSelectionTests(unittest.TestCase):
    def test_load_asset_catalog_from_json_path(self) -> None:
        job = VideoJob(
            topic="Pourquoi l'hippopotame est dangereux",
            template="tiktok_ai",
            account="tiktok_main",
            metadata={
                "config_path": str(EXAMPLE_ROOT / "topics_scene_asset_catalog_example.json"),
                "scene_asset_catalog_path": "scene_asset_catalog.example.json",
            },
        )

        catalog = load_asset_catalog(job)

        self.assertIsNotNone(catalog)
        assert catalog is not None
        self.assertEqual(len(catalog.entries), 3)
        self.assertEqual(catalog.entries[0].path, HIPPO_ASSET.resolve())
        self.assertEqual(catalog.entries[0].entities, ("hippopotame",))

    def test_build_scene_visual_intent_marks_concrete_subject_as_strict(self) -> None:
        scene = _build_scene(
            scene_id="scene_01",
            scene_type="explain",
            source_text="Le plus dangereux animal ici c'est l'hippopotame",
            title_text="Hippopotame",
            highlight_text="animal tres agressif",
            keywords=["hippopotame", "animal"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene)

        self.assertEqual(intent.match_mode, "strict")
        self.assertEqual(intent.required_entities, ("hippopotame",))

    def test_build_scene_visual_intent_marks_catalog_entity_as_strict(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=HIPPO_ASSET,
                    asset_type="image",
                    tags=("animal", "wildlife"),
                    entities=("hippopotame",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_catalog_entity",
            scene_type="explain",
            source_text="Le plus dangereux animal ici c'est l'hippopotame",
            title_text="Hippopotame",
            highlight_text="animal tres agressif",
            keywords=["hippopotame", "animal"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene, catalog)

        self.assertEqual(intent.match_mode, "strict")
        self.assertEqual(intent.required_entities, ("hippopotame",))

    def test_build_scene_visual_intent_keeps_automation_soft(self) -> None:
        scene = _build_scene(
            scene_id="scene_automation",
            scene_type="workflow",
            source_text="3 erreurs qui rendent ton automatisation inutile",
            title_text="Automatisation",
            highlight_text="ton systeme perd en qualite",
            keywords=["erreurs", "automatisation", "inutile", "ton"],
            visual_asset_mode="workflow",
        )

        intent = build_scene_visual_intent(scene)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        self.assertNotIn("ton", intent.required_entities)
        self.assertNotIn("vrai", intent.required_entities)
        self.assertNotIn("sur", intent.required_entities)

    def test_build_scene_visual_intent_ignores_noisy_scene_text_for_strict_mode(self) -> None:
        scene = _build_scene(
            scene_id="scene_noise_only",
            scene_type="explain",
            source_text="sur tiktok on perd rarement a cause du sujet",
            title_text="Visualise tout",
            highlight_text="tout doit presenter le fait",
            keywords=["tiktok", "perd", "rarement", "cause", "visualise", "doit"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        for forbidden_word in ("tiktok", "perd", "rarement", "cause", "visualise", "doit"):
            self.assertNotIn(forbidden_word, intent.required_entities)

    def test_build_scene_visual_intent_uses_global_topic_for_strict_entities(self) -> None:
        scene = _build_scene(
            scene_id="scene_topic_fallback",
            scene_type="hook",
            source_text="sur tiktok on perd rarement a cause du sujet",
            title_text="Visualise tout",
            highlight_text="tout doit presenter le fait",
            keywords=["tiktok", "perd", "rarement", "cause"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(
            scene,
            global_topic="Pourquoi l'hippopotame est plus dangereux qu'un lion",
        )

        self.assertEqual(intent.match_mode, "strict")
        self.assertIn("hippopotame", intent.required_entities)
        self.assertIn("lion", intent.required_entities)
        for forbidden_word in ("tiktok", "perd", "rarement", "cause"):
            self.assertNotIn(forbidden_word, intent.required_entities)

    def test_build_scene_visual_intent_keeps_intro_energy_soft(self) -> None:
        scene = _build_scene(
            scene_id="scene_intro",
            scene_type="hook",
            source_text="Pourquoi ton intro manque d'energie",
            title_text="Ton intro",
            highlight_text="plus d'energie ne suffit pas",
            keywords=["ton", "intro", "energie"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        self.assertNotIn("ton", intent.required_entities)

    def test_build_scene_visual_intent_keeps_catalog_absent_abstract_tokens_soft(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=HIPPO_ASSET,
                    asset_type="image",
                    tags=("hippopotame", "animal"),
                    entities=("hippopotame",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_abstract_catalog_absent",
            scene_type="hook",
            source_text="3 erreurs qui rendent ton automatisation inutile",
            title_text="Pourquoi vendre sans energie",
            highlight_text="deuxieme erreur qui rend l'intro faible",
            keywords=["rendent", "vendre", "deuxieme"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene, catalog)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        for forbidden_word in ("rendent", "vendre", "deuxieme"):
            self.assertNotIn(forbidden_word, intent.required_entities)

    def test_build_scene_visual_intent_keeps_only_generic_or_verbal_candidates_soft(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=FALLBACK_ASSET,
                    asset_type="image",
                    tags=("girafe", "animal"),
                    entities=("girafe",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_generic_verbs_only",
            scene_type="explain",
            source_text="Cette phrase comprend une promesse generale",
            title_text="Phrase promesse",
            highlight_text="comprend une deuxieme partie",
            keywords=["phrase", "promesse", "comprend"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene, catalog)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        for forbidden_word in ("phrase", "promesse", "comprend"):
            self.assertNotIn(forbidden_word, intent.required_entities)

    def test_build_scene_visual_intent_filters_empty_words_from_required_entities(self) -> None:
        scene = _build_scene(
            scene_id="scene_noise",
            scene_type="explain",
            source_text="Le vrai probleme sur ton business",
            title_text="Vrai probleme",
            highlight_text="sur ton systeme",
            keywords=["vrai", "sur", "ton", "business"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene)

        self.assertEqual(intent.match_mode, "soft")
        self.assertEqual(intent.required_entities, ())
        for forbidden_word in ("sur", "vrai", "ton"):
            self.assertNotIn(forbidden_word, intent.required_entities)

    def test_resolve_scene_asset_raises_when_strict_entity_is_missing(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=FALLBACK_ASSET,
                    asset_type="image",
                    tags=("girafe", "animal"),
                    entities=("girafe",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_01",
            scene_type="explain",
            source_text="Le plus dangereux animal ici c'est l'hippopotame",
            title_text="Hippopotame",
            highlight_text="animal tres agressif",
            keywords=["hippopotame", "animal"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene, catalog)

        self.assertEqual(intent.match_mode, "strict")
        with self.assertRaisesRegex(AssetCatalogError, "no catalog asset matches strict scene intent"):
            resolve_scene_asset(intent, catalog, AssetSelectionContext())

    def test_resolve_scene_asset_accepts_strict_catalog_tag_match(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=HIPPO_ASSET,
                    asset_type="image",
                    tags=("lion", "animal"),
                    entities=(),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_catalog_tag",
            scene_type="explain",
            source_text="Pourquoi le lion est plus dangereux",
            title_text="Lion",
            highlight_text="animal sauvage",
            keywords=["lion", "animal"],
            visual_asset_mode="dashboard",
        )

        intent = build_scene_visual_intent(scene, catalog)
        candidate = resolve_scene_asset(intent, catalog, AssetSelectionContext())

        self.assertEqual(intent.match_mode, "strict")
        self.assertEqual(intent.required_entities, ("lion",))
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertIn("lion", candidate.matched_tags)
        self.assertEqual(candidate.matched_entities, ())

    def test_resolve_scene_asset_prefers_semantic_match_and_diversifies(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=AUTOMATION_ASSET,
                    asset_type="image",
                    tags=("automatisation", "workflow"),
                    entities=(),
                    visual_categories=("workflow", "automation"),
                    allowed_scene_types=("workflow", "explain"),
                    source_kind="test_catalog[1]",
                ),
                AssetCatalogEntry(
                    path=DASHBOARD_ASSET,
                    asset_type="image",
                    tags=("processus", "systeme"),
                    entities=(),
                    visual_categories=("workflow", "system"),
                    allowed_scene_types=("workflow", "explain"),
                    source_kind="test_catalog[2]",
                ),
                AssetCatalogEntry(
                    path=HIPPO_ASSET,
                    asset_type="image",
                    tags=("hippopotame", "animal"),
                    entities=("hippopotame",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[3]",
                ),
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_02",
            scene_type="workflow",
            source_text="Ton automatisation casse quand le processus n'est pas clair",
            title_text="Automatisation",
            highlight_text="processus et systeme",
            keywords=["automatisation", "processus"],
            visual_asset_mode="workflow",
        )
        intent = build_scene_visual_intent(scene, catalog)
        context = AssetSelectionContext()

        first_candidate = resolve_scene_asset(intent, catalog, context)
        self.assertIsNotNone(first_candidate)
        assert first_candidate is not None
        self.assertNotEqual(first_candidate.entry.path, HIPPO_ASSET)

        register_selected_asset(context, first_candidate.entry.selection_key)
        second_candidate = resolve_scene_asset(intent, catalog, context)

        self.assertIsNotNone(second_candidate)
        assert second_candidate is not None
        self.assertNotEqual(first_candidate.entry.path, second_candidate.entry.path)
        self.assertNotEqual(second_candidate.entry.path, HIPPO_ASSET)

    def test_resolve_scene_asset_returns_none_when_soft_scene_has_no_match(self) -> None:
        catalog = AssetCatalog(
            entries=[
                AssetCatalogEntry(
                    path=HIPPO_ASSET,
                    asset_type="image",
                    tags=("hippopotame", "animal"),
                    entities=("hippopotame",),
                    visual_categories=("animal", "wildlife"),
                    allowed_scene_types=("hook", "explain"),
                    source_kind="test_catalog[1]",
                )
            ],
            source_key="test_catalog",
        )
        scene = _build_scene(
            scene_id="scene_03",
            scene_type="workflow",
            source_text="Automatisation et efficacite pour ton business",
            title_text="Automatisation",
            highlight_text="systeme plus rapide",
            keywords=["automatisation", "efficacite"],
            visual_asset_mode="workflow",
        )

        intent = build_scene_visual_intent(scene, catalog)
        candidate = resolve_scene_asset(intent, catalog, AssetSelectionContext())

        self.assertEqual(intent.match_mode, "soft")
        self.assertIsNone(candidate)

    def test_pool_selection_avoids_immediate_repetition(self) -> None:
        state = SceneAssetSelectionState(
            pool_assets=[
                ConfiguredSceneAsset(
                    path=FALLBACK_ASSET,
                    source_kind="scene_asset_pool[1]",
                    order=0,
                    selected_by="pool",
                    asset_origin_path=str(FALLBACK_ASSET),
                ),
                ConfiguredSceneAsset(
                    path=AUTOMATION_ASSET,
                    source_kind="scene_asset_pool[2]",
                    order=1,
                    selected_by="pool",
                    asset_origin_path=str(AUTOMATION_ASSET),
                ),
            ],
            asset_catalog=None,
            selection_context=AssetSelectionContext(),
        )

        first_asset = _select_scene_asset_from_pool(state)
        self.assertIsNotNone(first_asset)
        assert first_asset is not None
        register_selected_asset(state.selection_context, first_asset.asset_origin_path)

        second_asset = _select_scene_asset_from_pool(state)

        self.assertIsNotNone(second_asset)
        assert second_asset is not None
        self.assertNotEqual(first_asset.path, second_asset.path)

    def test_visual_manifest_round_trip_preserves_audit_fields(self) -> None:
        original = SceneVisualAsset(
            scene_id="scene_04",
            scene_index=4,
            asset_path="storage/assets/scene_04.ppm",
            asset_type="image",
            source_kind="test_catalog[1]",
            selected_by="catalog_match",
            asset_origin_path="config/examples/assets/hippopotamus.ppm",
            matched_tags=("hippopotame", "animal"),
            matched_entities=("hippopotame",),
            matched_visual_categories=("animal", "wildlife"),
            selection_score=78.0,
            reuse_rank=1,
            visual_asset_mode="dashboard",
            motion_effect="zoom_in",
            transition_effect="fade",
            transition_duration_seconds=0.2,
        )

        restored = SceneVisualAsset.from_mapping(original.to_metadata())

        self.assertEqual(restored.selected_by, "catalog_match")
        self.assertEqual(restored.asset_origin_path, "config/examples/assets/hippopotamus.ppm")
        self.assertEqual(restored.matched_entities, ("hippopotame",))
        self.assertEqual(restored.matched_visual_categories, ("animal", "wildlife"))
        self.assertEqual(restored.selection_score, 78.0)
        self.assertEqual(restored.reuse_rank, 1)


if __name__ == "__main__":
    unittest.main()
