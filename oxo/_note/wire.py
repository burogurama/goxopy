"""Wire framing for notes: a 4-byte big-endian length prefix then a JSON body."""

import collections.abc
import json
import struct
from typing import Any
from typing import BinaryIO

# Caps a single note's JSON body. Frames larger than this are refused rather
# than allocated, so a corrupt or hostile length prefix can't drive an unbounded
# read. It stays below 4 GiB so a body length always fits the 4-byte prefix.
MAX_FRAME_SIZE = 256 << 20

_HEADER = struct.Struct(">I")


class Error(Exception):
    """Base error for the note wire."""


class FrameTooLargeError(Error):
    """Raised when a frame's declared length exceeds MAX_FRAME_SIZE."""


def write_frame(w: BinaryIO, body: collections.abc.Mapping[str, Any]) -> None:
    """Encode body to compact JSON and write it as one frame, then flush.

    Args:
        w: The binary stream the frame is written to (the protocol's stdout).
        body: The note as a JSON-serialisable mapping.

    Raises:
        FrameTooLargeError: If the encoded body exceeds MAX_FRAME_SIZE.
    """
    encoded: bytes = json.dumps(body, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_SIZE:
        raise FrameTooLargeError(f"note: frame exceeds {MAX_FRAME_SIZE} bytes")
    w.write(_HEADER.pack(len(encoded)))
    w.write(encoded)
    w.flush()


def read_frame(r: BinaryIO) -> dict[str, Any]:
    """Read one frame from r and return its decoded JSON body.

    Args:
        r: The binary stream frames are read from (the protocol's stdin).

    Returns:
        The note's JSON body decoded into a dict.

    Raises:
        EOFError: When the stream is cleanly at a frame boundary (the engine
            closed stdin) or when a frame is truncated.
        FrameTooLargeError: If the declared length exceeds MAX_FRAME_SIZE.
        Error: If the body is not a JSON object.
    """
    header: bytes = _read_exactly(r, _HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_SIZE:
        raise FrameTooLargeError(f"note: frame exceeds {MAX_FRAME_SIZE} bytes")
    body: bytes = _read_exactly(r, length)
    decoded: Any = json.loads(body)
    if isinstance(decoded, dict) is False:
        raise Error("note: frame body is not a JSON object")
    result: dict[str, Any] = decoded
    return result


def _read_exactly(r: BinaryIO, n: int) -> bytes:
    """Read exactly n bytes, blocking until they arrive.

    A clean stream end at a read boundary and a short read mid-frame both raise
    EOFError; the run loop treats EOF at a frame boundary as a normal shutdown.
    """
    buf: bytearray = bytearray()
    while len(buf) < n:
        chunk: bytes = r.read(n - len(buf))
        if len(chunk) == 0:
            raise EOFError("note: stream closed")
        buf.extend(chunk)
    return bytes(buf)
