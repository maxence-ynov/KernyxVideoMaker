from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import json
from typing import Any, Mapping
from uuid import uuid4

JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return _utc_now()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _normalize_json_value(value: Any) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]

    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}

    raise TypeError(
        "metadata must only contain JSON-serializable values "
        f"(got {type(value).__name__})"
    )


class VideoJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(slots=True)
class VideoJob:
    topic: str
    template: str
    account: str
    job_id: str = field(default_factory=lambda: uuid4().hex)
    status: VideoJobStatus = VideoJobStatus.PENDING
    errors: list[str] = field(default_factory=list)
    file_paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, JSONValue] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        self.topic = self.topic.strip()
        self.template = self.template.strip()
        self.account = self.account.strip()
        self.job_id = self.job_id.strip()
        self.status = VideoJobStatus(self.status)
        self.errors = [str(error).strip() for error in self.errors if str(error).strip()]
        self.file_paths = {
            str(name): str(path)
            for name, path in self.file_paths.items()
            if str(name).strip() and str(path).strip()
        }
        self.metadata = {
            key: _normalize_json_value(value)
            for key, value in self.metadata.items()
        }
        self.created_at = _parse_datetime(self.created_at)
        self.updated_at = _parse_datetime(self.updated_at)

        if not self.topic:
            raise ValueError("topic must not be empty")
        if not self.template:
            raise ValueError("template must not be empty")
        if not self.account:
            raise ValueError("account must not be empty")
        if not self.job_id:
            raise ValueError("job_id must not be empty")

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def set_status(self, status: VideoJobStatus) -> None:
        self.status = VideoJobStatus(status)
        self.touch()

    def add_error(self, error: str) -> None:
        message = error.strip()
        if not message:
            return

        self.errors.append(message)
        self.touch()

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "job_id": self.job_id,
            "topic": self.topic,
            "template": self.template,
            "account": self.account,
            "status": self.status.value,
            "errors": list(self.errors),
            "file_paths": dict(self.file_paths),
            "metadata": {
                key: _normalize_json_value(value)
                for key, value in self.metadata.items()
            },
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VideoJob:
        return cls(
            job_id=str(data.get("job_id") or uuid4().hex),
            topic=str(data["topic"]),
            template=str(data["template"]),
            account=str(data["account"]),
            status=VideoJobStatus(data.get("status", VideoJobStatus.PENDING)),
            errors=list(data.get("errors", [])),
            file_paths=dict(data.get("file_paths", {})),
            metadata=dict(data.get("metadata", {})),
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )

    @classmethod
    def from_json(cls, payload: str) -> VideoJob:
        return cls.from_dict(json.loads(payload))
