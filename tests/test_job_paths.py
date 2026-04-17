from __future__ import annotations

import unittest

from job_paths import build_scene_asset_filename, build_scene_clip_filename


class JobPathsSceneNamingTests(unittest.TestCase):
    def test_build_scene_asset_filename_uses_zero_based_indices(self) -> None:
        self.assertEqual(build_scene_asset_filename(0, ".ppm"), "scene_01.ppm")
        self.assertEqual(build_scene_asset_filename(1, "ppm"), "scene_02.ppm")
        self.assertEqual(build_scene_asset_filename(4, ".jpg"), "scene_05.jpg")

    def test_build_scene_clip_filename_uses_zero_based_indices(self) -> None:
        self.assertEqual(build_scene_clip_filename(0), "scene_01.mp4")
        self.assertEqual(build_scene_clip_filename(1), "scene_02.mp4")
        self.assertEqual(build_scene_clip_filename(4), "scene_05.mp4")

    def test_negative_scene_indices_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_scene_asset_filename(-1, ".ppm")
        with self.assertRaises(ValueError):
            build_scene_clip_filename(-1)


if __name__ == "__main__":
    unittest.main()
