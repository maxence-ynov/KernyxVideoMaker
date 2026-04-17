from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.assembler import _resolve_audio_layers, plan_scene_sfx_events
from pipeline.edit_planner import EditManifest, SceneEditPlan, build_edit_manifest
from pipeline.scene_planner import SceneManifest, SceneSegment, SubtitleCue
from schemas import VideoJob

TEST_AUDIO_PATH = (Path(__file__).resolve().parents[1] / "test_fr.wav").resolve()
STRONG_PROFILES = {"hit_strong", "whoosh_fast", "glitch_light"}


def _build_scene(
    *,
    scene_id: str,
    index: int,
    role: str,
    scene_type: str,
    source_text: str,
    visual_asset_mode: str,
    duration_seconds: float,
    start_seconds: float,
    cue_texts: list[str] | None = None,
    proof_mode: str = "generic",
    highlight_text: str | None = None,
    keywords: list[str] | None = None,
) -> SceneSegment:
    cue_texts = cue_texts or [source_text]
    cue_duration = duration_seconds / max(1, len(cue_texts))
    cue_timeline: list[SubtitleCue] = []
    current_start = start_seconds
    for cue_index, cue_text in enumerate(cue_texts, start=1):
        cue_end = round(current_start + cue_duration, 3)
        cue_timeline.append(
            SubtitleCue(
                index=index * 10 + cue_index,
                start_seconds=round(current_start, 3),
                end_seconds=cue_end,
                duration_seconds=round(cue_end - current_start, 3),
                text=cue_text,
                word_count=max(1, len(cue_text.split())),
            )
        )
        current_start = cue_end

    return SceneSegment(
        scene_id=scene_id,
        index=index,
        role=role,
        scene_type=scene_type,
        source_text=source_text,
        title_text=source_text,
        highlight_text=highlight_text or source_text,
        keywords=keywords or ["hook", "proof", "workflow", "cta"],
        word_count=max(1, len(source_text.split())),
        proof_mode=proof_mode,
        visual_asset_mode=visual_asset_mode,
        motion_effect="static",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=start_seconds,
        end_seconds=round(start_seconds + duration_seconds, 3),
        duration_seconds=duration_seconds,
        cue_timeline=cue_timeline,
    )


