from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from pipeline.assembler import assemble_video_step
from pipeline.scene_planner import SceneManifest, SceneSegment, save_scene_manifest, load_scene_manifest
from pipeline.visual_gen import generate_visual_step
from pipeline.visual_manifest import load_visual_manifest
from schemas import VideoJob

TEST_AUDIO_PATH = (Path(__file__).resolve().parents[1] / "test_fr.wav").resolve()


def _build_scene(scene_index: int, *, start_seconds: float, end_seconds: float) -> SceneSegment:
    scene_number = scene_index + 1
    return SceneSegment(
        scene_id=f"scene_{scene_number:02d}",
        index=scene_index,
        role="hook" if scene_index == 0 else "body",
        scene_type="proof" if scene_index % 2 else "explain",
        source_text=f"Scene {scene_number} render integrity check",
        title_text=f"Scene {scene_number}",
        highlight_text="render integrity",
        keywords=["render", "integrity", str(scene_number)],
        word_count=4,
        proof_mode="generic",
        visual_asset_mode="dashboard" if scene_index % 2 else "screenshot",
        motion_effect="static",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=round(start_seconds, 3),
        end_seconds=round(end_seconds, 3),
        duration_seconds=round(max(0.05, end_seconds - start_seconds), 3),
    )


def _build_manifest(scene_count: int, *, total_duration_seconds: float = 2.304535) -> SceneManifest:
    scenes: list[SceneSegment] = []
    cursor = 0.0
    step = total_duration_seconds / scene_count
    for scene_index in range(scene_count):
        end_seconds = total_duration_seconds if scene_index == scene_count - 1 else cursor + step
        scenes.append(
            _build_scene(
                scene_index,
                start_seconds=cursor,
                end_seconds=end_seconds,
            )
        )
        cursor = end_seconds
    return SceneManifest(
        version="multi_scene_v2",
        strategy="test_integrity",
        total_duration_seconds=round(total_duration_seconds, 3),
        primary_visual_asset_mode="dashboard",
        scenes=scenes,
    )


class MultiSceneArtifactIntegrityTests(unittest.TestCase):
    def test_visual_generation_keeps_unique_scene_assets_for_2_3_and_5_scenes(self) -> None:
        for scene_count in (2, 3, 5):
            with self.subTest(scene_count=scene_count):
                with tempfile.TemporaryDirectory(prefix=f"multiscene_assets_{scene_count}_") as temp_dir:
                    job_dir = Path(temp_dir)
                    job = VideoJob(
                        topic="General content render test",
                        template="tiktok_ai",
                        account="main",
                    )
                    save_scene_manifest(job, job_dir, _build_manifest(scene_count))

                    asyncio.run(generate_visual_step(job, job_dir))

                    visual_manifest = load_visual_manifest(job)
                    updated_manifest = load_scene_manifest(job)
                    self.assertIsNotNone(visual_manifest)
                    self.assertIsNotNone(updated_manifest)
                    assert visual_manifest is not None
                    assert updated_manifest is not None

                    asset_paths = [Path(scene.asset_path) for scene in visual_manifest.scenes]
                    self.assertEqual(len(asset_paths), scene_count)
                    self.assertEqual(len({path.resolve() for path in asset_paths}), scene_count)

                    for scene in updated_manifest.scenes:
                        self.assertIsNotNone(scene.asset_path)
                        assert scene.asset_path is not None
                        asset_path = Path(scene.asset_path)
                        self.assertTrue(asset_path.exists())
                        self.assertEqual(asset_path.name.split(".")[0], f"scene_{scene.index + 1:02d}")

    def test_assembler_keeps_unique_scene_clips_for_five_scenes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="multiscene_assembly_") as temp_dir:
            job_dir = Path(temp_dir)
            job = VideoJob(
                topic="General content render test",
                template="tiktok_ai",
                account="main",
            )
            job.set_voice_audio_path(str(TEST_AUDIO_PATH))
            save_scene_manifest(job, job_dir, _build_manifest(5))

            asyncio.run(generate_visual_step(job, job_dir))
            asyncio.run(assemble_video_step(job, job_dir))

            updated_manifest = load_scene_manifest(job)
            self.assertIsNotNone(updated_manifest)
            assert updated_manifest is not None

            clip_paths = [Path(scene.clip_path) for scene in updated_manifest.scenes if scene.clip_path]
            self.assertEqual(len(clip_paths), 5)
            self.assertEqual(len({path.resolve() for path in clip_paths}), 5)
            for scene, clip_path in zip(updated_manifest.scenes, clip_paths, strict=True):
                self.assertTrue(clip_path.exists())
                self.assertEqual(clip_path.name, f"scene_{scene.index + 1:02d}.mp4")

            final_video_path = Path(job.require_file_path("final_video"))
            self.assertTrue(final_video_path.exists())
            self.assertGreater(final_video_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
