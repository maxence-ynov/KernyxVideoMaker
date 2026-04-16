from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from job_paths import get_job_artifacts_dir
from pipeline.script_gen import generate_script_step
from pipeline.visual_gen import generate_visual_step
from scheduler import load_jobs_from_config
from schemas import VideoJobStatus
from storage import VideoJobStore

DEFAULT_CONFIG_PATH = Path("config/anti_repeat_v1_1_10_jobs.json")
DEFAULT_OUTPUT_ROOT = Path("tmp/anti_repeat_v1_1_demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the anti-repetition V1.1 demo on 10 jobs.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to the demo config")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory where demo jobs and artifacts are written",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep any previous demo output instead of recreating it",
    )
    return parser.parse_args()


async def run_demo(config_path: Path, output_root: Path, *, clean: bool) -> Path:
    if clean and output_root.exists():
        shutil.rmtree(output_root)

    jobs_dir = output_root / "jobs"
    artifacts_dir = output_root / "artifacts"
    summary_path = output_root / "summary.json"

    store = VideoJobStore(jobs_dir)
    jobs = load_jobs_from_config(config_path)
    summary: list[dict[str, object]] = []

    for index, job in enumerate(jobs, start=1):
        job.set_metadata("jobs_dir", str(jobs_dir.resolve()))
        store.save(job)

        job_dir = get_job_artifacts_dir(artifacts_dir, job)
        await generate_script_step(job, job_dir)
        await generate_visual_step(job, job_dir)
        job.set_status(VideoJobStatus.COMPLETED)
        store.save(job)

        profile_resolution = job.metadata.get("script_profile_resolution", {})
        summary.append(
            {
                "index": index,
                "topic": job.topic,
                "niche": job.metadata.get("niche"),
                "script_profile": job.metadata.get("script_profile"),
                "script_profile_source": job.metadata.get("script_profile_source"),
                "script_profile_locked": job.metadata.get("script_profile_locked"),
                "script_profile_resolution": profile_resolution,
                "script_proof_mode": job.metadata.get("script_proof_mode"),
                "script_hook_family": job.metadata.get("script_hook_family"),
                "script_cta_family": job.metadata.get("script_cta_family"),
                "script_body_structure": job.metadata.get("script_body_structure"),
                "visual_asset_mode": job.metadata.get("visual_asset_mode"),
                "visual_theme": job.metadata.get("visual_theme"),
                "visual_layout_variant": job.metadata.get("visual_layout_variant"),
            }
        )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_summary(summary, summary_path)
    return summary_path


def _print_summary(summary: list[dict[str, object]], summary_path: Path) -> None:
    for item in summary:
        print(
            (
                f"{int(item['index']):02d} | profile={item['script_profile']} | "
                f"source={item['script_profile_source']} | proof={item['script_proof_mode']} | "
                f"hook={item['script_hook_family']} | cta={item['script_cta_family']} | "
                f"body={item['script_body_structure']} | visual={item['visual_asset_mode']}/"
                f"{item['visual_theme']}/{item['visual_layout_variant']} | topic={item['topic']}"
            )
        )
    print(f"\nSummary written to {summary_path}")


def main() -> int:
    args = parse_args()
    summary_path = asyncio.run(
        run_demo(
            config_path=Path(args.config),
            output_root=Path(args.output_root),
            clean=not args.no_clean,
        )
    )
    return 0 if summary_path.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
