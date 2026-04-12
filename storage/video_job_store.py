from __future__ import annotations

from pathlib import Path

from schemas import VideoJob


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

    def _job_path(self, job_id: str) -> Path:
        return self.base_dir / f"{job_id}.json"
