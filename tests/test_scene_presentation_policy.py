from __future__ import annotations

import unittest

from pipeline.scene_planner import build_scene_manifest
from schemas import VideoJob


class ScenePresentationPolicyTests(unittest.TestCase):
    def test_build_scene_manifest_rewrites_overlay_and_caps_overlay_ratio(self) -> None:
        job = VideoJob(
            topic="Automation proof",
            template="tiktok_ai",
            account="main",
            metadata={
                "scene_min_duration_seconds": 0.8,
                "script_sections": {
                    "hook": "3 erreurs qui rendent ton automatisation inutile",
                    "points": [
                        "Montre le workflow sans surcharger l'ecran",
                        "Ajoute une preuve avec 27% de gain visible",
                        "Explique seulement l'etape critique",
                    ],
                    "cta": "Commente STACK pour le template",
                },
                "subtitle_cues": [
                    {
                        "index": 1,
                        "start_seconds": 0.0,
                        "end_seconds": 1.0,
                        "duration_seconds": 1.0,
                        "text": "3 erreurs qui rendent ton automatisation inutile",
                        "word_count": 6,
                    },
                    {
                        "index": 2,
                        "start_seconds": 1.0,
                        "end_seconds": 2.0,
                        "duration_seconds": 1.0,
                        "text": "Montre le workflow sans surcharger l'ecran",
                        "word_count": 6,
                    },
                    {
                        "index": 3,
                        "start_seconds": 2.0,
                        "end_seconds": 3.0,
                        "duration_seconds": 1.0,
                        "text": "Ajoute une preuve avec 27% de gain visible",
                        "word_count": 8,
                    },
                    {
                        "index": 4,
                        "start_seconds": 3.0,
                        "end_seconds": 4.0,
                        "duration_seconds": 1.0,
                        "text": "Explique seulement l'etape critique",
                        "word_count": 4,
                    },
                    {
                        "index": 5,
                        "start_seconds": 4.0,
                        "end_seconds": 5.0,
                        "duration_seconds": 1.0,
                        "text": "Commente STACK pour le template",
                        "word_count": 5,
                    },
                ],
            },
        )

        manifest = build_scene_manifest(job)

        overlay_scenes = [scene for scene in manifest.scenes if scene.overlay_text]
        self.assertEqual(len(manifest.scenes), 5)
        self.assertEqual(len(overlay_scenes), 2)
        self.assertTrue(all(scene.visual_intensity_level is not None for scene in manifest.scenes))
        self.assertEqual(manifest.scenes[0].overlay_role, "structure")
        self.assertEqual(manifest.scenes[0].overlay_text, "3 erreurs")
        self.assertTrue(all(len(scene.overlay_text.split()) <= 6 for scene in overlay_scenes))
        self.assertTrue(all(scene.subtitle_text for scene in manifest.scenes))
        self.assertTrue(any(scene.overlay_role == "proof" and scene.overlay_text for scene in manifest.scenes))

    def test_build_scene_manifest_keeps_automation_video_in_tighter_visual_family(self) -> None:
        job = VideoJob(
            topic="Automation proof",
            template="tiktok_ai",
            account="main",
            metadata={
                "script_profile": "automation",
                "scene_min_duration_seconds": 0.8,
                "script_sections": {
                    "hook": "3 erreurs qui rendent ton automatisation inutile",
                    "points": [
                        "Montre le workflow sans surcharger l'ecran",
                        "Ajoute une preuve avec 27% de gain visible",
                        "Explique l'etape critique sur le meme workflow",
                    ],
                    "cta": "Commente STACK pour le workflow complet",
                },
                "subtitle_cues": [
                    {
                        "index": 1,
                        "start_seconds": 0.0,
                        "end_seconds": 1.0,
                        "duration_seconds": 1.0,
                        "text": "3 erreurs qui rendent ton automatisation inutile",
                        "word_count": 6,
                    },
                    {
                        "index": 2,
                        "start_seconds": 1.0,
                        "end_seconds": 2.0,
                        "duration_seconds": 1.0,
                        "text": "Montre le workflow sans surcharger l'ecran",
                        "word_count": 6,
                    },
                    {
                        "index": 3,
                        "start_seconds": 2.0,
                        "end_seconds": 3.0,
                        "duration_seconds": 1.0,
                        "text": "Ajoute une preuve avec 27% de gain visible",
                        "word_count": 8,
                    },
                    {
                        "index": 4,
                        "start_seconds": 3.0,
                        "end_seconds": 4.0,
                        "duration_seconds": 1.0,
                        "text": "Explique l'etape critique sur le meme workflow",
                        "word_count": 7,
                    },
                    {
                        "index": 5,
                        "start_seconds": 4.0,
                        "end_seconds": 5.0,
                        "duration_seconds": 1.0,
                        "text": "Commente STACK pour le workflow complet",
                        "word_count": 6,
                    },
                ],
            },
        )

        manifest = build_scene_manifest(job)
        modes = {scene.visual_asset_mode for scene in manifest.scenes}
        proof_scenes = [scene for scene in manifest.scenes if scene.scene_type == "proof"]

        self.assertNotIn("ui", modes)
        self.assertLessEqual(len(modes), 2)
        self.assertTrue(proof_scenes)
        self.assertTrue(all(scene.primary_focus == "visual_proof" for scene in proof_scenes))
        self.assertEqual(manifest.scenes[0].visual_asset_mode, "workflow")
        self.assertEqual(proof_scenes[0].visual_asset_mode, "dashboard")


if __name__ == "__main__":
    unittest.main()
