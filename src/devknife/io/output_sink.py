"""Safe terminal and machine-readable output adapters."""

from __future__ import annotations

import base64
import json
import sys
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel
from rich import box
from rich.console import Console, ConsoleRenderable
from rich.pretty import Pretty
from rich.table import Table

from devknife.core.errors import DevKnifeError
from devknife.core.models import CommandResult, ErrorReport, OutputFormat


class OutputSink:
    """Write command data to stdout and diagnostics to stderr.

    The sink deliberately owns both streams so tests and embedding applications
    can supply isolated buffers. ``BrokenPipeError`` is treated as a normal
    short-lived pipeline condition and never produces a traceback.
    """

    def __init__(
        self,
        output_format: OutputFormat | str = OutputFormat.TEXT,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        rich_enabled: bool | None = None,
    ) -> None:
        try:
            self.output_format = OutputFormat(output_format)
        except ValueError as exc:
            raise ValueError(f"Unsupported output format: {output_format!r}") from exc

        self.stdout = stdout if stdout is not None else sys.stdout
        self.stderr = stderr if stderr is not None else sys.stderr
        self.broken_pipe = False
        self._rich_enabled = (
            self._stream_is_tty(self.stdout) if rich_enabled is None else rich_enabled
        )
        self._console = Console(
            file=self.stdout,
            force_terminal=False,
            color_system=None,
            no_color=not self._rich_enabled,
            highlight=False,
            soft_wrap=True,
        )
        self._error_console = Console(
            file=self.stderr,
            force_terminal=False,
            color_system=None,
            no_color=True,
            highlight=False,
            soft_wrap=True,
        )

    def write_result(self, result: CommandResult | Any) -> None:
        """Render a normalized command result according to the selected format."""

        normalized = result if isinstance(result, CommandResult) else CommandResult(data=result)
        if self.output_format is OutputFormat.JSON:
            self._write_json(normalized.model_dump(mode="python"))
            return

        if self.output_format is OutputFormat.TABLE:
            self._write_table(normalized.data)
            self._write_warnings(normalized.warnings)
            return

        self._write_text_data(normalized.data)
        self._write_warnings(normalized.warnings)

    def write_data(self, data: Any) -> None:
        """Render arbitrary data using this sink's configured output format."""

        self.write_result(CommandResult(data=data))

    def write_error(
        self,
        error: ErrorReport | DevKnifeError,
        *,
        debug: bool = False,
        exception: BaseException | None = None,
    ) -> None:
        """Write a human-readable diagnostic or a machine-readable error."""

        report = error.to_report() if isinstance(error, DevKnifeError) else error
        if self.output_format is OutputFormat.JSON:
            self._write_error_json(report)
            return

        parts = [f"Error [{report.category}]", report.message]
        if report.location is not None:
            location_parts: list[str] = []
            if report.location.path:
                location_parts.append(report.location.path)
            if report.location.line is not None:
                location_parts.append(f"line {report.location.line}")
            if report.location.column is not None:
                location_parts.append(f"column {report.location.column}")
            if location_parts:
                parts.append(f"({', '.join(location_parts)})")
        self._write_stderr(" ".join(parts))
        if report.hint:
            self._write_stderr(f"Hint: {report.hint}")
        if debug and exception is not None:
            self._write_stderr(f"Debug exception: {type(exception).__name__}: {exception}")

    def write_stderr(self, message: str) -> None:
        """Write one diagnostic line to stderr without touching stdout."""

        self._write_stderr(message)

    def flush(self) -> None:
        """Flush both streams while suppressing broken-pipe failures."""

        for stream in (self.stdout, self.stderr):
            try:
                stream.flush()
            except BrokenPipeError:
                self.broken_pipe = True
            except (OSError, ValueError):
                # A closed stream must not turn a completed CLI operation into a traceback.
                continue

    def _write_text_data(self, data: Any) -> None:
        if data is None:
            return
        if isinstance(data, bytes):
            self._write_stdout(data.decode("utf-8", errors="replace"))
            return
        if isinstance(data, str):
            self._write_stdout(data if data.endswith("\n") else f"{data}\n")
            return
        if isinstance(data, (str, int, float, bool)):
            self._write_stdout(f"{data}\n")
            return
        if isinstance(data, ConsoleRenderable):
            self._print_renderable(data)
            return
        self._print_renderable(Pretty(data, indent_guides=False, expand_all=False))

    def _write_table(self, data: Any) -> None:
        if not isinstance(data, list) or not data:
            self._write_text_data(data)
            return
        if not all(isinstance(item, dict) for item in data):
            self._write_text_data(data)
            return

        keys: list[str] = []
        for item in data:
            for key in item:
                normalized_key = str(key)
                if normalized_key not in keys:
                    keys.append(normalized_key)
        table = Table(box=box.ASCII, show_header=True, header_style="bold")
        for key in keys:
            table.add_column(key)
        for item in data:
            normalized_item = {str(key): value for key, value in item.items()}
            table.add_row(
                *(self._display_value(normalized_item.get(key, "")) for key in keys)
            )
        self._print_renderable(table)

    def _write_warnings(self, warnings: list[str]) -> None:
        for warning in warnings:
            self._write_stderr(f"Warning: {warning}")

    def _write_json(self, value: Any) -> None:
        try:
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
                default=self._json_default,
            )
        except (TypeError, ValueError) as exc:
            self._write_stderr(f"Error [serialization_error] Unable to encode output as JSON: {exc}")
            return
        self._write_stdout(f"{serialized}\n")

    def _write_error_json(self, report: ErrorReport) -> None:
        self._write_json({"success": False, "error": report.model_dump(mode="python")})

    def _write_stdout(self, text: str) -> None:
        if self.broken_pipe:
            return
        try:
            self.stdout.write(text)
            self.stdout.flush()
        except BrokenPipeError:
            self.broken_pipe = True
        except (OSError, UnicodeError, ValueError):
            # Treat a closed output stream like a broken pipe.
            self.broken_pipe = True

    def _write_stderr(self, text: str) -> None:
        try:
            self.stderr.write(f"{text}\n")
            self.stderr.flush()
        except (BrokenPipeError, OSError, UnicodeError, ValueError):
            return

    def _print_renderable(self, renderable: Any) -> None:
        if self.broken_pipe:
            return
        try:
            self._console.print(renderable)
        except BrokenPipeError:
            self.broken_pipe = True
        except (OSError, UnicodeError, ValueError):
            self.broken_pipe = True

    @staticmethod
    def _stream_is_tty(stream: object) -> bool:
        try:
            return bool(stream.isatty())  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            return False

    @staticmethod
    def _display_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list, tuple, set)):
            return json.dumps(value, ensure_ascii=False, default=OutputSink._json_default)
        return str(value)

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        return str(value)
