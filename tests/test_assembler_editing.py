from __future__ import annotations

from pathlib import Path
import unittest

from pipeline.assembler import (
    AudioLayerSpec,
    SceneRenderPlan,
    _build_edited_scene_clip_command,
    _build_mux_command,
    _build_scene_caption_ass,
)
from pipeline.scene_planner import SceneSegment, SubtitleCue
from schemas import VideoJob


def _build_scene() -> SceneSegment:
    return SceneSegment(
        scene_id="scene_01",
        index=0,
        role="hook",
        scene_type="hook",
        source_text="Ton hook manque d'impact concret",
        title_text="Hook",
        highlight_text="impact concret",
        keywords=["hook", "impact", "concret"],
        word_count=5,
        proof_mode="generic",
        visual_asset_mode="screenshot",
        motion_effect="zoom_in",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=0.0,
        end_seconds=2.4,
        duration_seconds=2.4,
        cue_timeline=[
            SubtitleCue(
                index=1,
                start_seconds=0.0,
                end_seconds=1.2,
                duration_seconds=1.2,
                text="Ton hook manque d'impact",
                word_count=4,
            ),
            SubtitleCue(
                index=2,
                start_seconds=1.2,
                end_seconds=2.4,
                duration_seconds=1.2,
                text="reste concret des le debut",
                word_count=5,
            ),
        ],
    )


class AssemblerEditingTests(unittest.TestCase):
    def test_build_scene_caption_ass_highlights_keywords(self) -> None:
        scene = _build_scene()
        plan = SceneRenderPlan(
            pace_role="explain",
            camera_pattern="push_in_soft",
            camera_intensity="medium",
            transition_in="cut",
            transition_out="blur_out",
            text_style="keyword_emphasis",
            overlay_style="subtle_vignette",
            sfx_profile="whoosh_soft",
            bgm_profile="business_clean",
        )

        payload = _build_scene_caption_ass(scene, plan)

        self.assertIn("[V4+ Styles]", payload)
        self.assertIn(r"{\b1\1c&H0038C8FF&}", payload)
        self.assertIn(r"\N", payload)

    def test_build_edited_scene_clip_command_uses_filter_complex(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        scene = _build_scene()
        plan = SceneRenderPlan(
            pace_role="hook",
            camera_pattern="impact_zoom",
            camera_intensity="high",
            transition_in="flash_cut",
            transition_out="blur_out",
            text_style="hook_focus",
            overlay_style="light_glow",
            sfx_profile="hit_soft",
            bgm_profile="tension_light",
        )

        command = _build_edited_scene_clip_command(
            job=job,
            scene=scene,
            plan=plan,
            asset_path=Path("scene_01.ppm"),
            caption_path=Path("scene_01.ass"),
            output_path=Path("scene_01.mp4"),
        )

        serialized = " ".join(command)
        self.assertIn("-filter_complex", command)
        self.assertIn("gblur=sigma=18", serialized)
        self.assertIn("fade=t=in:st=0:d=0.080:color=white", serialized)
        self.assertIn("subtitles='scene_01.ass'", serialized)

    def test_build_mux_command_with_audio_layers_switches_to_filter_complex(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        command = _build_mux_command(
            job=job,
            source_video_path=Path("concat.mp4"),
            voice_audio_path=Path("voice.mp3"),
            subtitles_path=None,
            audio_layers=[
                AudioLayerSpec(
                    kind="bgm",
                    profile="business_clean",
                    path=Path("bgm.mp3"),
                    start_seconds=0.0,
                    duration_seconds=4.0,
                    volume=0.18,
                    loop=True,
                ),
                AudioLayerSpec(
                    kind="sfx",
                    profile="hit_soft",
                    path=Path("hit.wav"),
                    start_seconds=1.1,
                    duration_seconds=0.4,
                    volume=0.9,
                    loop=False,
                ),
            ],
            output_path=Path("final.mp4"),
        )

        serialized = " ".join(command)
        self.assertIn("-filter_complex", command)
        self.assertIn("amix=inputs=3", serialized)
        self.assertIn("alimiter=limit=0.92", serialized)
        self.assertIn("-stream_loop", command)


if __name__ == "__main__":
    unittest.main()
