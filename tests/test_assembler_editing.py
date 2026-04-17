from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import tempfile
import unittest

from pipeline.assembler import (
    AudioLayerSpec,
    VoiceWordTiming,
    _build_scene_story_overlay_plan,
    assemble_video_step,
    _build_audio_layer_filter,
    SceneRenderPlan,
    _build_edited_scene_clip_command,
    _build_mux_command,
    _build_scene_caption_ass,
    _resolve_caption_settings,
    compute_caption_emphasis,
    _resolve_audio_layers,
)
from pipeline.edit_planner import EditManifest, SceneEditPlan, save_edit_manifest
from pipeline.scene_planner import SceneManifest
from pipeline.scene_planner import SceneSegment, SubtitleCue, save_scene_manifest
from pipeline.visual_gen import generate_visual_step
from pipeline.visual_manifest import SceneVisualAsset
from schemas import VideoJob

TEST_AUDIO_PATH = (Path(__file__).resolve().parents[1] / "test_fr.wav").resolve()


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


def _build_manifest() -> SceneManifest:
    first_scene = _build_scene()
    second_scene = SceneSegment(
        scene_id="scene_02",
        index=1,
        role="body",
        scene_type="proof",
        source_text="Preuve visible avec 2 chiffres concrets",
        title_text="Proof",
        highlight_text="2 chiffres concrets",
        keywords=["preuve", "chiffres", "concrets"],
        word_count=5,
        proof_mode="result",
        visual_asset_mode="dashboard",
        motion_effect="pan_right",
        transition_effect="cut",
        transition_duration_seconds=0.0,
        start_seconds=2.4,
        end_seconds=5.0,
        duration_seconds=2.6,
        cue_timeline=[
            SubtitleCue(
                index=3,
                start_seconds=2.4,
                end_seconds=3.6,
                duration_seconds=1.2,
                text="Preuve visible avec 2 chiffres",
                word_count=5,
            ),
            SubtitleCue(
                index=4,
                start_seconds=3.6,
                end_seconds=5.0,
                duration_seconds=1.4,
                text="sur le meme ecran",
                word_count=4,
            ),
        ],
    )
    return SceneManifest(
        version="multi_scene_v2",
        strategy="test",
        total_duration_seconds=5.0,
        primary_visual_asset_mode="screenshot",
        scenes=[first_scene, second_scene],
    )


def _build_edit_manifest() -> EditManifest:
    return EditManifest(
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
            ),
            SceneEditPlan(
                scene_id="scene_02",
                scene_index=1,
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
                bgm_profile="tension_light",
            ),
        ],
    )


def _build_voice_word_timing(words: list[str], *, start_seconds: float = 0.0, step_seconds: float = 0.24) -> list[VoiceWordTiming]:
    timing: list[VoiceWordTiming] = []
    elapsed = start_seconds
    for index, word in enumerate(words, start=1):
        timing.append(
            VoiceWordTiming(
                index=index,
                text=word,
                start_seconds=round(elapsed, 3),
                end_seconds=round(elapsed + step_seconds, 3),
            )
        )
        elapsed += step_seconds
    return timing


