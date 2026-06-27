"""Tests for the note wire framing."""

import io
import struct

import pytest

from oxo._note import note
from oxo._note import wire


def testWriteFrame_thenReadFrame_roundTrips() -> None:
    buf = io.BytesIO()

    wire.write_frame(buf, note.Emit(id=7, deliver=0, selector="v3.report.vuln", data={"title": "x"}).to_dict())
    buf.seek(0)
    got = wire.read_frame(buf)

    assert got["id"] == 7
    assert got["selector"] == "v3.report.vuln"
    assert got["type"] == note.TYPE_EMIT


def testReadFrame_whenStreamAtBoundary_raisesEOFError() -> None:
    with pytest.raises(EOFError):
        wire.read_frame(io.BytesIO(b""))


def testReadFrame_whenPrefixOversized_raisesFrameTooLargeError() -> None:
    header = struct.pack(">I", wire.MAX_FRAME_SIZE + 1)

    with pytest.raises(wire.FrameTooLargeError):
        wire.read_frame(io.BytesIO(header))


def testReadFrame_whenBodyNotObject_raisesError() -> None:
    body = b"[]"
    framed = struct.pack(">I", len(body)) + body

    with pytest.raises(wire.Error):
        wire.read_frame(io.BytesIO(framed))


def testWriteFrame_omitsZeroDeliverID() -> None:
    buf = io.BytesIO()

    wire.write_frame(buf, note.Emit(id=1, deliver=0, selector="s", data={}).to_dict())
    buf.seek(0)
    got = wire.read_frame(buf)

    assert "deliver" not in got


def testWriteFrame_keepsNonZeroDeliverID() -> None:
    buf = io.BytesIO()

    wire.write_frame(buf, note.Emit(id=1, deliver=7, selector="s", data={}).to_dict())
    buf.seek(0)
    got = wire.read_frame(buf)

    assert got["deliver"] == 7
