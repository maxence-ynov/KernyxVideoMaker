from __future__ import annotations

from pathlib import Path
from typing import Iterable

from schemas import VideoJob, VideoJobStatus


class VideoJobStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, job: VideoJob) -> Path:
        job.touch()
        path = self._job_path(job.job_id)
        path.write_text(f"{job.to_json()}\n", encoding="utf-8")
        return path

    def load(self, job_id: str) -> VideoJob:
        return VideoJob.from_json(self._job_path(job_id).read_text(encoding="utf-8"))

    def delete(self, job_id: str) -> None:
        path = self._job_path(job_id)
        if path.exists():
            path.unlink()

    def list_jobs(self) -> list[VideoJob]:
        return [
            VideoJob.from_json(path.read_text(encoding="utf-8"))
            for path in sorted(self.base_dir.glob("*.json"))
        ]

    def list_recent_jobs(
        self,
        *,
        limit: int | None = None,
        exclude_job_id: str | None = None,
        statuses: Iterable[VideoJobStatus | str] | None = None,
    ) -> list[VideoJob]:
        jobs = self.list_jobs()
        if exclude_job_id:
            jobs = [job for job in jobs if job.job_id != exclude_job_id]

        if statuses is not None:
            allowed_statuses = {
                VideoJobStatus(status).value
                for status in statuses
            }
            jobs = [job for job in jobs if job.status.value in allowed_statuses]

        jobs.sort(key=lambda job: job.updated_at, reverse=True)
        if limit is not None:
            return jobs[:limit]
        return jobs

    def _job_path(self, job_id: str) -> Path:
        return self.base_dir / f"{job_id}.json"
