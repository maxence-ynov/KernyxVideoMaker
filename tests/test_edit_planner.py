from __future__ import annotations

from pathlib import Path
import shutil
import unittest

from pipeline.edit_planner import (
    EditManifest,
    SceneEditPlan,
    build_edit_manifest,
    load_edit_manifest,
    save_edit_manifest,
)
from pipeline.scene_planner import SceneManifest, SceneSegment
from schemas import VideoJob


def _build_scene(
    *,
    scene_id: str,
    index: int,
    role: str,
    scene_type: str,
    source_text: str,
    visual_asset_mode: str,
    duration_seconds: float = 2.4,
    start_seconds: float = 0.0,
) -> SceneSegment:
    return SceneSegment(
        scene_id=scene_id,
        index=index,
        role=role,
        scene_type=scene_type,
        source_text=source_text,
        title_text=source_text,
        highlight_text=source_text,
        keywords=["analytics", "hook", "proof", "workflow"],
        word_count=max(1, len(source_text.split())),
        proof_mode="generic",
        visual_asset_mode=visual_asset_mode,
        motion_effect="static",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=start_seconds,
        end_seconds=round(start_seconds + duration_seconds, 3),
        duration_seconds=duration_seconds,
    )


class EditPlannerTests(unittest.TestCase):
    def test_build_edit_manifest_assigns_scene_role_specific_profiles(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        manifest = SceneManifest(
            version="multi_scene_v2",
            strategy="test",
            total_duration_seconds=9.0,
            primary_visual_asset_mode="dashboard",
            scenes=[
                _build_scene(
                    scene_id="scene_01",
                    index=0,
                    role="hook",
                    scene_type="hook",
                    source_text="Ton hook manque d'impact",
                    visual_asset_mode="screenshot",
                    duration_seconds=2.2,
                    start_seconds=0.0,
                ),
                _build_scene(
                    scene_id="scene_02",
                    index=1,
                    role="body",
                    scene_type="proof",
                    source_text="3 preuves concretes avec analytics",
                    visual_asset_mode="dashboard",
                    duration_seconds=2.8,
                    start_seconds=2.2,
                ),
                _build_scene(
                    scene_id="scene_03",
                    index=2,
                    role="body",
                    scene_type="workflow",
                    source_text="Explique le workflow sans perdre la lecture",
                    visual_asset_mode="workflow",
                    duration_seconds=2.6,
                    start_seconds=5.0,
                ),
                _build_scene(
                    scene_id="scene_04",
                    index=3,
                    role="cta",
                    scene_type="cta",
                    source_text="Si tu veux le stack exact je te le donne",
                    visual_asset_mode="ui",
                    duration_seconds=2.0,
                    start_seconds=7.6,
                ),
            ],
        )

        edit_manifest = build_edit_manifest(job, scene_manifest=manifest)

        self.assertEqual(edit_manifest.version, "scene_edit_v1")
        hook = edit_manifest.get_scene_plan("scene_01")
        proof = edit_manifest.get_scene_plan("scene_02")
        explain = edit_manifest.get_scene_plan("scene_03")
        cta = edit_manifest.get_scene_plan("scene_04")

        self.assertIsNotNone(hook)
        self.assertIsNotNone(proof)
        self.assertIsNotNone(explain)
        self.assertIsNotNone(cta)
        assert hook is not None and proof is not None and explain is not None and cta is not None

        self.assertEqual(hook.pace_role, "hook")
        self.assertEqual(hook.text_style, "hook_focus")
        self.assertEqual(hook.bgm_profile, "tension_light")
        self.assertIn(hook.camera_pattern, {"impact_zoom", "push_in_fast", "micro_shake_light"})

        self.assertEqual(proof.pace_role, "proof")
        self.assertEqual(proof.text_style, "proof_clean")
        self.assertEqual(proof.overlay_style, "proof_frame")
        self.assertEqual(proof.bgm_profile, "business_clean")

        self.assertEqual(explain.pace_role, "explain")
        self.assertEqual(explain.text_style, "keyword_emphasis")
        self.assertEqual(explain.overlay_style, "subtle_vignette")

        self.assertEqual(cta.pace_role, "cta")
        self.assertEqual(cta.overlay_style, "cta_band")
        self.assertEqual(cta.transition_in, "dip_to_black_soft")

    def test_edit_manifest_round_trip_preserves_scene_decisions(self) -> None:
        original = EditManifest(
            version="scene_edit_v1",
            scenes=[
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
                    sfx_profile="hit_soft",
                    bgm_profile="business_clean",
                )
            ],
        )

        restored = EditManifest.from_mapping(original.to_metadata())

        self.assertEqual(restored.scenes[0].camera_pattern, "hold_static")
        self.assertEqual(restored.scenes[0].overlay_style, "proof_frame")
        self.assertEqual(restored.scenes[0].bgm_profile, "business_clean")

    def test_save_and_load_edit_manifest_updates_job_metadata(self) -> None:
        manifest = EditManifest(
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
                    sfx_profile="hit_soft",
                    bgm_profile="tension_light",
                )
            ],
        )
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")

        job_dir = (Path(__file__).resolve().parents[1] / "tmp" / "test_edit_planner_manifest").resolve()
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            manifest_path = save_edit_manifest(job, job_dir, manifest)
            loaded = load_edit_manifest(job)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

        self.assertTrue(manifest_path.name.endswith("06_edit_manifest.json"))
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(job.metadata["edit_manifest_scene_count"], 1)
        self.assertEqual(loaded.scenes[0].text_style, "hook_focus")


if __name__ == "__main__":
    unittest.main()