class AudioSfxPolicyTests(unittest.TestCase):
    def test_plan_scene_sfx_events_matches_role_policy(self) -> None:
        job = VideoJob(topic="SFX policy", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=10.2,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_hook",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="3 erreurs qui ruinent ton hook",
                    visual_asset_mode="screenshot",
                    duration_seconds=2.1,
                    start_seconds=0.0,
                    cue_texts=["3 erreurs", "qui ruinent ton hook"],
                    highlight_text="3 erreurs",
                ),
                _build_scene(
                    scene_id="scene_proof",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 12% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.8,
                    start_seconds=2.1,
                    cue_texts=["Dashboard", "12% de retention en plus"],
                    proof_mode="result",
                    highlight_text="12% de retention",
                ),
                _build_scene(
                    scene_id="scene_explain",
                    index=2,
                    role="body",
                    scene_type="workflow",
                    source_text="On separe le workflow en trois etapes simples",
                    visual_asset_mode="workflow",
                    duration_seconds=3.4,
                    start_seconds=4.9,
                    cue_texts=["On separe le workflow", "en trois etapes", "simples"],
                    highlight_text="trois etapes",
                ),
                _build_scene(
                    scene_id="scene_cta",
                    index=3,
                    role="cta",
                    scene_type="cta",
                    source_text="Commente stack et je te donne le template",
                    visual_asset_mode="ui",
                    duration_seconds=1.9,
                    start_seconds=8.3,
                    cue_texts=["Commente stack", "et je te donne le template"],
                    highlight_text="Commente stack",
                ),
            ],
        )

        edit_manifest = build_edit_manifest(job, scene_manifest=manifest)
        decisions = {
            decision.scene_id: decision
            for decision in plan_scene_sfx_events(job=job, manifest=manifest, edit_manifest=edit_manifest)
        }

        self.assertIn(decisions["scene_hook"].sfx_selected, {"hit_soft", "hit_strong", "glitch_light", "whoosh_fast"})
        self.assertIn(decisions["scene_hook"].placement, {"scene_start", "early_hook", "first_keyword", "transition_in"})
        self.assertIn(decisions["scene_proof"].sfx_selected, {"click_ui_soft", "click_ui_hard", "pop_light", "hit_soft"})
        self.assertNotIn(decisions["scene_proof"].sfx_selected, STRONG_PROFILES)
        self.assertIn(decisions["scene_explain"].sfx_selected, {None, "whoosh_soft", "pop_short"})
        self.assertIn(decisions["scene_cta"].sfx_selected, {None, "pop_short", "hit_soft", "whoosh_soft", "riser_short"})
        if decisions["scene_cta"].sfx_selected is not None:
            self.assertIn(decisions["scene_cta"].placement, {"cta_close", "scene_end", "transition_out"})

    def test_plan_scene_sfx_events_keeps_only_one_strong_accent(self) -> None:
        job = VideoJob(topic="Strong SFX", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=5.5,
            primary_visual_asset_mode="screenshot",
            scenes=[
                _build_scene(
                    scene_id="scene_01",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="3 erreurs qui cassent ton hook",
                    visual_asset_mode="screenshot",
                    duration_seconds=2.0,
                    start_seconds=0.0,
                    cue_texts=["3 erreurs", "qui cassent ton hook"],
                ),
                _build_scene(
                    scene_id="scene_02",
                    index=1,
                    role="hook",
                    scene_type="hook",
                    source_text="Stop aux hooks plats",
                    visual_asset_mode="screenshot",
                    duration_seconds=1.8,
                    start_seconds=2.0,
                    cue_texts=["Stop", "aux hooks plats"],
                ),
                _build_scene(
                    scene_id="scene_03",
                    index=2,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 9% de clic en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=1.7,
                    start_seconds=3.8,
                    cue_texts=["Dashboard", "9% de clic en plus"],
                    proof_mode="result",
                ),
            ],
        )
        edit_manifest = EditManifest(
            version="scene_edit_v1",
            scenes=[
                SceneEditPlan(
                    scene_id="scene_01",
                    scene_index=0,
                    role="hook",
                    scene_type="hook",
                    pace_role="hook",
                    camera_pattern="impact_zoom",
                    camera_intensity="high",
                    transition_in="cut",
                    transition_out="flash_cut",
                    text_style="hook_focus",
                    overlay_style="light_glow",
                    sfx_profile="hit_strong",
                    bgm_profile="tension_light",
                ),
                SceneEditPlan(
                    scene_id="scene_02",
                    scene_index=1,
                    role="hook",
                    scene_type="hook",
                    pace_role="hook",
                    camera_pattern="push_in_fast",
                    camera_intensity="high",
                    transition_in="flash_cut",
                    transition_out="cut",
                    text_style="hook_focus",
                    overlay_style="light_glow",
                    sfx_profile="hit_strong",
                    bgm_profile="tension_light",
                ),
                SceneEditPlan(
                    scene_id="scene_03",
                    scene_index=2,
                    role="body",
                    scene_type="proof",
                    pace_role="proof",
                    camera_pattern="hold_static",
                    camera_intensity="low",
                    transition_in="blur_in",
                    transition_out="cut",
                    text_style="proof_clean",
                    overlay_style="proof_frame",
                    sfx_profile="click_ui_soft",
                    bgm_profile="business_clean",
                ),
            ],
        )

        decisions = plan_scene_sfx_events(job=job, manifest=manifest, edit_manifest=edit_manifest)

        strong_count = sum(1 for decision in decisions if decision.sfx_selected in STRONG_PROFILES)
        self.assertEqual(strong_count, 1)

    def test_plan_scene_sfx_events_prefers_ui_profiles_for_proof(self) -> None:
        job = VideoJob(topic="Proof SFX", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=2.6,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_proof",
                    index=0,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 14% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.6,
                    start_seconds=0.0,
                    cue_texts=["Dashboard", "14% de retention en plus"],
                    proof_mode="result",
                )
            ],
        )
        edit_manifest = EditManifest(
            version="scene_edit_v1",
            scenes=[
                SceneEditPlan(
                    scene_id="scene_proof",
                    scene_index=0,
                    role="body",
                    scene_type="proof",
                    pace_role="proof",
                    camera_pattern="hold_static",
                    camera_intensity="low",
                    transition_in="cut",
                    transition_out="cut",
                    text_style="proof_clean",
                    overlay_style="proof_frame",
                    sfx_profile="hit_soft",
                    bgm_profile="business_clean",
                )
            ],
        )

        decision = plan_scene_sfx_events(job=job, manifest=manifest, edit_manifest=edit_manifest)[0]

        self.assertIn(decision.sfx_selected, {"click_ui_soft", "click_ui_hard", "pop_light"})
        self.assertEqual(decision.selected_reason, "proof_ui_confirmation")

    def test_plan_scene_sfx_events_keeps_explain_sparse(self) -> None:
        job = VideoJob(topic="Explain SFX", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=2.2,
            primary_visual_asset_mode="workflow",
            scenes=[
                _build_scene(
                    scene_id="scene_explain",
                    index=0,
                    role="body",
                    scene_type="workflow",
                    source_text="On relie les etapes sans couper la lecture",
                    visual_asset_mode="workflow",
                    duration_seconds=2.2,
                    start_seconds=0.0,
                    cue_texts=["On relie les etapes sans couper la lecture"],
                )
            ],
        )

        decision = plan_scene_sfx_events(
            job=job,
            manifest=manifest,
            edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
        )[0]

        self.assertIsNone(decision.sfx_selected)
        self.assertIn(decision.skipped_reason, {"scene_too_short", "scene_role_prefers_silence"})

    def test_plan_scene_sfx_events_enforces_min_gap(self) -> None:
        job = VideoJob(
            topic="Gap SFX",
            template="tiktok_ai",
            account="main",
            metadata={"edit_min_sfx_gap_ms": 900},
        )
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=2.4,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_hook",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="Hook clair des la premiere phrase",
                    visual_asset_mode="screenshot",
                    duration_seconds=1.3,
                    start_seconds=0.0,
                    cue_texts=["Hook clair", "des la premiere phrase"],
                ),
                _build_scene(
                    scene_id="scene_proof",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 8% de clic en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=1.7,
                    start_seconds=0.65,
                    cue_texts=["Dashboard", "8% de clic en plus"],
                    proof_mode="result",
                ),
            ],
        )

        decisions = plan_scene_sfx_events(
            job=job,
            manifest=manifest,
            edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
        )

        self.assertIsNotNone(decisions[0].sfx_selected)
        self.assertIsNone(decisions[1].sfx_selected)
        self.assertEqual(decisions[1].skipped_reason, "min_gap_guardrail")

    def test_plan_scene_sfx_events_avoids_profile_repetition(self) -> None:
        job = VideoJob(topic="Repeat SFX", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=6.6,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_01",
                    index=0,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 11% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.2,
                    start_seconds=0.0,
                    cue_texts=["Dashboard", "11% de retention"],
                    proof_mode="result",
                ),
                _build_scene(
                    scene_id="scene_02",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Autre dashboard avec 7% de clic en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.2,
                    start_seconds=2.2,
                    cue_texts=["Autre dashboard", "7% de clic en plus"],
                    proof_mode="result",
                ),
            ],
        )

        decisions = plan_scene_sfx_events(
            job=job,
            manifest=manifest,
            edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
        )

        self.assertIsNotNone(decisions[0].sfx_selected)
        self.assertIsNotNone(decisions[1].sfx_selected)
        self.assertNotEqual(decisions[0].sfx_selected, decisions[1].sfx_selected)

    def test_plan_scene_sfx_events_uses_non_uniform_timing_offsets(self) -> None:
        job = VideoJob(topic="Timing offsets", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=8.4,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_hook",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="3 erreurs qui flinguent ton hook",
                    visual_asset_mode="screenshot",
                    duration_seconds=2.1,
                    start_seconds=0.0,
                    cue_texts=["3 erreurs", "qui flinguent ton hook"],
                    highlight_text="3 erreurs",
                ),
                _build_scene(
                    scene_id="scene_proof",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 14% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.7,
                    start_seconds=2.1,
                    cue_texts=["Dashboard", "14% de retention en plus"],
                    proof_mode="result",
                ),
                _build_scene(
                    scene_id="scene_cta",
                    index=2,
                    role="cta",
                    scene_type="cta",
                    source_text="Commente stack pour recevoir le template",
                    visual_asset_mode="ui",
                    duration_seconds=1.9,
                    start_seconds=4.8,
                    cue_texts=["Commente stack", "pour recevoir le template"],
                ),
            ],
        )

        decisions = plan_scene_sfx_events(
            job=job,
            manifest=manifest,
            edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
        )

        offsets = [decision.timing_offset_ms for decision in decisions if decision.sfx_selected is not None]
        self.assertTrue(offsets)
        self.assertTrue(all(offset is not None for offset in offsets))
        self.assertTrue(any(offset != 0 for offset in offsets))
        self.assertGreater(len(set(offsets)), 1)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is required for audio asset resolution")
    def test_resolve_audio_layers_falls_back_to_alternate_profile(self) -> None:
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=2.6,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_proof",
                    index=0,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 14% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.6,
                    start_seconds=0.0,
                    cue_texts=["Dashboard", "14% de retention en plus"],
                    proof_mode="result",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sfx").mkdir()
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "pop_light.wav")

            job = VideoJob(
                topic="Fallback SFX",
                template="tiktok_ai",
                account="main",
                metadata={"edit_audio_roots": [str(root)]},
            )
            audio_plan = asyncio.run(
                _resolve_audio_layers(
                    job=job,
                    manifest=manifest,
                    edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
                )
            )

        self.assertEqual(audio_plan.sfx_layer_count, 1)
        self.assertEqual(audio_plan.sfx_events[0].profile, "pop_light")
        self.assertEqual(audio_plan.sfx_decisions[0].fallback_from, "click_ui_soft")
        self.assertTrue(audio_plan.sfx_decisions[0].mixed)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is required for audio asset resolution")
    def test_resolve_audio_layers_rotates_assets_across_consecutive_ui_scenes(self) -> None:
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=4.8,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_proof_01",
                    index=0,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 11% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.4,
                    start_seconds=0.0,
                    cue_texts=["Dashboard", "11% de retention en plus"],
                    proof_mode="result",
                ),
                _build_scene(
                    scene_id="scene_proof_02",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Autre dashboard avec 9% de clic en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.4,
                    start_seconds=2.4,
                    cue_texts=["Autre dashboard", "9% de clic en plus"],
                    proof_mode="result",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            variants = root / "variants"
            variants.mkdir()
            shutil.copy2(TEST_AUDIO_PATH, variants / "click_a.wav")
            shutil.copy2(TEST_AUDIO_PATH, variants / "click_b.wav")

            job = VideoJob(
                topic="Asset rotation",
                template="tiktok_ai",
                account="main",
                metadata={
                    "edit_audio_roots": [str(root)],
                    "edit_sfx_same_family_cooldown_scenes": 0,
                    "edit_audio_profiles": {
                        "sfx": {
                            "click_ui_soft": [
                                str((variants / "click_a.wav").resolve()),
                                str((variants / "click_b.wav").resolve()),
                            ],
                            "click_ui_hard": [
                                str((variants / "click_a.wav").resolve()),
                                str((variants / "click_b.wav").resolve()),
                            ],
                        }
                    },
                },
            )
            audio_plan = asyncio.run(
                _resolve_audio_layers(
                    job=job,
                    manifest=manifest,
                    edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
                )
            )

        self.assertEqual(audio_plan.sfx_layer_count, 2)
        self.assertNotEqual(audio_plan.sfx_events[0].asset_path, audio_plan.sfx_events[1].asset_path)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is required for audio asset resolution")
    def test_resolve_audio_layers_modulates_gain_by_scene_role(self) -> None:
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=9.0,
            primary_visual_asset_mode="screenshot",
            scenes=[
                _build_scene(
                    scene_id="scene_hook",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="3 erreurs qui flinguent ton hook",
                    visual_asset_mode="screenshot",
                    duration_seconds=2.1,
                    start_seconds=0.0,
                    cue_texts=["3 erreurs", "qui flinguent ton hook"],
                    highlight_text="3 erreurs",
                ),
                _build_scene(
                    scene_id="scene_proof",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="Dashboard avec 14% de retention en plus",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.8,
                    start_seconds=2.1,
                    cue_texts=["Dashboard", "14% de retention en plus"],
                    proof_mode="result",
                ),
                _build_scene(
                    scene_id="scene_explain",
                    index=2,
                    role="body",
                    scene_type="workflow",
                    source_text="On relie le workflow en trois etapes simples",
                    visual_asset_mode="workflow",
                    duration_seconds=3.0,
                    start_seconds=4.9,
                    cue_texts=["On relie le workflow", "en trois etapes", "simples"],
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "sfx").mkdir()
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "hit_soft.wav")
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "click_ui_soft.wav")
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "click_ui_hard.wav")
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "whoosh_soft.wav")

            job = VideoJob(
                topic="Gain strategy",
                template="tiktok_ai",
                account="main",
                metadata={"edit_audio_roots": [str(root)]},
            )
            audio_plan = asyncio.run(
                _resolve_audio_layers(
                    job=job,
                    manifest=manifest,
                    edit_manifest=build_edit_manifest(job, scene_manifest=manifest),
                )
            )

        gains_by_role = {event.scene_role: event.gain_db for event in audio_plan.sfx_events}
        self.assertGreater(gains_by_role["hook"], gains_by_role["proof"])
        self.assertGreater(gains_by_role["hook"], gains_by_role["explain"])
        self.assertGreaterEqual(len(set(gains_by_role.values())), 3)


if __name__ == "__main__":
    unittest.main()
