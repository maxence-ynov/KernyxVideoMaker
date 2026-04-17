from __future__ import annotations

import unittest

from pipeline.captions import (
    CaptionTimingSettings,
    CaptionWordBoundary,
    build_timed_caption_cues,
    compute_caption_emphasis,
)


def _default_settings() -> CaptionTimingSettings:
    return CaptionTimingSettings(
        max_words_per_chunk=4,
        max_chars_per_line=16,
        max_lines=2,
        min_duration_ms=650,
        max_duration_ms=2200,
        gap_ms=30,
    )


class CaptionEngineTests(unittest.TestCase):
    def test_build_timed_caption_cues_prefers_word_boundaries(self) -> None:
        settings = _default_settings()
        boundaries = [
            CaptionWordBoundary(index=1, text="Zapier", start_seconds=0.00, end_seconds=0.20),
            CaptionWordBoundary(index=2, text="donne", start_seconds=0.20, end_seconds=0.38),
            CaptionWordBoundary(index=3, text="27%", start_seconds=0.38, end_seconds=0.58),
            CaptionWordBoundary(index=4, text="de", start_seconds=0.58, end_seconds=0.70),
            CaptionWordBoundary(index=5, text="gain", start_seconds=0.70, end_seconds=0.90),
            CaptionWordBoundary(index=6, text="visible", start_seconds=0.90, end_seconds=1.18),
        ]

        cues, timing_source = build_timed_caption_cues(
            "Zapier donne 27% de gain visible",
            1.4,
            settings=settings,
            word_boundaries=boundaries,
        )

        self.assertEqual(timing_source, "word_boundaries")
        self.assertGreaterEqual(len(cues), 2)
        self.assertTrue(all(cue.timing_source == "word_boundaries" for cue in cues))
        self.assertLess(cues[0].start_seconds, cues[0].end_seconds)

    def test_build_timed_caption_cues_falls_back_without_boundaries(self) -> None:
        cues, timing_source = build_timed_caption_cues(
            "Lecture claire meme sans timing mot a mot",
            2.2,
            settings=_default_settings(),
            word_boundaries=[],
        )

        self.assertEqual(timing_source, "proportional_fallback")
        self.assertTrue(all(cue.timing_source == "proportional_fallback" for cue in cues))
        self.assertGreaterEqual(len(cues), 2)

    def test_compute_caption_emphasis_prioritizes_numbers_tools_and_results(self) -> None:
        decision = compute_caption_emphasis(
            "Zapier sort 27% de resultat visible",
            scene_keywords=["zapier", "resultat"],
            pace_role="proof",
            scene_type="proof",
        )

        self.assertIsNone(decision.skipped_reason)
        self.assertIn("Zapier", decision.tokens)
        self.assertIn("27%", decision.tokens)

    def test_compute_caption_emphasis_skips_weak_tokens(self) -> None:
        decision = compute_caption_emphasis(
            "tu peux faire des choses avec le truc",
            scene_keywords=[],
            pace_role="explain",
            scene_type="explain",
        )

        self.assertEqual(decision.tokens, ())
        self.assertEqual(decision.skipped_reason, "no_priority_tokens")


if __name__ == "__main__":
    unittest.main()
