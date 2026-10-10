"""Bounded input adapters for command arguments, files, and stdin."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

from devknife.core.errors import InputError

DEFAULT_MAX_INPUT_SIZE = 64 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024


class InputSource:
    """Read one bounded input value from an argument, file, or standard input.

    A source is immutable after construction and can be read more than once for
    argument/file inputs. Standard input naturally becomes exhausted after its
    first read. Exactly one of ``argument`` and ``file_path`` may be supplied;
    if neither is supplied, the configured stdin stream is used.
    """

    def __init__(
        self,
        *,
        argument: str | bytes | None = None,
        file_path: str | Path | None = None,
        stdin: TextIO | BinaryIO | None = None,
        max_size: int = DEFAULT_MAX_INPUT_SIZE,
    ) -> None:
        if argument is not None and file_path is not None:
            raise InputError(
                "Only one input source can be selected.",
                hint="Provide a value or --file, then use stdin when neither is specified.",
            )
        if argument is not None and not isinstance(argument, (str, bytes)):
            raise InputError("Argument input must be text or bytes.")
        if file_path is not None and not isinstance(file_path, (str, Path)):
            raise InputError("Input file path must be a string or pathlib.Path.")
        if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size <= 0:
            raise InputError("Input size limit must be a positive integer.")

        self._argument = argument
        self._file_path = Path(file_path) if file_path is not None else None
        self._stdin = stdin if stdin is not None else sys.stdin
        self._max_size = max_size

    @classmethod
    def from_argument(
        cls,
        value: str | bytes,
        *,
        max_size: int = DEFAULT_MAX_INPUT_SIZE,
    ) -> "InputSource":
        """Build a source backed by a literal command-line value."""

        if not isinstance(value, (str, bytes)):
            raise InputError("Argument input must be text or bytes.")
        return cls(argument=value, max_size=max_size)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        max_size: int = DEFAULT_MAX_INPUT_SIZE,
    ) -> "InputSource":
        """Build a source backed by a filesystem path."""

        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise InputError("Input file path must be a non-empty path.")
        return cls(file_path=path, max_size=max_size)

    @classmethod
    def from_stdin(
        cls,
        stream: TextIO | BinaryIO | None = None,
        *,
        max_size: int = DEFAULT_MAX_INPUT_SIZE,
    ) -> "InputSource":
        """Build a source backed by a supplied stream or ``sys.stdin``."""

        return cls(stdin=stream, max_size=max_size)

    @property
    def max_size(self) -> int:
        """Maximum number of bytes accepted from this source."""

        return self._max_size

    @property
    def description(self) -> str:
        """Human-readable source description used in diagnostics."""

        if self._argument is not None:
            return "argument"
        if self._file_path is not None:
            return f"file {self._file_path}"
        return "stdin"

    def read(self, *, binary: bool = False, encoding: str = "utf-8") -> bytes | str:
        """Read the source as bytes or strictly decoded text."""

        raw = self._read_bytes()
        if binary:
            return raw
        try:
            return raw.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            if isinstance(exc, LookupError):
                raise InputError(
                    f"Unknown text encoding: {encoding!r}.",
                    hint="Use a valid Python codec name, for example utf-8.",
                ) from exc
            raise InputError(
                f"Input from {self.description} is not valid {encoding} text.",
                hint="Convert the input to UTF-8 or pass binary mode to the command.",
            ) from exc

    def read_text(self, *, encoding: str = "utf-8") -> str:
        """Read and strictly decode text input."""

        value = self.read(binary=False, encoding=encoding)
        assert isinstance(value, str)
        return value

    def read_bytes(self) -> bytes:
        """Read binary input while enforcing the configured byte limit."""

        value = self.read(binary=True)
        assert isinstance(value, bytes)
        return value

    def _read_bytes(self) -> bytes:
        if self._argument is not None:
            if isinstance(self._argument, bytes):
                raw = self._argument
            else:
                try:
                    raw = self._argument.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise InputError("Argument input cannot be encoded as UTF-8.") from exc
            self._ensure_within_limit(len(raw))
            return raw

        if self._file_path is not None:
            return self._read_file(self._file_path)

        if self._stream_is_tty(self._stdin):
            raise InputError(
                "No input was provided.",
                hint="Pass a value, use --file, or pipe data through stdin.",
            )
        return self._read_stream(self._stdin)

    def _read_file(self, path: Path) -> bytes:
        try:
            if not path.exists():
                raise InputError(f"Input file does not exist: {path}")
            if not path.is_file():
                raise InputError(f"Input path is not a regular file: {path}")
            with path.open("rb") as handle:
                return self._read_stream(handle)
        except InputError:
            raise
        except OSError as exc:
            raise InputError(
                f"Unable to read input file {path}: {exc}",
                hint="Check the path and file permissions.",
            ) from exc

    def _read_stream(self, stream: TextIO | BinaryIO) -> bytes:
        binary_stream: BinaryIO | TextIO = stream
        if not self._is_binary_stream(stream):
            possible_buffer = getattr(stream, "buffer", None)
            if possible_buffer is not None:
                binary_stream = possible_buffer

        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = binary_stream.read(_READ_CHUNK_SIZE)
                if chunk in (b"", ""):
                    break
                if isinstance(chunk, str):
                    try:
                        encoded = chunk.encode("utf-8")
                    except UnicodeEncodeError as exc:
                        raise InputError(
                            f"Input from {self.description} cannot be encoded as UTF-8."
                        ) from exc
                elif isinstance(chunk, bytes):
                    encoded = chunk
                else:
                    raise InputError(
                        f"Input stream {self.description} returned an unsupported value."
                    )
                total += len(encoded)
                self._ensure_within_limit(total)
                chunks.append(encoded)
        except InputError:
            raise
        except (OSError, ValueError) as exc:
            raise InputError(
                f"Unable to read from {self.description}: {exc}",
                hint="Check that the input stream is open and readable.",
            ) from exc
        return b"".join(chunks)

    @staticmethod
    def _is_binary_stream(stream: object) -> bool:
        return isinstance(stream, (io.RawIOBase, io.BufferedIOBase, io.BytesIO))

    @staticmethod
    def _stream_is_tty(stream: object) -> bool:
        try:
            return bool(stream.isatty())  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            # Test doubles and closed streams often do not implement isatty.
            return False

    def _ensure_within_limit(self, size: int) -> None:
        if size > self._max_size:
            limit_mib = self._max_size / (1024 * 1024)
            raise InputError(
                f"Input from {self.description} exceeds the {limit_mib:g} MiB limit.",
                hint="Use a smaller input or raise the command-specific limit.",
            )
