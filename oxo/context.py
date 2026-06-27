"""The handler-facing values: Context, Message, Identity, and Meta."""

import collections.abc
import dataclasses
import logging
from typing import Any

from oxo import connection


@dataclasses.dataclass(frozen=True)
class Identity:
    """Names the agent instance the handler runs as.

    It mirrors the OXO agent identity.
    """

    agent: str
    key: str
    universe: str


@dataclasses.dataclass(frozen=True)
class Meta:
    """The per-message metadata that travelled with a deliver."""

    message_id: str
    headers: collections.abc.Mapping[str, Any] | None


@dataclasses.dataclass(frozen=True)
class Message:
    """One decoded scan-message handed to a message handler.

    Data is the proto fields as a plain dict; the engine owns the codec, so a
    handler never sees protobuf.
    """

    selector: str
    data: collections.abc.Mapping[str, Any]
    meta: Meta


class Context:
    """A handler's window onto one phase.

    It carries the agent's identity and config, a logger, and an emitter. It is
    valid only for the duration of the handler call.
    """

    def __init__(
        self,
        identity: Identity,
        config: collections.abc.Mapping[str, Any] | None,
        log: logging.Logger,
        conn: connection.Connection,
        deliver_id: int,
    ) -> None:
        self._identity = identity
        self._config = config
        self._log = log
        self._conn = conn
        self._deliver_id = deliver_id

    @property
    def identity(self) -> Identity:
        """The agent instance the handler runs as."""
        return self._identity

    @property
    def config(self) -> collections.abc.Mapping[str, Any] | None:
        """The agent's configuration as a plain dict, or None when none was sent."""
        return self._config

    @property
    def log(self) -> logging.Logger:
        """The handler's logger. It writes to stderr; stdout is reserved for the protocol."""
        return self._log

    def emit(self, selector: str, data: collections.abc.Mapping[str, Any]) -> None:
        """Publish data on a declared output selector and wait for the engine.

        Args:
            selector: One of the agent's declared output selectors.
            data: The dict payload to publish.

        Raises:
            connection.EmitRejectedError: If the engine rejected the emit (an
                undeclared selector, or a publish failure).
            connection.EngineClosedError: If the engine closed the connection
                before acknowledging the emit.
        """
        self._conn.emit(self._deliver_id, selector, data)
