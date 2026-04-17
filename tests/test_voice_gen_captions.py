from __future__ import annotations

import unittest

from pipeline.voice_gen import SubtitleSettings, TimedWord, _build_subtitle_content


def _build_settings() -> SubtitleSettings:
    return SubtitleSettings(
        max_words_per_chunk=4,
        max_chars_per_line=16,
        max_lines=2,
        min_duration_ms=550,
        max_duration_ms=2200,
        gap_ms=30,
    )


class VoiceGenCaptionTimingTests(unittest.TestCase):
    def test_build_subtitle_content_prefers_word_boundaries(self) -> None:
        timed_words = [
            TimedWord(index=1, text="Gagne", start_seconds=0.00, end_seconds=0.20, duration_seconds=0.20),
            TimedWord(index=2, text="30%", start_seconds=0.20, end_seconds=0.38, duration_seconds=0.18),
            TimedWord(index=3, text="de", start_seconds=0.38, end_seconds=0.50, duration_seconds=0.12),
            TimedWord(index=4, text="temps", start_seconds=0.50, end_seconds=0.74, duration_seconds=0.24),
            TimedWord(index=5, text="avec", start_seconds=0.74, end_seconds=0.92, duration_seconds=0.18),
            TimedWord(index=6, text="Zapier", start_seconds=0.92, end_seconds=1.18, duration_seconds=0.26),
            TimedWord(index=7, text="en", start_seconds=1.18, end_seconds=1.34, duration_seconds=0.16),
            TimedWord(index=8, text="2", start_seconds=1.34, end_seconds=1.48, duration_seconds=0.14),
            TimedWord(index=9, text="jours", start_seconds=1.48, end_seconds=1.68, duration_seconds=0.20),
        ]

        content, cues, timing_source = _build_subtitle_content(
            "Gagne 30% de temps avec Zapier en 2 jours",
            1.68,
            settings=_build_settings(),
            timed_words=timed_words,
        )

        self.assertEqual(timing_source, "word_boundaries")
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "Gagne 30% de temps")
        self.assertIn("Gagne 30%\nde temps", content)
        self.assertGreater(cues[1].start_seconds, cues[0].start_seconds)

    def test_build_subtitle_content_falls_back_without_word_boundaries(self) -> None:
        content, cues, timing_source = _build_subtitle_content(
            "Gagne 30% de temps avec Zapier en 2 jours",
            1.68,
            settings=_build_settings(),
            timed_words=[],
        )

        self.assertEqual(timing_source, "proportional_fallback")
        self.assertEqual(len(cues), 3)
        self.assertIn("-->", content)
        self.assertTrue(all(cue.end_seconds > cue.start_seconds for cue in cues))


if __name__ == "__main__":
    unittest.main()
