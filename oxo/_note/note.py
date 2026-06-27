"""Note types for the goxo engine handler IPC.

Engine to handler: init, start, deliver, emit_ack, shutdown.
Handler to engine: pickup, emit, done.
"""

import collections.abc
import dataclasses
from typing import Any

# Note type tags carried in every note's "type" field.
TYPE_INIT = "init"
TYPE_START = "start"
TYPE_DELIVER = "deliver"
TYPE_EMIT_ACK = "emit_ack"
TYPE_SHUTDOWN = "shutdown"
TYPE_PICKUP = "pickup"
TYPE_EMIT = "emit"
TYPE_DONE = "done"

# Outcome statuses carried by emit_ack and done.
STATUS_OK = "ok"
STATUS_ERROR = "error"

# The note IPC version this handler speaks. The engine declares its version in
# init; a mismatch means the wire contract has diverged.
PROTOCOL_VERSION = 2

# The reserved id for the start phase: a start note carries no id, so its done
# echoes 0 and its emits carry no deliver id.
START_ID = 0


@dataclasses.dataclass(frozen=True)
class Identity:
    """Names the agent instance the handler runs as."""

    agent: str
    key: str
    universe: str

    @classmethod
    def from_dict(cls, raw: collections.abc.Mapping[str, Any]) -> Identity:
        """Read an identity from the init note's "identity" object."""
        return cls(
            agent=str(raw.get("agent", "")),
            key=str(raw.get("key", "")),
            universe=str(raw.get("universe", "")),
        )


@dataclasses.dataclass(frozen=True)
class Meta:
    """Per-message metadata that travels with a deliver."""

    message_id: str
    headers: collections.abc.Mapping[str, Any] | None

    @classmethod
    def from_dict(cls, raw: collections.abc.Mapping[str, Any] | None) -> Meta:
        """Read a deliver's "meta" object, tolerating an absent meta or fields."""
        if raw is None:
            return cls(message_id="", headers=None)
        return cls(
            message_id=str(raw.get("message_id", "")),
            headers=raw.get("headers"),
        )


@dataclasses.dataclass(frozen=True)
class Init:
    """The first note the engine sends.

    It hands the handler the protocol version, identity, agent config, and the
    declared input selectors. Config and inputs may be absent.
    """

    type: str
    protocol: int
    identity: Identity
    config: collections.abc.Mapping[str, Any] | None
    inputs: collections.abc.Sequence[str] | None

    @classmethod
    def from_dict(cls, raw: collections.abc.Mapping[str, Any]) -> Init:
        """Read an init note from its decoded JSON body."""
        return cls(
            type=str(raw.get("type", "")),
            protocol=int(raw.get("protocol", 0)),
            identity=Identity.from_dict(raw.get("identity", {})),
            config=raw.get("config"),
            inputs=raw.get("inputs"),
        )


@dataclasses.dataclass(frozen=True)
class Deliver:
    """One decoded scan-message handed to the handler.

    Data is the proto fields as a plain JSON-like dict; the engine owns the
    codec, so a handler never sees protobuf.
    """

    type: str
    id: int
    selector: str
    data: collections.abc.Mapping[str, Any]
    meta: Meta

    @classmethod
    def from_dict(cls, raw: collections.abc.Mapping[str, Any]) -> Deliver:
        """Read a deliver note from its decoded JSON body."""
        return cls(
            type=str(raw.get("type", "")),
            id=int(raw.get("id", 0)),
            selector=str(raw.get("selector", "")),
            data=raw.get("data") or {},
            meta=Meta.from_dict(raw.get("meta")),
        )


@dataclasses.dataclass(frozen=True)
class EmitAck:
    """Answers a handler emit: ok if the engine published it, error otherwise.

    The error reason is present only on error. The ack is advisory; done stays
    authoritative for the message outcome.
    """

    type: str
    id: int
    status: str
    error: str

    @classmethod
    def from_dict(cls, raw: collections.abc.Mapping[str, Any]) -> EmitAck:
        """Read an emit_ack note from its decoded JSON body."""
        return cls(
            type=str(raw.get("type", "")),
            id=int(raw.get("id", 0)),
            status=str(raw.get("status", "")),
            error=str(raw.get("error", "")),
        )


@dataclasses.dataclass(frozen=True)
class Pickup:
    """A handler's claim that it has taken a deliver off the wire.

    Sent the instant a deliver is read, before any user handler code runs, so
    the engine can tell picked-up messages (dropped on crash) from unread ones
    (requeued on crash).
    """

    id: int

    def to_dict(self) -> dict[str, Any]:
        """Encode the pickup note for the wire."""
        return {"type": TYPE_PICKUP, "id": self.id}


@dataclasses.dataclass(frozen=True)
class Emit:
    """A handler's request to publish a message on one of its output selectors.

    Deliver names the message (a deliver id) this emit was produced for, so the
    engine stamps that message's agent chain; for a start emit there is no
    message, so deliver is 0 and is omitted from the wire. Id is the emit's own
    id, echoed by the emit_ack.
    """

    id: int
    deliver: int
    selector: str
    data: collections.abc.Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Encode the emit note for the wire, omitting a zero deliver id."""
        body: dict[str, Any] = {
            "type": TYPE_EMIT,
            "id": self.id,
            "selector": self.selector,
            "data": self.data,
        }
        if self.deliver != START_ID:
            body["deliver"] = self.deliver
        return body


@dataclasses.dataclass(frozen=True)
class Done:
    """Ends the handler's work for an id.

    Ok if it processed cleanly, error (carrying a reason) if it failed. The
    error is omitted on the wire when ok.
    """

    id: int
    status: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        """Encode the done note for the wire, omitting the error when ok."""
        body: dict[str, Any] = {"type": TYPE_DONE, "id": self.id, "status": self.status}
        if self.status == STATUS_ERROR:
            body["error"] = self.error
        return body


def done_for(deliver_id: int, error: str | None) -> Done:
    """Build a deliver's terminal done.

    Args:
        deliver_id: The id of the message (or start phase) this done ends.
        error: The failure text, or None when the work completed cleanly.

    Returns:
        A done note: ok when error is None, error (carrying its text) otherwise.
    """
    if error is None:
        return Done(id=deliver_id, status=STATUS_OK, error="")
    return Done(id=deliver_id, status=STATUS_ERROR, error=error)
