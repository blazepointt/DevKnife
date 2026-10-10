"""Typed errors raised by DevKnife application services."""

from __future__ import annotations

from typing import Any

from devknife.core.exit_codes import ExitCode
from devknife.core.models import ErrorLocation, ErrorReport


class DevKnifeError(Exception):
    """Base class for expected, user-facing DevKnife failures."""

    default_code = ExitCode.INTERNAL_ERROR
    category = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: ExitCode | int | None = None,
        location: ErrorLocation | dict[str, Any] | None = None,
        line: int | None = None,
        column: int | None = None,
        offset: int | None = None,
        path: str | None = None,
        hint: str | None = None,
    ) -> None:
        normalized_message = str(message).strip() or "An unspecified DevKnife error occurred."
        if location is not None and any(
            value is not None for value in (line, column, offset, path)
        ):
            raise ValueError("location and individual location fields cannot be combined")

        if location is not None and not isinstance(location, ErrorLocation):
            location = ErrorLocation.model_validate(location)
        elif location is None and any(
            value is not None for value in (line, column, offset, path)
        ):
            location = ErrorLocation(
                line=line,
                column=column,
                offset=offset,
                path=path,
            )

        self.code = ExitCode(self.default_code if code is None else code)
        self.message = normalized_message
        self.location = location
        self.hint = str(hint).strip() if hint is not None and str(hint).strip() else None
        super().__init__(self.message)

    def to_report(self, *, request_id: str | None = None) -> ErrorReport:
        """Convert the exception into the stable error model used by renderers."""

        return ErrorReport(
            code=int(self.code),
            category=self.category,
            message=self.message,
            location=self.location,
            hint=self.hint,
            request_id=request_id,
        )

    def __str__(self) -> str:
        """Return a concise message suitable for logs and exception chaining."""

        return self.message


class InputError(DevKnifeError):
    """The requested input is missing, malformed, or inaccessible."""

    default_code = ExitCode.INVALID_USAGE
    category = "input_error"


class ParsingError(InputError):
    """A supplied document could not be parsed or validated."""

    category = "parsing_error"


class NetworkError(DevKnifeError):
    """A network operation failed or exceeded its timeout."""

    default_code = ExitCode.NETWORK_ERROR
    category = "network_error"


class CryptoError(DevKnifeError):
    """A cryptographic operation failed or its input was invalid."""

    default_code = ExitCode.CRYPTO_ERROR
    category = "crypto_error"
