from __future__ import annotations

from pathlib import Path
from typing import Iterable

from job_paths import JOB_FILE_PATH_METADATA_KEY, JOB_JSON_FILENAME, get_job_json_path
from schemas import VideoJob, VideoJobStatus


class VideoJobStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, job: VideoJob) -> Path:
        path = get_job_json_path(self.base_dir, job)
        path.parent.mkdir(parents=True, exist_ok=True)
        job.set_metadata(JOB_FILE_PATH_METADATA_KEY, str(path))
        job.touch()
        path.write_text(f"{job.to_json()}\n", encoding="utf-8")
        return path

    def load(self, job_id: str) -> VideoJob:
        return VideoJob.from_json(self._locate_job_path(job_id).read_text(encoding="utf-8"))

    def delete(self, job_id: str) -> None:
        path = self._locate_job_path(job_id, required=False)
        if path is None or not path.exists():
            return

        path.unlink()
        if path.name == JOB_JSON_FILENAME and path.parent.parent == self.base_dir:
            try:
                path.parent.rmdir()
            except OSError:
                pass

    def list_jobs(self) -> list[VideoJob]:
        jobs_by_id: dict[str, VideoJob] = {}
        for path in self._iter_job_paths():
            job = VideoJob.from_json(path.read_text(encoding="utf-8"))
            existing_job = jobs_by_id.get(job.job_id)
            if existing_job is None or job.updated_at >= existing_job.updated_at:
                jobs_by_id[job.job_id] = job
        return list(jobs_by_id.values())

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

    def _locate_job_path(self, job_id: str, *, required: bool = True) -> Path | None:
        legacy_path = self.base_dir / f"{job_id}.json"
        if legacy_path.exists():
            return legacy_path

        for path in self._iter_job_paths():
            job = VideoJob.from_json(path.read_text(encoding="utf-8"))
            if job.job_id == job_id:
                return path

        if required:
            raise FileNotFoundError(f"job '{job_id}' was not found in {self.base_dir}")
        return None

    def _iter_job_paths(self) -> list[Path]:
        paths = list(self.base_dir.glob("*.json"))
        paths.extend(self.base_dir.glob(f"*/{JOB_JSON_FILENAME}"))
        return sorted({path.resolve() for path in paths})
