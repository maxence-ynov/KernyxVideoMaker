from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Iterable

from core.orchestrator import Orchestrator
from schemas import VideoJob, VideoJobStatus


@dataclass(frozen=True, slots=True)
class QueueRunSummary:
    total: int
    completed: int
    failed: int


class QueueManager:
    def __init__(
        self,
        orchestrator: Orchestrator,
        concurrency: int = 2,
        logger: logging.Logger | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be greater than or equal to 1")

        self.orchestrator = orchestrator
        self.concurrency = concurrency
        self.logger = logger or logging.getLogger(__name__)

    async def run(self, jobs: Iterable[VideoJob]) -> QueueRunSummary:
        job_list = list(jobs)
        queue: asyncio.Queue[VideoJob | None] = asyncio.Queue()

        for job in job_list:
            await queue.put(job)
            self.logger.info("queued job=%s topic=%s", job.job_id, job.topic)

        for _ in range(self.concurrency):
            await queue.put(None)

        workers = [
            asyncio.create_task(self._worker_loop(queue, worker_id), name=f"worker-{worker_id}")
            for worker_id in range(1, self.concurrency + 1)
        ]

        await queue.join()
        await asyncio.gather(*workers)

        summary = QueueRunSummary(
            total=len(job_list),
            completed=sum(job.status == VideoJobStatus.COMPLETED for job in job_list),
            failed=sum(job.status == VideoJobStatus.FAILED for job in job_list),
        )
        self.logger.info(
            "queue finished total=%s completed=%s failed=%s",
            summary.total,
            summary.completed,
            summary.failed,
        )
        return summary

    async def _worker_loop(
        self,
        queue: asyncio.Queue[VideoJob | None],
        worker_id: int,
    ) -> None:
        while True:
            job = await queue.get()
            try:
                if job is None:
                    self.logger.debug("worker=%s stopped", worker_id)
                    return

                self.logger.info("worker=%s started job=%s", worker_id, job.job_id)
                await self.orchestrator.run_job(job)
                self.logger.info(
                    "worker=%s finished job=%s status=%s",
                    worker_id,
                    job.job_id,
                    job.status,
                )
            finally:
                queue.task_done()


__all__ = ["QueueManager", "QueueRunSummary"]
