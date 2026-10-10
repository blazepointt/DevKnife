"""Pydantic models shared by the CLI and command plugins."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from devknife.core.exit_codes import ExitCode


class OutputFormat(str, Enum):
    """Supported terminal output formats."""

    TEXT = "text"
    JSON = "json"
    TABLE = "table"


class ErrorLocation(BaseModel):
    """A source location associated with an input or parsing error."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    path: str | None = None


class CommandResult(BaseModel):
    """Normalized result returned by an application service."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    data: Any | None = None
    format: OutputFormat = OutputFormat.TEXT
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, value: list[str]) -> list[str]:
        """Normalize warning text and reject empty diagnostic entries."""

        normalized: list[str] = []
        for item in value:
            text = item.strip()
            if text:
                normalized.append(text)
        return normalized

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        """Ensure metric keys are usable in JSON and values are numeric."""

        return {str(key): float(metric) for key, metric in value.items()}


class ErrorReport(BaseModel):
    """Machine-readable representation of a handled application error."""

    model_config = ConfigDict(extra="forbid")

    code: int
    category: str
    message: str
    location: ErrorLocation | None = None
    hint: str | None = None
    request_id: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: int | ExitCode) -> int:
        """Serialize an ``ExitCode`` as its stable integer value."""

        return int(value)

    @field_validator("category", "message", "hint", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str | None:
        """Trim textual diagnostics while preserving optional hints."""

        if value is None:
            return None
        text = str(value).strip()
        return text or None
