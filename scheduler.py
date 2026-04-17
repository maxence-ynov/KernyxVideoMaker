from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, cast

from core import Orchestrator, QueueManager, run_runtime_preflight
from core.queue_manager import QueueRunSummary
from schemas import JSONValue, VideoJob
from storage import VideoJobStore

DEFAULT_CONFIG_PATH = Path("config/topics.json")
DEFAULT_JOBS_DIR = Path("storage/jobs")
DEFAULT_ARTIFACTS_DIR = Path("storage/artifacts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local video job scheduler.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to topics.json")
    parser.add_argument("--jobs-dir", default=str(DEFAULT_JOBS_DIR), help="Directory for job JSON")
    parser.add_argument(
        "--artifacts-dir",
        default=str(DEFAULT_ARTIFACTS_DIR),
        help="Directory for generated artifacts",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Number of jobs processed in parallel",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run runtime preflight checks and exit without processing jobs",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip automatic runtime preflight checks before starting the queue",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    return parser.parse_args()


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def load_jobs_from_config(config_path: str | Path) -> list[VideoJob]:
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("topics config must be a JSON object")

    raw_defaults = payload.get("defaults", {})
    if raw_defaults is None:
        raw_defaults = {}
    if not isinstance(raw_defaults, dict):
        raise ValueError("'defaults' must be a JSON object")

    default_account = _read_optional_string(
        raw_defaults.get("account", payload.get("account")),
        "defaults.account",
    )
    default_template = _read_optional_string(
        raw_defaults.get("template", payload.get("template")),
        "defaults.template",
    )
    default_metadata = _read_metadata(
        raw_defaults.get("metadata", payload.get("metadata", {})),
        "defaults.metadata",
    )

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise ValueError("'topics' must be a non-empty array")

    jobs: list[VideoJob] = []
    for index, entry in enumerate(raw_topics):
        jobs.append(
            _build_job_from_entry(
                entry=entry,
                index=index,
                default_account=default_account,
                default_template=default_template,
                default_metadata=default_metadata,
                config_path=path,
            )
        )

    return jobs


def _build_job_from_entry(
    entry: Any,
    index: int,
    default_account: str | None,
    default_template: str | None,
    default_metadata: dict[str, JSONValue],
    config_path: Path,
) -> VideoJob:
    if isinstance(entry, str):
        topic = entry.strip()
        account = default_account
        template = default_template
        entry_metadata: dict[str, JSONValue] = {}
    elif isinstance(entry, dict):
        topic = _read_required_string(entry.get("topic"), f"topics[{index}].topic")
        account = _read_optional_string(entry.get("account"), f"topics[{index}].account")
        template = _read_optional_string(entry.get("template"), f"topics[{index}].template")
        entry_metadata = _read_metadata(entry.get("metadata", {}), f"topics[{index}].metadata")
    else:
        raise ValueError(f"topics[{index}] must be either a string or an object")

    resolved_account = account or default_account
    resolved_template = template or default_template
    if not resolved_account:
        raise ValueError(f"missing account for topics[{index}]")
    if not resolved_template:
        raise ValueError(f"missing template for topics[{index}]")

    metadata = dict(default_metadata)
    metadata.update(entry_metadata)
    metadata["config_path"] = str(config_path)
    metadata["config_index"] = index

    return VideoJob(
        topic=topic,
        template=resolved_template,
        account=resolved_account,
        metadata=metadata,
    )


def _read_required_string(value: Any, field_name: str) -> str:
    normalized_value = _read_optional_string(value, field_name)
    if normalized_value is None:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized_value


def _read_optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized_value = value.strip()
    return normalized_value or None


def _read_metadata(value: Any, field_name: str) -> dict[str, JSONValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return {str(key): cast(JSONValue, item) for key, item in value.items()}


async def run_scheduler(
    args: argparse.Namespace,
    *,
    jobs: list[VideoJob] | None = None,
) -> QueueRunSummary:
    logger = logging.getLogger(__name__)
    job_list = list(jobs) if jobs is not None else load_jobs_from_config(args.config)

    store = VideoJobStore(args.jobs_dir)
    for job in job_list:
        store.save(job)
        logger.info("prepared job=%s topic=%s", job.job_id, job.topic)

    orchestrator = Orchestrator(
        store=store,
        artifacts_dir=args.artifacts_dir,
        logger=logging.getLogger("core.orchestrator"),
    )
    queue_manager = QueueManager(
        orchestrator=orchestrator,
        concurrency=args.concurrency,
        logger=logging.getLogger("core.queue_manager"),
    )
    return await queue_manager.run(job_list)


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    try:
        jobs = load_jobs_from_config(args.config)
        if args.preflight or not args.skip_preflight:
            run_runtime_preflight(jobs, args.config)
        if args.preflight:
            logging.getLogger(__name__).info(
                "preflight finished config=%s jobs=%s",
                Path(args.config).resolve(),
                len(jobs),
            )
            return 0
        summary = asyncio.run(run_scheduler(args, jobs=jobs))
    except Exception:
        logging.getLogger(__name__).exception("scheduler failed")
        return 1

    logging.getLogger(__name__).info(
        "scheduler finished total=%s completed=%s failed=%s",
        summary.total,
        summary.completed,
        summary.failed,
    )
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
