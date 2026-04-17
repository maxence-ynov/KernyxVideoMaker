from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import logging
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

from pipeline.assembler import AssemblerError, resolve_runtime_subtitle_font
from schemas import VideoJob

LOGGER = logging.getLogger(__name__)


class RuntimePreflightError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class PreflightCheckResult:
    name: str
    detail: str


def run_runtime_preflight(
    jobs: Sequence[VideoJob],
    config_path: str | Path,
) -> tuple[PreflightCheckResult, ...]:
    results: list[PreflightCheckResult] = []
    failures: list[str] = []

    python3_path = shutil.which("python3")
    if python3_path is None:
        failures.append("python3 was not found in PATH")
    else:
        results.append(
            PreflightCheckResult(
                name="python3",
                detail=f"ok ({python3_path}; runtime={Path(sys.executable).resolve()})",
            )
        )

    if importlib.util.find_spec("edge_tts") is None:
        failures.append("edge-tts is not installed; run `python3 -m pip install -r requirements.txt`")
    else:
        results.append(PreflightCheckResult(name="edge-tts", detail="ok"))

    for binary_name in ("ffmpeg", "ffprobe"):
        binary_path = shutil.which(binary_name)
        if binary_path is None:
            failures.append(f"{binary_name} was not found in PATH")
            continue
        version_probe = subprocess.run(
            [binary_path, "-version"],
            capture_output=True,
            text=True,
            check=False,
        )
        version_line = (version_probe.stdout or version_probe.stderr).splitlines()[0].strip()
        results.append(
            PreflightCheckResult(
                name=binary_name,
                detail=f"ok ({version_line})",
            )
        )

    font_failures: list[str] = []
    seen_font_requests: set[str] = set()
    for job in jobs:
        requested_name = str(
            job.metadata.get(
                "subtitle_font_name",
                job.metadata.get("edit_caption_font_name", ""),
            )
            or ""
        ).strip()
        if requested_name in seen_font_requests:
            continue
        seen_font_requests.add(requested_name)
        try:
            resolution = resolve_runtime_subtitle_font(job)
        except AssemblerError as exc:
            font_failures.append(str(exc))
            continue
        detail = (
            f"requested='{resolution.requested_name}' resolved='{resolution.resolved_name}'"
            if resolution.used_fallback
            else f"ok ({resolution.resolved_name})"
        )
        results.append(PreflightCheckResult(name="subtitle-font", detail=detail))

    failures.extend(font_failures)
    if failures:
        raise RuntimePreflightError(
            "runtime preflight failed for "
            f"{Path(config_path).resolve()}:\n- " + "\n- ".join(failures)
        )

    for result in results:
        LOGGER.info("preflight %s %s", result.name, result.detail)
    return tuple(results)


__all__ = ["PreflightCheckResult", "RuntimePreflightError", "run_runtime_preflight"]
