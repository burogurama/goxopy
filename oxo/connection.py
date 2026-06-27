"""The shared stdin/stdout transport for one run.

Concurrent handler threads write notes (pickup, emit, done) onto stdout,
serialised by a lock; an emit then waits for the engine's emit_ack, which the
run loop (the only reader) routes back to it by id. The connection closes when
the engine closes stdin, releasing any emit awaiting an ack that will not come.
"""

import collections.abc
import queue
import threading
from typing import Any
from typing import BinaryIO

from oxo._note import note
from oxo._note import wire


class Error(Exception):
    """Base error for the connection."""


class EmitRejectedError(Error):
    """Raised when the engine rejects an emit (undeclared selector or failure)."""


class EngineClosedError(Error):
    """Raised when the engine closes the connection before acknowledging an emit."""


class Connection:
    """The serialised note transport shared by all of a run's handler threads."""

    def __init__(self, w: BinaryIO) -> None:
        self._w = w
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._next_emit = 0
        self._acks: dict[int, queue.SimpleQueue[note.EmitAck]] = {}
        self._closed = False

    def write_note(self, body: collections.abc.Mapping[str, Any]) -> None:
        """Serialise one handler-to-engine note onto stdout.

        Args:
            body: The note's JSON body.

        Raises:
            wire.Error: If the frame could not be written.
        """
        with self._write_lock:
            wire.write_frame(self._w, body)

    def emit(self, deliver: int, selector: str, data: collections.abc.Mapping[str, Any]) -> None:
        """Publish data on selector for the message named by deliver, then wait.

        Args:
            deliver: The deliver id of the message this emit answers, or
                note.START_ID for a start-phase emit.
            selector: The declared output selector to publish on.
            data: The dict payload to publish.

        Raises:
            EmitRejectedError: If the engine rejected the emit.
            EngineClosedError: If the engine closed the connection before the
                ack arrived; the emit may have been published, but its outcome
                is unconfirmed.
        """
        ack_queue: queue.SimpleQueue[note.EmitAck] = queue.SimpleQueue()
        with self._state_lock:
            if self._closed is True:
                raise EngineClosedError(f"oxo: emit {selector!r} unacknowledged: engine closed the connection")
            self._next_emit += 1
            emit_id: int = self._next_emit
            self._acks[emit_id] = ack_queue

        try:
            self.write_note(note.Emit(id=emit_id, deliver=deliver, selector=selector, data=data).to_dict())
            ack: note.EmitAck | None = ack_queue.get()
            if ack is None:
                raise EngineClosedError(f"oxo: emit {selector!r} unacknowledged: engine closed the connection")
            if ack.status == note.STATUS_ERROR:
                raise EmitRejectedError(f"oxo: emit {selector!r} rejected: {ack.error}")
        finally:
            with self._state_lock:
                self._acks.pop(emit_id, None)

    def route_ack(self, ack: note.EmitAck) -> None:
        """Hand an emit_ack to the emit waiting on its id.

        An ack with no waiter (the emit already returned, or none was sent) is
        dropped.
        """
        with self._state_lock:
            ack_queue: queue.SimpleQueue[note.EmitAck] | None = self._acks.get(ack.id)
        if ack_queue is not None:
            ack_queue.put(ack)

    def close_acks(self) -> None:
        """Release every emit still awaiting an ack.

        The engine has closed stdin, so no further acks will arrive; each waiter
        is woken with a sentinel that surfaces as an EngineClosedError.
        """
        with self._state_lock:
            if self._closed is True:
                return
            self._closed = True
            waiters: list[queue.SimpleQueue[note.EmitAck]] = list(self._acks.values())
        for ack_queue in waiters:
            ack_queue.put(None)  # type: ignore[arg-type]
