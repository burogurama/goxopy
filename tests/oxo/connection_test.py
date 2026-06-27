"""Tests for the emit/emit_ack id-routing and shutdown handling of Connection."""

import io
import threading

import pytest

from oxo import connection
from oxo._note import note
from oxo._note import wire


def _ack(emit_id: int, status: str = note.STATUS_OK, error: str = "") -> note.EmitAck:
    return note.EmitAck(type=note.TYPE_EMIT_ACK, id=emit_id, status=status, error=error)


def testEmit_whenAcked_returns() -> None:
    conn = connection.Connection(io.BytesIO())

    waiter = threading.Thread(target=_route_when_registered, args=(conn, 1))
    waiter.start()
    conn.emit(0, "v3.report.vuln", {"title": "x"})
    waiter.join(timeout=2)

    assert waiter.is_alive() is False


def testEmit_whenRejected_raisesEmitRejectedError() -> None:
    conn = connection.Connection(io.BytesIO())
    waiter = threading.Thread(target=_route_when_registered, args=(conn, 1, note.STATUS_ERROR))
    waiter.start()

    with pytest.raises(connection.EmitRejectedError):
        conn.emit(0, "v3.not.declared", {"x": 1})

    waiter.join(timeout=2)


def testEmit_whenClosedBeforeAck_raisesEngineClosedError() -> None:
    conn = connection.Connection(io.BytesIO())
    blocked = threading.Event()
    error: list[BaseException] = []

    def emit_then_block() -> None:
        blocked.set()
        try:
            conn.emit(0, "v3.report.vuln", {"title": "x"})
        except connection.EngineClosedError as e:
            error.append(e)

    worker = threading.Thread(target=emit_then_block)
    worker.start()
    blocked.wait(timeout=2)
    _wait_for_waiter(conn)
    conn.close_acks()
    worker.join(timeout=2)

    assert len(error) == 1
    assert isinstance(error[0], connection.EngineClosedError)


def testEmit_whenAlreadyClosed_raisesEngineClosedError() -> None:
    conn = connection.Connection(io.BytesIO())
    conn.close_acks()

    with pytest.raises(connection.EngineClosedError):
        conn.emit(0, "v3.report.vuln", {"title": "x"})


def testRouteAck_whenNoWaiter_isNoOp() -> None:
    conn = connection.Connection(io.BytesIO())

    conn.route_ack(_ack(999))


def testCloseAcks_whenCalledTwice_isIdempotent() -> None:
    conn = connection.Connection(io.BytesIO())

    conn.close_acks()
    conn.close_acks()


def testEmit_whenSecondEmit_incrementsID() -> None:
    out = io.BytesIO()
    conn = connection.Connection(out)
    for emit_id in (1, 2):
        waiter = threading.Thread(target=_route_when_registered, args=(conn, emit_id))
        waiter.start()
        conn.emit(0, "v3.report.vuln", {"n": emit_id})
        waiter.join(timeout=2)

    out.seek(0)
    first: dict[str, object] = wire.read_frame(out)
    second: dict[str, object] = wire.read_frame(out)
    assert first["id"] == 1
    assert second["id"] == 2


def _wait_for_waiter(conn: connection.Connection) -> None:
    """Block until emit has registered its waiter under the connection lock."""
    while True:
        with conn._state_lock:
            if len(conn._acks) > 0:
                return


def _route_when_registered(conn: connection.Connection, emit_id: int, status: str = note.STATUS_OK) -> None:
    """Wait for emit to register, then route its ack (the single-reader role)."""
    _wait_for_waiter(conn)
    conn.route_ack(_ack(emit_id, status))
