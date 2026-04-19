from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.preflight import RuntimePreflightError, run_runtime_preflight
from pipeline.assembler import RuntimeFontResolution
from schemas import VideoJob


def _which(binary_name: str) -> str | None:
    mapping = {
        "python3": "/usr/bin/python3",
        "ffmpeg": "/usr/bin/ffmpeg",
        "ffprobe": "/usr/bin/ffprobe",
    }
    return mapping.get(binary_name)


class RuntimePreflightTests(unittest.TestCase):
    def test_preflight_fails_when_edge_tts_is_missing(self) -> None:
        with (
            patch("core.preflight.shutil.which", side_effect=_which),
            patch("core.preflight.importlib.util.find_spec", return_value=None),
            patch(
                "core.preflight.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="ffmpeg version 6.1.1\n",
                    stderr="",
                ),
            ),
        ):
            with self.assertRaises(RuntimePreflightError) as exc_info:
                run_runtime_preflight([], "config/topics.json")

        self.assertIn("edge-tts is not installed", str(exc_info.exception))

    def test_preflight_fails_when_ffmpeg_or_ffprobe_is_missing(self) -> None:
        def missing_ffmpeg(binary_name: str) -> str | None:
            mapping = {
                "python3": "/usr/bin/python3",
                "ffprobe": None,
                "ffmpeg": None,
            }
            return mapping.get(binary_name)

        with (
            patch("core.preflight.shutil.which", side_effect=missing_ffmpeg),
            patch("core.preflight.importlib.util.find_spec", return_value=object()),
        ):
            with self.assertRaises(RuntimePreflightError) as exc_info:
                run_runtime_preflight([], "config/topics.json")

        message = str(exc_info.exception)
        self.assertIn("ffmpeg was not found", message)
        self.assertIn("ffprobe was not found", message)

    def test_preflight_succeeds_when_font_smoke_resolves_cleanly(self) -> None:
        job = VideoJob(topic="Hooks", template="tiktok_ai", account="main")
        with (
            patch("core.preflight.shutil.which", side_effect=_which),
            patch("core.preflight.importlib.util.find_spec", return_value=object()),
            patch(
                "core.preflight.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="ffmpeg version 6.1.1\n",
                    stderr="",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_subtitle_font",
                return_value=RuntimeFontResolution(
                    requested_name="DejaVu Sans",
                    resolved_name="DejaVu Sans",
                    used_fallback=False,
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_font",
                return_value=RuntimeFontResolution(
                    requested_name="Noto Sans Display",
                    resolved_name="Noto Sans Display",
                    used_fallback=False,
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_support_font",
                return_value=RuntimeFontResolution(
                    requested_name="Noto Sans",
                    resolved_name="Noto Sans",
                    used_fallback=False,
                    resolved_path="/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_symbol_font",
                return_value=RuntimeFontResolution(
                    requested_name="DejaVu Sans",
                    resolved_name="DejaVu Sans",
                    used_fallback=False,
                    resolved_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                ),
            ),
        ):
            results = run_runtime_preflight([job], "config/topics.json")

        self.assertTrue(any(result.name == "subtitle-font" for result in results))
        self.assertTrue(any(result.name == "overlay-font" for result in results))
        self.assertTrue(any(result.name == "overlay-support-font" for result in results))
        self.assertTrue(any(result.name == "overlay-symbol-font" for result in results))
        self.assertTrue(any("DejaVu Sans" in result.detail for result in results))

    def test_preflight_reports_font_fallback_when_requested_font_is_missing(self) -> None:
        job = VideoJob(
            topic="Hooks",
            template="tiktok_ai",
            account="main",
            metadata={
                "subtitle_font_name": "Missing Font",
                "story_text_font_name": "Missing Display Font",
            },
        )
        with (
            patch("core.preflight.shutil.which", side_effect=_which),
            patch("core.preflight.importlib.util.find_spec", return_value=object()),
            patch(
                "core.preflight.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="ffmpeg version 6.1.1\n",
                    stderr="",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_subtitle_font",
                return_value=RuntimeFontResolution(
                    requested_name="Missing Font",
                    resolved_name="DejaVu Sans",
                    used_fallback=True,
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_font",
                return_value=RuntimeFontResolution(
                    requested_name="Missing Display Font",
                    resolved_name="Noto Sans",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\segoeuib.ttf",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_support_font",
                return_value=RuntimeFontResolution(
                    requested_name="Missing Support Font",
                    resolved_name="Segoe UI",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\segoeui.ttf",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_symbol_font",
                return_value=RuntimeFontResolution(
                    requested_name="Missing Symbol Font",
                    resolved_name="Segoe UI Symbol",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\seguisym.ttf",
                ),
            ),
        ):
            results = run_runtime_preflight([job], "config/topics.json")

        font_result = next(result for result in results if result.name == "subtitle-font")
        overlay_result = next(result for result in results if result.name == "overlay-font")
        self.assertIn("requested='Missing Font'", font_result.detail)
        self.assertIn("resolved='DejaVu Sans'", font_result.detail)
        self.assertIn("requested='Missing Display Font'", overlay_result.detail)
        self.assertIn("resolved='Noto Sans'", overlay_result.detail)
        self.assertIn("mode=fontfile", overlay_result.detail)
        self.assertIn("C:/Windows/Fonts/segoeuib.ttf", overlay_result.detail)

    def test_preflight_reports_drawtext_lanes_with_fontfile_paths(self) -> None:
        job = VideoJob(topic="Hooks", template="tiktok_ai", account="main")
        with (
            patch("core.preflight.shutil.which", side_effect=_which),
            patch("core.preflight.importlib.util.find_spec", return_value=object()),
            patch(
                "core.preflight.subprocess.run",
                return_value=SimpleNamespace(
                    returncode=0,
                    stdout="ffmpeg version 6.1.1\n",
                    stderr="",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_subtitle_font",
                return_value=RuntimeFontResolution(
                    requested_name="Noto Sans",
                    resolved_name="Noto Sans",
                    used_fallback=False,
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_font",
                return_value=RuntimeFontResolution(
                    requested_name="Noto Sans Display",
                    resolved_name="Segoe UI Semibold",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\seguisb.ttf",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_support_font",
                return_value=RuntimeFontResolution(
                    requested_name="Noto Sans",
                    resolved_name="Segoe UI",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\segoeui.ttf",
                ),
            ),
            patch(
                "core.preflight.resolve_runtime_story_symbol_font",
                return_value=RuntimeFontResolution(
                    requested_name="DejaVu Sans",
                    resolved_name="Segoe UI Symbol",
                    used_fallback=True,
                    resolved_path=r"C:\Windows\Fonts\seguisym.ttf",
                ),
            ),
        ):
            results = run_runtime_preflight([job], "config/topics.json")

        overlay_result = next(result for result in results if result.name == "overlay-font")
        support_result = next(result for result in results if result.name == "overlay-support-font")
        symbol_result = next(result for result in results if result.name == "overlay-symbol-font")

        self.assertIn("mode=fontfile", overlay_result.detail)
        self.assertIn("C:/Windows/Fonts/seguisb.ttf", overlay_result.detail)
        self.assertIn("mode=fontfile", support_result.detail)
        self.assertIn("C:/Windows/Fonts/segoeui.ttf", support_result.detail)
        self.assertIn("mode=fontfile", symbol_result.detail)
        self.assertIn("C:/Windows/Fonts/seguisym.ttf", symbol_result.detail)


if __name__ == "__main__":
    unittest.main()
