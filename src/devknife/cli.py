"""DevKnife command-line entry point and global error boundary."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from devknife import __version__
from devknife.core.errors import DevKnifeError, InputError
from devknife.core.exit_codes import ExitCode
from devknife.core.models import CommandResult, ErrorReport, OutputFormat
from devknife.io.input_source import InputSource
from devknife.io.output_sink import OutputSink

app = typer.Typer(
    name="devknife",
    help="A modular Swiss-army-knife CLI for developers.",
    no_args_is_help=True,
    add_completion=False,
)


def _context_values(ctx: typer.Context) -> dict[str, Any]:
    if ctx.obj is None:
        ctx.obj = {}
    if not isinstance(ctx.obj, dict):
        raise RuntimeError("Typer context object must be a dictionary.")
    return ctx.obj


def _sink_for(ctx: typer.Context) -> OutputSink:
    values = _context_values(ctx)
    sink = values.get("sink")
    if isinstance(sink, OutputSink):
        return sink
    sink = OutputSink(values.get("output", OutputFormat.TEXT))
    values["sink"] = sink
    return sink


def _run_command(ctx: typer.Context, operation: Callable[[], CommandResult]) -> None:
    """Execute a command inside the single user-facing exception boundary."""

    values = _context_values(ctx)
    sink = _sink_for(ctx)
    request_id = str(values.get("request_id") or uuid.uuid4())
    debug = bool(values.get("debug", False))
    try:
        result = operation()
        if not isinstance(result, CommandResult):
            result = CommandResult(data=result)
        sink.write_result(result)
        sink.flush()
    except DevKnifeError as exc:
        report = exc.to_report(request_id=request_id)
        sink.write_error(report, debug=debug, exception=exc if debug else None)
        sink.flush()
        raise typer.Exit(code=int(exc.code)) from None
    except ValidationError as exc:
        error = InputError(
            "Input validation failed.",
            hint=_validation_hint(exc),
        )
        report = error.to_report(request_id=request_id)
        sink.write_error(report, debug=debug, exception=exc if debug else None)
        sink.flush()
        raise typer.Exit(code=int(error.code)) from None
    except (BrokenPipeError, OSError) as exc:
        error = InputError(
            f"I/O operation failed: {exc}",
            hint="Check the input/output stream and file permissions.",
        )
        report = error.to_report(request_id=request_id)
        sink.write_error(report, debug=debug, exception=exc if debug else None)
        sink.flush()
        raise typer.Exit(code=int(error.code)) from None
    except Exception as exc:  # noqa: BLE001 - intentional process-level safety boundary
        error = DevKnifeError(
            "The command failed unexpectedly.",
            code=ExitCode.INTERNAL_ERROR,
            hint="Retry with --debug to inspect the underlying exception.",
        )
        report = error.to_report(request_id=request_id)
        sink.write_error(report, debug=debug, exception=exc if debug else None)
        sink.flush()
        raise typer.Exit(code=int(error.code)) from None


def _validation_hint(error: ValidationError) -> str:
    details = error.errors(include_url=False)
    if not details:
        return "Check the supplied values and try again."
    first = details[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "input"
    message = str(first.get("msg", "invalid value"))
    return f"Field {location}: {message}"


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Include the underlying exception type in handled error reports.",
        is_flag=True,
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.TEXT,
        "--output",
        "-o",
        help="Output format: text, json, or table.",
        case_sensitive=False,
    ),
) -> None:
    """Configure global output and diagnostics behavior."""

    ctx.obj = {
        "debug": debug,
        "output": output,
        "request_id": str(uuid.uuid4()),
        "version": __version__,
    }


@app.command()
def ping(
    ctx: typer.Context,
    text: str | None = typer.Argument(
        None,
        help="Text to echo. When omitted, input is read from a pipe or --file.",
    ),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        help="Read the payload from a UTF-8 file.",
    ),
) -> None:
    """Echo input through the complete DevKnife input/output pipeline."""

    def operation() -> CommandResult:
        if file is not None:
            source = InputSource.from_file(file)
        elif text is not None:
            source = InputSource.from_argument(text)
        else:
            source = InputSource.from_stdin()
        return CommandResult(data=source.read_text())

    _run_command(ctx, operation)


if __name__ == "__main__":
    app()
