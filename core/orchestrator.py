from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil
from typing import Awaitable, Callable, Sequence

from job_paths import get_job_artifacts_dir, resolve_job_path
from pipeline import (
    assemble_video_step,
    generate_script_step,
    plan_edit_step,
    generate_visual_step,
    generate_voice_step,
    plan_scenes_step,
)
from schemas import VideoJob, VideoJobStatus
from storage import VideoJobStore

StepHandler = Callable[[VideoJob, Path], Awaitable[None]]
TEXT_ARTIFACT_KEYS = frozenset({"script", "subtitles"})
MOCK_PATH_MARKERS = (".mock.",)


class ArtifactValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PipelineStep:
    name: str
    handler: StepHandler
    output_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VideoProbe:
    has_video: bool
    duration_seconds: float | None


class Orchestrator:
    def __init__(
        self,
        store: VideoJobStore,
        artifacts_dir: str | Path,
        steps: Sequence[PipelineStep] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.store = store
        self.artifacts_dir = Path(artifacts_dir).expanduser().resolve()
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.steps = tuple(steps or build_default_pipeline())
        self.logger = logger or logging.getLogger(__name__)
        self._validate_pipeline_contract()

    async def run_job(self, job: VideoJob) -> VideoJob:
        job_dir = get_job_artifacts_dir(self.artifacts_dir, job)
        job_dir.mkdir(parents=True, exist_ok=True)

        job.set_metadata("artifact_dir", str(job_dir))
        job.set_metadata("jobs_dir", str(self.store.base_dir.resolve()))
        job.set_status(VideoJobStatus.RUNNING)
        self.store.save(job)
        self.logger.info("job=%s status=%s topic=%s", job.job_id, job.status, job.topic)

        try:
            for step in self.steps:
                self.logger.info("job=%s step=%s started", job.job_id, step.name)
                await step.handler(job, job_dir)
                await self._validate_step_outputs(job, step)
                self.store.save(job)
                self.logger.info("job=%s step=%s finished", job.job_id, step.name)

            await self._validate_completion_requirements(job)
            job.set_status(VideoJobStatus.COMPLETED)
            self.store.save(job)
            self.logger.info("job=%s status=%s", job.job_id, job.status)
        except Exception as exc:
            job.add_error(str(exc))
            job.set_status(VideoJobStatus.FAILED)
            self.store.save(job)
            self.logger.exception("job=%s status=%s", job.job_id, job.status)

        return job

    def _validate_pipeline_contract(self) -> None:
        invalid_artifacts = sorted(
            {
                artifact_name
                for step in self.steps
                for artifact_name in step.output_artifacts
                if artifact_name not in VideoJob.ARTIFACT_KEYS
            }
        )
        if invalid_artifacts:
            joined_names = ", ".join(invalid_artifacts)
            raise ValueError(f"pipeline declares unsupported artifact keys: {joined_names}")

    async def _validate_step_outputs(self, job: VideoJob, step: PipelineStep) -> None:
        for artifact_name in step.output_artifacts:
            await self._validate_artifact(job, artifact_name)

    async def _validate_completion_requirements(self, job: VideoJob) -> None:
        for artifact_name in VideoJob.ARTIFACT_KEYS:
            await self._validate_artifact(job, artifact_name)

    async def _validate_artifact(self, job: VideoJob, artifact_name: str) -> None:
        artifact_path = self._resolve_required_artifact_path(job, artifact_name)
        self._validate_file_exists(job, artifact_name, artifact_path)
        self._validate_non_mock_path(job, artifact_name, artifact_path)
        self._validate_text_artifact(job, artifact_name, artifact_path)
        if artifact_name == "final_video":
            await self._validate_video_artifact(job, artifact_path)

    def _resolve_required_artifact_path(self, job: VideoJob, artifact_name: str) -> Path:
        try:
            raw_path = job.require_file_path(artifact_name)
        except ValueError as exc:
            raise ArtifactValidationError(
                f"job {job.job_id} is missing required artifact '{artifact_name}'"
            ) from exc
        return resolve_job_path(job, raw_path)

    def _validate_file_exists(self, job: VideoJob, artifact_name: str, path: Path) -> None:
        if not path.exists():
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' does not exist: {path}"
            )
        if not path.is_file():
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' is not a file: {path}"
            )
        if path.stat().st_size <= 0:
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' is empty: {path}"
            )

    def _validate_non_mock_path(self, job: VideoJob, artifact_name: str, path: Path) -> None:
        normalized_path = str(path).lower()
        if any(marker in normalized_path for marker in MOCK_PATH_MARKERS):
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' points to a mock output: {path}"
            )

    def _validate_text_artifact(self, job: VideoJob, artifact_name: str, path: Path) -> None:
        if artifact_name not in TEXT_ARTIFACT_KEYS:
            return

        try:
            content = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' is not valid UTF-8 text: {path}"
            ) from exc

        if not content:
            raise ArtifactValidationError(
                f"job {job.job_id} artifact '{artifact_name}' is empty after trimming: {path}"
            )
        if artifact_name == "subtitles" and "-->" not in content:
            raise ArtifactValidationError(
                f"job {job.job_id} subtitles artifact is missing SRT timestamps: {path}"
            )

    async def _validate_video_artifact(self, job: VideoJob, path: Path) -> None:
        _ensure_binary_available("ffprobe")
        probe = await _probe_video(path)
        if not probe.has_video:
            raise ArtifactValidationError(
                f"job {job.job_id} final video is missing a video stream: {path}"
            )
        if probe.duration_seconds is None or probe.duration_seconds <= 0:
            raise ArtifactValidationError(
                f"job {job.job_id} final video has no positive duration: {path}"
            )


def build_default_pipeline() -> tuple[PipelineStep, ...]:
    return (
        PipelineStep(name="script_gen", handler=generate_script_step, output_artifacts=("script",)),
        PipelineStep(
            name="voice_gen",
            handler=generate_voice_step,
            output_artifacts=("voice_audio", "subtitles"),
        ),
        PipelineStep(name="scene_planner", handler=plan_scenes_step),
        PipelineStep(
            name="visual_gen",
            handler=generate_visual_step,
            output_artifacts=("source_media",),
        ),
        PipelineStep(name="edit_planner", handler=plan_edit_step),
        PipelineStep(
            name="assembler",
            handler=assemble_video_step,
            output_artifacts=("final_video",),
        ),
    )

def _ensure_binary_available(binary_name: str) -> None:
    if shutil.which(binary_name) is None:
        raise ArtifactValidationError(f"required binary '{binary_name}' was not found in PATH")


async def _probe_video(path: Path) -> VideoProbe:
    payload = await _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(payload or "{}")
    streams = data.get("streams", [])
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    duration_value = data.get("format", {}).get("duration")
    duration_seconds = None
    if isinstance(duration_value, str) and duration_value.strip() and duration_value != "N/A":
        duration_seconds = float(duration_value)
    return VideoProbe(has_video=has_video, duration_seconds=duration_seconds)


async def _run_command(command: list[str]) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        error_output = stderr_text or stdout_text or "<no output>"
        raise ArtifactValidationError(
            f"command failed with exit code {process.returncode}: "
            f"{_format_command(command)}\n{error_output}"
        )

    return stdout_text


def _format_command(command: list[str]) -> str:
    return " ".join(_shell_quote(part) for part in command)


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe_characters = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._/:=+")
    if all(character in safe_characters for character in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


__all__ = ["Orchestrator", "PipelineStep", "build_default_pipeline"]
