"""DevKnife Workbench — FastAPI backend.

Run:
    pip install fastapi "uvicorn[standard]"
    python web_app.py            # http://127.0.0.1:8000
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from devknife import __version__
from devknife.core.errors import DevKnifeError, InputError
from devknife.core.exit_codes import ExitCode
from devknife.core.models import CommandResult, ErrorReport, OutputFormat
from devknife.io.input_source import InputSource

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
INDEX_FILE = WEB_DIR / "index.html"

app = FastAPI(
    title="DevKnife Workbench",
    version=__version__,
    description="Local web interface for the DevKnife CLI core.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="")


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="")
    source: Literal["json", "yaml", "toml"] = "json"
    target: Literal["json", "yaml", "toml"] = "yaml"


class JwtRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(default="")
    secret: str | None = None
    action: Literal["decode", "verify", "encode"] = "decode"


class PasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    length: int = Field(default=20, ge=4, le=256)
    uppercase: bool = True
    lowercase: bool = True
    digits: bool = True
    symbols: bool = True
    count: int = Field(default=1, ge=1, le=100)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(report: ErrorReport, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "data": None,
            "format": OutputFormat.TEXT.value,
            "warnings": [report.message] + ([report.hint] if report.hint else []),
            "metrics": {},
            "error": report.model_dump(mode="json"),
        },
    )


def _not_implemented(feature: str, started: float) -> JSONResponse:
    result = CommandResult(
        success=False,
        data=None,
        format=OutputFormat.TEXT,
        warnings=[f"{feature} is not implemented yet."],
        metrics={"duration_ms": round((time.perf_counter() - started) * 1000, 3)},
    )
    return JSONResponse(status_code=501, content=result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Global error boundary (mirrors the CLI boundary in cli.py)
# ---------------------------------------------------------------------------


@app.exception_handler(DevKnifeError)
async def devknife_error_handler(_: Request, exc: DevKnifeError) -> JSONResponse:
    report = exc.to_report(request_id=str(uuid.uuid4()))
    status = 422 if isinstance(exc, InputError) else 500
    return _error_response(report, status)


@app.exception_handler(ValidationError)
async def pydantic_error_handler(_: Request, exc: ValidationError) -> JSONResponse:
    error = InputError("Input validation failed.", hint=str(exc.errors(include_url=False)[0].get("msg", "invalid value")))
    return _error_response(error.to_report(request_id=str(uuid.uuid4())), 422)


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error = DevKnifeError(
        "The command failed unexpectedly.",
        code=ExitCode.INTERNAL_ERROR,
        hint=f"{type(exc).__name__}: {exc}",
    )
    return _error_response(error.to_report(request_id=str(uuid.uuid4())), 500)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "service": "devknife-workbench", "version": __version__}


@app.post("/api/ping", response_model=CommandResult)
async def ping(payload: PingRequest) -> CommandResult:
    """Run the same input -> output pipeline as the `devknife ping` command."""

    started = time.perf_counter()
    source = InputSource.from_argument(payload.text)
    text = source.read_text()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return CommandResult(
        success=True,
        data=text,
        format=OutputFormat.TEXT,
        warnings=[],
        metrics={
            "duration_ms": round(elapsed_ms, 3),
            "input_chars": float(len(text)),
            "input_lines": float(len(text.splitlines())),
        },
    )


@app.post("/api/format/convert")
async def format_convert(payload: ConvertRequest) -> JSONResponse:
    return _not_implemented(f"Format conversion {payload.source} -> {payload.target}", time.perf_counter())


@app.post("/api/crypto/jwt")
async def crypto_jwt(payload: JwtRequest) -> JSONResponse:
    return _not_implemented(f"JWT {payload.action}", time.perf_counter())


@app.post("/api/gen/password")
async def gen_password(payload: PasswordRequest) -> JSONResponse:
    return _not_implemented("Password generator", time.perf_counter())


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE, media_type="text/html")


WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)
