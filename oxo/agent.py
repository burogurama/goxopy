"""The Agent: handler registration and the engine run loop.

An author registers a handler per selector, then calls run. run reads the
engine's init, then serves every delivered message until the engine closes
stdin. Each deliver runs on its own thread, so one process handles several at
once. Logs go to stderr; stdout is reserved for the protocol.
"""

import collections.abc
import logging
import sys
import threading
from typing import Any
from typing import BinaryIO
from typing import overload

from oxo import connection
from oxo import context
from oxo._note import note
from oxo._note import wire

# A message handler. Raising fails the message: the engine nacks it (poison
# messages are dropped, not requeued).
type MessageHandler = collections.abc.Callable[[context.Context, context.Message], None]


def _stderr_logger() -> logging.Logger:
    """Build the agent logger that writes to stderr; stdout is reserved for the protocol."""
    logger: logging.Logger = logging.getLogger("oxo")
    if len(logger.handlers) == 0:
        handler: logging.StreamHandler[Any] = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


_LOGGER = _stderr_logger()


class Error(Exception):
    """Base error for the oxo agent."""


class ProtocolError(Error):
    """Raised when the engine's IPC breaks (a missing or malformed note)."""


class Agent:
    """Collects the handlers an author registers, then serves the handler process.

    run reads the engine's init, then handles every delivered message until the
    engine closes stdin.
    """

    def __init__(self) -> None:
        self._on_message: dict[str, MessageHandler] = {}
        self._log = _LOGGER

    @overload
    def on_message(
        self, selector: str, fn: None = None
    ) -> collections.abc.Callable[[MessageHandler], MessageHandler]: ...

    @overload
    def on_message(self, selector: str, fn: MessageHandler) -> Agent: ...

    def on_message(
        self, selector: str, fn: MessageHandler | None = None
    ) -> collections.abc.Callable[[MessageHandler], MessageHandler] | Agent:
        """Register fn as the handler for selector.

        Usable as a decorator (``@agent.on_message("v3.asset.ip")``) or called
        directly. A later registration for the same selector replaces an
        earlier one.

        Args:
            selector: The input selector the handler answers.
            fn: The handler; omitted when used as a decorator.

        Returns:
            The decorator when fn is omitted, otherwise the agent for chaining.
        """
        if fn is None:
            return self._message_decorator(selector)
        self._on_message[selector] = fn
        return self

    def run(self) -> None:
        """Serve the handler process's whole life on stdin/stdout.

        Raises:
            ProtocolError: If the IPC broke (a missing or malformed note). A
                failing handler is not raised here; it is reported to the engine
                as a done with an error status.
        """
        self._run(sys.stdin.buffer, sys.stdout.buffer)

    def _message_decorator(self, selector: str) -> collections.abc.Callable[[MessageHandler], MessageHandler]:
        def register(fn: MessageHandler) -> MessageHandler:
            self._on_message[selector] = fn
            return fn

        return register

    def _run(self, r: BinaryIO, w: BinaryIO) -> None:
        init: note.Init = self._read_init(r)
        ident: context.Identity = context.Identity(
            agent=init.identity.agent, key=init.identity.key, universe=init.identity.universe
        )
        conn: connection.Connection = connection.Connection(w)
        workers: list[threading.Thread] = []
        try:
            self._serve(r, conn, ident, init.config, workers)
        finally:
            conn.close_acks()
            for worker in workers:
                worker.join()

    def _read_init(self, r: BinaryIO) -> note.Init:
        try:
            body: dict[str, Any] = wire.read_frame(r)
            init: note.Init = note.Init.from_dict(body)
        except (EOFError, wire.Error) as e:
            raise ProtocolError(f"oxo: read init: {e}") from e
        if init.type != note.TYPE_INIT:
            raise ProtocolError(f"oxo: expected init, got {init.type!r}")
        if init.protocol != note.PROTOCOL_VERSION:
            raise ProtocolError(f"oxo: unsupported protocol version {init.protocol}, want {note.PROTOCOL_VERSION}")
        return init

    def _serve(
        self,
        r: BinaryIO,
        conn: connection.Connection,
        ident: context.Identity,
        config: collections.abc.Mapping[str, Any] | None,
        workers: list[threading.Thread],
    ) -> None:
        """Read notes until EOF (clean shutdown) or a malformed frame (error).

        On EOF it returns so the caller can release waiting emits and join the
        in-flight handlers. A malformed frame or undecodable note raises
        ProtocolError.
        """
        while True:
            try:
                body: dict[str, Any] = wire.read_frame(r)
            except EOFError:
                return
            except wire.Error as e:
                raise ProtocolError(f"oxo: read note: {e}") from e
            try:
                self._dispatch(body, conn, ident, config, workers)
            except wire.Error as e:
                raise ProtocolError(f"oxo: dispatch note: {e}") from e

    def _dispatch(
        self,
        body: collections.abc.Mapping[str, Any],
        conn: connection.Connection,
        ident: context.Identity,
        config: collections.abc.Mapping[str, Any] | None,
        workers: list[threading.Thread],
    ) -> None:
        note_type: str = str(body.get("type", ""))
        if note_type == note.TYPE_DELIVER:
            self._dispatch_deliver(body, conn, ident, config, workers)
        elif note_type == note.TYPE_EMIT_ACK:
            conn.route_ack(note.EmitAck.from_dict(body))
        elif note_type == note.TYPE_SHUTDOWN:
            pass  # Advisory: the engine also closes stdin, which is the real cue.
        else:
            self._log.warning("oxo: ignoring unexpected note: %s", note_type)

    def _dispatch_deliver(
        self,
        body: collections.abc.Mapping[str, Any],
        conn: connection.Connection,
        ident: context.Identity,
        config: collections.abc.Mapping[str, Any] | None,
        workers: list[threading.Thread],
    ) -> None:
        dlv: note.Deliver = note.Deliver.from_dict(body)
        conn.write_note(note.Pickup(id=dlv.id).to_dict())
        worker: threading.Thread = threading.Thread(target=self._handle_deliver, args=(conn, ident, config, dlv))
        worker.start()
        self._track(workers, worker)

    def _track(self, workers: list[threading.Thread], worker: threading.Thread) -> None:
        """Record a started worker, pruning finished ones first.

        Only the run loop touches workers, so this is race-free. Pruning keeps
        the list bounded by the number of in-flight handlers rather than by the
        total number of messages the process has handled.
        """
        workers[:] = [w for w in workers if w.is_alive() is True]
        workers.append(worker)

    def _handle_deliver(
        self,
        conn: connection.Connection,
        ident: context.Identity,
        config: collections.abc.Mapping[str, Any] | None,
        dlv: note.Deliver,
    ) -> None:
        """Run one message's handler and report its outcome as a done.

        A selector with no handler fails the message: the engine binds the queue
        to declared inputs, so an unrouted deliver is a misconfiguration. An
        exception in the handler is caught and reported as an error done, so one
        bad message cannot take down the other messages sharing this process.
        """
        error: str | None = self._call_deliver(conn, ident, config, dlv)
        self._report_done(conn, dlv.id, error)

    def _call_deliver(
        self,
        conn: connection.Connection,
        ident: context.Identity,
        config: collections.abc.Mapping[str, Any] | None,
        dlv: note.Deliver,
    ) -> str | None:
        fn: MessageHandler | None = self._on_message.get(dlv.selector)
        if fn is None:
            return f"no handler registered for selector {dlv.selector!r}"
        ctx: context.Context = context.Context(ident, config, self._log, conn, dlv.id)
        msg: context.Message = context.Message(
            selector=dlv.selector,
            data=dlv.data,
            meta=context.Meta(message_id=dlv.meta.message_id, headers=dlv.meta.headers),
        )
        try:
            fn(ctx, msg)
        except Exception as e:  # noqa: BLE001 — a handler may raise anything; one bad message must not crash the process.
            return f"oxo: handler raised: {e}"
        return None

    def _report_done(self, conn: connection.Connection, deliver_id: int, error: str | None) -> None:
        try:
            conn.write_note(note.done_for(deliver_id, error).to_dict())
        except wire.Error as e:
            self._log.error("oxo: write done failed: id=%d err=%s", deliver_id, e)
