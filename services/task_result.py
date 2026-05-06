from dataclasses import dataclass


@dataclass
class TaskResult:
    success: bool
    path: str | None = None
    paths: dict | None = None
    error: str | None = None