class AssemblerEditingTests(unittest.TestCase):
    def test_build_scene_caption_ass_uses_premium_debug_and_emphasis(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        scene = _build_manifest().scenes[1]
        plan = SceneRenderPlan(
            pace_role="proof",
            camera_pattern="hold_static",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="proof_clean",
            overlay_style="proof_frame",
            sfx_profile="click_ui_soft",
            bgm_profile="business_clean",
        )
        settings = _resolve_caption_settings(job)

        payload, debug = _build_scene_caption_ass(
            job=job,
            scene=scene,
            plan=plan,
            settings=settings,
            voice_word_timing=[],
        )

        self.assertIn("[V4+ Styles]", payload)
        self.assertEqual(debug["caption_mode"], "premium_ass_v2")
        self.assertEqual(debug["text_style"], "proof_clean")
        self.assertIn(debug["timing_source"], {"srt_file", "subtitle_cues", "proportional_fallback"})
        chunk_emphasis = [token for chunk in debug["chunks"] for token in chunk["emphasis_tokens"]]
        self.assertIn("2", chunk_emphasis)

    def test_build_scene_caption_ass_uses_word_boundaries_when_available(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        scene = _build_scene()
        plan = SceneRenderPlan(
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
        settings = _resolve_caption_settings(job)

        _, debug = _build_scene_caption_ass(
            job=job,
            scene=scene,
            plan=plan,
            settings=settings,
            voice_word_timing=_build_voice_word_timing(
                ["Ton", "hook", "manque", "impact", "reste", "concret", "des", "le", "debut"],
                step_seconds=0.18,
            ),
        )

        self.assertEqual(debug["timing_source"], "word_boundaries")
        self.assertGreaterEqual(len(debug["chunks"]), 2)

    def test_caption_chunking_stays_mobile_first(self) -> None:
        job = VideoJob(
            topic="Explain",
            template="tiktok_ai",
            account="main",
            metadata={
                "edit_caption_max_words_per_chunk": 5,
                "edit_caption_max_chars_per_line": 16,
            },
        )
        scene = SceneSegment(
            scene_id="scene_long",
            index=0,
            role="body",
            scene_type="explain",
            source_text="Ce workflow garde la lecture claire meme quand la phrase devient plus longue",
            title_text="Workflow",
            highlight_text="lecture claire",
            keywords=["workflow", "lecture", "claire"],
            word_count=11,
            proof_mode="generic",
            visual_asset_mode="workflow",
            motion_effect="static",
            transition_effect="cut",
            transition_duration_seconds=0.0,
            start_seconds=0.0,
            end_seconds=3.0,
            duration_seconds=3.0,
            cue_timeline=[
                SubtitleCue(
                    index=1,
                    start_seconds=0.0,
                    end_seconds=3.0,
                    duration_seconds=3.0,
                    text="Ce workflow garde la lecture claire meme quand la phrase devient plus longue",
                    word_count=11,
                )
            ],
        )
        plan = SceneRenderPlan(
            pace_role="explain",
            camera_pattern="push_in_soft",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="explain_readable",
            overlay_style="subtle_vignette",
            sfx_profile="none",
            bgm_profile="business_clean",
        )

        _, debug = _build_scene_caption_ass(
            job=job,
            scene=scene,
            plan=plan,
            settings=_resolve_caption_settings(job),
            voice_word_timing=[],
        )

        self.assertGreaterEqual(len(debug["chunks"]), 2)
        self.assertTrue(all(chunk["word_count"] <= 5 for chunk in debug["chunks"]))
        self.assertTrue(all(chunk["line_count"] <= 2 for chunk in debug["chunks"]))

    def test_compute_caption_emphasis_prioritizes_numbers_results_and_tools(self) -> None:
        scene = SceneSegment(
            scene_id="scene_tool",
            index=0,
            role="body",
            scene_type="proof",
            source_text="Zapier sort 27% de resultat visible",
            title_text="Proof",
            highlight_text="27% resultat",
            keywords=["zapier", "resultat", "visible"],
            word_count=5,
            proof_mode="result",
            visual_asset_mode="dashboard",
            motion_effect="static",
            transition_effect="cut",
            transition_duration_seconds=0.0,
            start_seconds=0.0,
            end_seconds=2.0,
            duration_seconds=2.0,
        )
        plan = SceneRenderPlan(
            pace_role="proof",
            camera_pattern="hold_static",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="proof_clean",
            overlay_style="proof_frame",
            sfx_profile="click_ui_soft",
            bgm_profile="business_clean",
        )

        emphasis_tokens, skipped_reason = compute_caption_emphasis(
            "Zapier sort 27% de resultat visible",
            scene=scene,
            plan=plan,
        )

        self.assertIsNone(skipped_reason)
        self.assertIn("Zapier", emphasis_tokens)
        self.assertIn("27%", emphasis_tokens)

    def test_compute_caption_emphasis_skips_weak_words(self) -> None:
        scene = SceneSegment(
            scene_id="scene_generic",
            index=0,
            role="body",
            scene_type="explain",
            source_text="tu peux faire des choses avec le truc",
            title_text="Generic",
            highlight_text="",
            keywords=[],
            word_count=8,
            proof_mode="generic",
            visual_asset_mode="workflow",
            motion_effect="static",
            transition_effect="cut",
            transition_duration_seconds=0.0,
            start_seconds=0.0,
            end_seconds=2.0,
            duration_seconds=2.0,
        )
        plan = SceneRenderPlan(
            pace_role="explain",
            camera_pattern="hold_static",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="explain_readable",
            overlay_style="none",
            sfx_profile="none",
            bgm_profile="business_clean",
        )

        emphasis_tokens, skipped_reason = compute_caption_emphasis(
            "tu peux faire des choses avec le truc",
            scene=scene,
            plan=plan,
        )

        self.assertEqual(emphasis_tokens, [])
        self.assertIn(skipped_reason, {"below_threshold", "no_candidate"})

    def test_build_scene_caption_ass_keeps_working_without_highlight_tokens(self) -> None:
        job = VideoJob(topic="Explain", template="tiktok_ai", account="main")
        scene = SceneSegment(
            scene_id="scene_plain",
            index=0,
            role="body",
            scene_type="explain",
            source_text="Tu peux faire des choses avec le truc",
            title_text="Plain",
            highlight_text="",
            keywords=[],
            word_count=8,
            proof_mode="generic",
            visual_asset_mode="workflow",
            motion_effect="static",
            transition_effect="cut",
            transition_duration_seconds=0.0,
            start_seconds=0.0,
            end_seconds=2.4,
            duration_seconds=2.4,
            cue_timeline=[
                SubtitleCue(
                    index=1,
                    start_seconds=0.0,
                    end_seconds=2.4,
                    duration_seconds=2.4,
                    text="Tu peux faire des choses avec le truc",
                    word_count=8,
                )
            ],
        )
        plan = SceneRenderPlan(
            pace_role="explain",
            camera_pattern="hold_static",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="explain_readable",
            overlay_style="none",
            sfx_profile="none",
            bgm_profile="business_clean",
        )

        payload, debug = _build_scene_caption_ass(
            job=job,
            scene=scene,
            plan=plan,
            settings=_resolve_caption_settings(job),
            voice_word_timing=[],
        )

        self.assertIn("Dialogue:", payload)
        self.assertTrue(all(not chunk["emphasis_tokens"] for chunk in debug["chunks"]))

    def test_build_edited_scene_clip_command_uses_filter_complex(self) -> None:
        job = VideoJob(topic="Montage", template="tiktok_ai", account="main")
        scene = _build_scene()
        scene_visual = SceneVisualAsset(
            scene_id=scene.scene_id,
            scene_index=scene.index,
            asset_path="scene_01.ppm",
            asset_type="image",
            source_kind="generated",
            selected_by="generated",
            asset_origin_path="scene_01.ppm",
            scene_title="Ton automation bug",
            scene_keywords=("automation", "impact"),
            visual_asset_used="scene_01.ppm",
            overlay_applied=("title_text", "keyword_text", "highlight_box", "focus_arrow", "impact_zoom"),
        )
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
            scene_visual=scene_visual,
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
        self.assertIn("drawtext=", serialized)
        self.assertIn("text='Ton automation bug'", serialized)

    def test_scene_story_overlay_plan_prefers_visual_proof_when_asset_is_credible(self) -> None:
        scene = SceneSegment(
            scene_id="scene_proof",
            index=1,
            role="body",
            scene_type="proof",
            source_text="La preuve montre 27% de gain concret",
            title_text="Proof",
            highlight_text="27% de gain concret",
            keywords=["preuve", "gain", "analytics"],
            word_count=6,
            proof_mode="result",
            visual_asset_mode="dashboard",
            motion_effect="static",
            transition_effect="cut",
            transition_duration_seconds=0.0,
            start_seconds=0.0,
            end_seconds=2.2,
            duration_seconds=2.2,
            overlay_role="proof",
            overlay_text="27% de gain",
            subtitle_text="La preuve montre 27% de gain concret",
            visual_intensity_level="high",
            primary_focus="visual_proof",
        )
        scene_visual = SceneVisualAsset(
            scene_id=scene.scene_id,
            scene_index=scene.index,
            asset_path="scene_proof.ppm",
            asset_type="image",
            source_kind="catalog",
            selected_by="catalog_match",
            asset_origin_path="scene_proof.ppm",
            visual_asset_mode="dashboard",
            scene_title="27% de gain",
            scene_keywords=("analytics", "gain"),
            visual_asset_used="scene_proof.ppm",
            overlay_applied=("title_text",),
        )
        plan = SceneRenderPlan(
            pace_role="proof",
            camera_pattern="hold_static",
            camera_intensity="low",
            transition_in="cut",
            transition_out="cut",
            text_style="proof_clean",
            overlay_style="proof_frame",
            sfx_profile="click_ui_soft",
            bgm_profile="business_clean",
        )

        story_plan = _build_scene_story_overlay_plan(
            scene=scene,
            scene_visual=scene_visual,
            plan=plan,
        )

        self.assertEqual(story_plan.title_text, "")
        self.assertEqual(story_plan.overlay_applied, ())

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

    def test_build_mux_command_ducks_bgm_under_voice(self) -> None:
        job = VideoJob(
            topic="Montage",
            template="tiktok_ai",
            account="main",
            metadata={
                "edit_ducking_threshold": 0.08,
                "edit_ducking_ratio": 8,
                "edit_ducking_attack_ms": 15,
                "edit_ducking_release_ms": 180,
            },
        )

        command = _build_mux_command(
            job=job,
            source_video_path=Path("concat.mp4"),
            voice_audio_path=Path("voice.mp3"),
            subtitles_path=None,
            audio_layers=[
                AudioLayerSpec(
                    kind="bgm",
                    profile="tension_light",
                    path=Path("bgm.mp3"),
                    start_seconds=0.0,
                    duration_seconds=5.0,
                    volume=0.18,
                    loop=True,
                )
            ],
            output_path=Path("final.mp4"),
        )

        serialized = " ".join(command)
        self.assertIn("sidechaincompress=", serialized)
        self.assertIn("threshold=0.080", serialized)
        self.assertIn("ratio=8.00", serialized)

    def test_build_audio_layer_filter_places_sfx_with_adelay(self) -> None:
        layer = AudioLayerSpec(
            kind="sfx",
            profile="click_ui_soft",
            path=Path("click.wav"),
            start_seconds=1.1,
            duration_seconds=0.22,
            volume=0.7,
            loop=False,
        )

        filter_text = _build_audio_layer_filter(layer)

        self.assertIn("adelay=1100|1100", filter_text)
        self.assertIn("atrim=0:0.220", filter_text)

    def test_resolve_audio_layers_reports_missing_profiles_without_error(self) -> None:
        job = VideoJob(
            topic="Montage",
            template="tiktok_ai",
            account="main",
            metadata={"edit_audio_auto_discovery": False},
        )
        audio_plan = asyncio.run(
            _resolve_audio_layers(
                job=job,
                manifest=_build_manifest(),
                edit_manifest=_build_edit_manifest(),
            )
        )

        self.assertEqual(audio_plan.layers, [])
        self.assertEqual(audio_plan.primary_bgm_profile, "tension_light")
        self.assertIn("bgm:tension_light", audio_plan.missing_profiles)
        self.assertIn("sfx:hit_soft", audio_plan.missing_profiles)
        self.assertIn("sfx:click_ui_soft", audio_plan.missing_profiles)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is required for audio asset resolution")
    def test_resolve_audio_layers_uses_single_primary_bgm_and_sfx_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "bgm").mkdir()
            (root / "sfx").mkdir()
            shutil.copy2(TEST_AUDIO_PATH, root / "bgm" / "tension_light.wav")
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "hit_soft.wav")
            shutil.copy2(TEST_AUDIO_PATH, root / "sfx" / "click_ui_soft.wav")

            job = VideoJob(
                topic="Montage",
                template="tiktok_ai",
                account="main",
                metadata={
                    "edit_audio_roots": [str(root)],
                    "edit_max_sfx_per_video": 6,
                    "edit_min_sfx_gap_ms": 100,
                },
            )

            audio_plan = asyncio.run(
                _resolve_audio_layers(
                    job=job,
                    manifest=_build_manifest(),
                    edit_manifest=_build_edit_manifest(),
                )
            )

        self.assertEqual(audio_plan.primary_bgm_profile, "tension_light")
        self.assertEqual(audio_plan.bgm_layer_count, 1)
        self.assertEqual(audio_plan.sfx_layer_count, 2)
        self.assertEqual(audio_plan.missing_profiles, [])
        self.assertEqual(len(audio_plan.sfx_events), 2)
        self.assertLess(audio_plan.sfx_events[0].time_seconds, audio_plan.sfx_events[1].time_seconds)

    @unittest.skipUnless(shutil.which("ffprobe"), "ffprobe is required for audio asset resolution")
    def test_resolve_audio_layers_maps_repo_style_tiktok_sfx_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_root = Path(temp_dir) / "tiktok" / "asset_son"
            (asset_root / "UI click").mkdir(parents=True)
            (asset_root / "hit").mkdir()
            (asset_root / "pop").mkdir()
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "UI click" / "little click.mp3")
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "hit" / "body impacting.mp3")
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "pop" / "pop1.mp3")

            job = VideoJob(
                topic="Montage",
                template="tiktok_ai",
                account="main",
                metadata={"edit_audio_roots": [str(asset_root)]},
            )
            audio_plan = asyncio.run(
                _resolve_audio_layers(
                    job=job,
                    manifest=_build_manifest(),
                    edit_manifest=_build_edit_manifest(),
                )
            )

        self.assertIn("hit_soft", audio_plan.available_sfx_assets)
        self.assertIn("click_ui_soft", audio_plan.available_sfx_assets)
        self.assertEqual(audio_plan.sfx_layer_count, 2)
        self.assertTrue(all(event.asset_path for event in audio_plan.sfx_events))
        self.assertTrue(
            all(decision.resolved_asset_path for decision in audio_plan.sfx_decisions if decision.mixed)
        )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
    def test_assemble_video_step_persists_audio_mix_and_sfx_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="assembler_sfx_") as temp_dir:
            job_dir = Path(temp_dir)
            asset_root = job_dir / "tiktok" / "asset_son"
            (asset_root / "UI click").mkdir(parents=True)
            (asset_root / "hit").mkdir()
            (asset_root / "whoosh").mkdir()
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "UI click" / "little click.mp3")
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "hit" / "body impacting.mp3")
            shutil.copy2(TEST_AUDIO_PATH, asset_root / "whoosh" / "whoosh air.mp3")

            job = VideoJob(
                topic="Montage SFX",
                template="tiktok_ai",
                account="main",
                metadata={
                    "edit_audio_roots": [str(asset_root)],
                    "edit_sfx_enabled": True,
                    "edit_max_sfx_per_video": 4,
                    "edit_min_sfx_gap_ms": 100,
                },
            )
            job.set_voice_audio_path(str(TEST_AUDIO_PATH))
            save_scene_manifest(job, job_dir, _build_manifest())
            save_edit_manifest(job, job_dir, _build_edit_manifest())

            asyncio.run(generate_visual_step(job, job_dir))
            asyncio.run(assemble_video_step(job, job_dir))

            audio_mix_path = Path(str(job.metadata["audio_mix_path"]))
            final_video_path = Path(job.require_file_path("final_video"))

            self.assertTrue(audio_mix_path.exists())
            self.assertTrue(final_video_path.exists())
            self.assertEqual(job.metadata["sfx_enabled"], True)
            self.assertEqual(job.metadata["sfx_strategy"], "scene_role+scene_type+proof_mode+visual_mode+cue_timeline+transitions_v2")
            self.assertGreaterEqual(int(job.metadata["sfx_asset_count"]), 3)
            self.assertGreater(int(job.metadata["edit_audio_sfx_mixed_count"]), 0)
            self.assertEqual(job.metadata["final_video_audio_mode"], "voice_sfx")
            self.assertEqual(job.metadata["sfx_mix_path"], str(audio_mix_path))
            self.assertTrue(job.metadata["sfx_events"])
            self.assertTrue(job.metadata["sfx_selection_log"])
            self.assertTrue(job.metadata["sfx_recent_files"])
            self.assertTrue(job.metadata["sfx_timing_mode"])
            self.assertTrue(job.metadata["sfx_gain_strategy"])
            self.assertTrue(job.metadata["scene_visual_storyboard"])
            self.assertEqual(job.metadata["scene_visual_storyboard_count"], 2)
            storyboard = job.metadata["scene_visual_storyboard"]
            assert isinstance(storyboard, list)
            self.assertIn("scene_title", storyboard[0])
            self.assertIn("overlay_applied", storyboard[0])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
    def test_assemble_video_step_keeps_render_valid_when_sfx_assets_are_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="assembler_voice_only_") as temp_dir:
            job_dir = Path(temp_dir)
            job = VideoJob(
                topic="Montage sans SFX",
                template="tiktok_ai",
                account="main",
                metadata={
                    "edit_sfx_enabled": True,
                    "edit_audio_auto_discovery": False,
                },
            )
            job.set_voice_audio_path(str(TEST_AUDIO_PATH))
            save_scene_manifest(job, job_dir, _build_manifest())
            save_edit_manifest(job, job_dir, _build_edit_manifest())

            asyncio.run(generate_visual_step(job, job_dir))
            asyncio.run(assemble_video_step(job, job_dir))

            final_video_path = Path(job.require_file_path("final_video"))
            audio_mix_path = Path(str(job.metadata["audio_mix_path"]))

            self.assertTrue(final_video_path.exists())
            self.assertTrue(audio_mix_path.exists())
            self.assertEqual(job.metadata["edit_audio_sfx_mixed_count"], 0)
            self.assertEqual(job.metadata["final_video_audio_mode"], "voice_only")
            self.assertEqual(job.metadata["sfx_events"], [])


if __name__ == "__main__":
    unittest.main()
