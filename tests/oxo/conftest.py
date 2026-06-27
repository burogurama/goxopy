"""Shared fixtures: a fake engine that drives the SDK over real byte pipes."""

from __future__ import annotations

import collections.abc
import dataclasses
import io
import os
import threading
from typing import Any
from typing import BinaryIO
from typing import Callable

import pytest

from oxo import agent
from oxo._note import note
from oxo._note import wire


@dataclasses.dataclass
class Published:
    """One emit the fake engine observed and acked ok."""

    selector: str
    data: collections.abc.Mapping[str, Any]
    deliver: int


@dataclasses.dataclass
class Result:
    """What the fake engine observed for one run."""

    notes: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    published: list[Published] = dataclasses.field(default_factory=list)
    dones: dict[int, dict[str, Any]] = dataclasses.field(default_factory=dict)
    pickups: list[int] = dataclasses.field(default_factory=list)
    run_error: BaseException | None = None

    def order_of(self, deliver_id: int) -> tuple[int | None, int | None]:
        """The note indices of the pickup and done for one deliver id."""
        pickup_index: int | None = None
        done_index: int | None = None
        for index, raw in enumerate(self.notes):
            if raw["type"] == note.TYPE_PICKUP and raw["id"] == deliver_id:
                pickup_index = index
            if raw["type"] == note.TYPE_DONE and raw["id"] == deliver_id:
                done_index = index
        return pickup_index, done_index


class FakeEngine:
    """Plays the engine side of a run against an Agent over OS pipes.

    It writes init then a caller-supplied phase, answers each emit with an ack
    (ok when the selector is in declared outputs, error otherwise), records
    pickups and dones, and stops once it has collected the expected dones.
    """

    def __init__(self, target_agent: agent.Agent, outputs: collections.abc.Sequence[str]) -> None:
        self._agent = target_agent
        self._outputs = set(outputs)
        e2h_read, e2h_write = os.pipe()
        h2e_read, h2e_write = os.pipe()
        self._engine_write: BinaryIO = io.FileIO(e2h_write, mode="w")
        self._engine_read: BinaryIO = io.FileIO(h2e_read, mode="r")
        self._handler_read: BinaryIO = io.FileIO(e2h_read, mode="r")
        self._handler_write: BinaryIO = io.FileIO(h2e_write, mode="w")
        self._result = Result()
        self._runner = threading.Thread(target=self._run_handler)

    def _run_handler(self) -> None:
        try:
            self._agent._run(self._handler_read, self._handler_write)
        except BaseException as e:  # noqa: BLE001 — record any run error for the assertion.
            self._result.run_error = e
        finally:
            self._handler_write.close()

    def run(
        self,
        write_phase: Callable[[BinaryIO], None],
        expected_dones: int,
        protocol: int = note.PROTOCOL_VERSION,
        send_init: bool = True,
    ) -> Result:
        """Drive one run and return what the engine observed.

        Args:
            write_phase: Writes the phase notes (delivers or a start) onto the
                engine's stream after init.
            expected_dones: How many dones to collect before closing stdin.
            protocol: The protocol version the init declares.
            send_init: Whether to send the init note at all; False leaves the
                handler reading EOF on init.

        Returns:
            The recorded notes, publishes, and dones.
        """
        self._runner.start()
        if send_init is True:
            wire.write_frame(
                self._engine_write,
                {
                    "type": note.TYPE_INIT,
                    "protocol": protocol,
                    "identity": {"agent": "agent/test", "key": "test", "universe": "u-1"},
                },
            )
        write_phase(self._engine_write)
        self._collect(expected_dones)
        self._engine_write.close()
        self._runner.join()
        self._engine_read.close()
        return self._result

    def _collect(self, expected_dones: int) -> None:
        while len(self._result.dones) < expected_dones:
            try:
                raw: dict[str, Any] = wire.read_frame(self._engine_read)
            except (EOFError, wire.Error):
                return
            self._record(raw)

    def _record(self, raw: collections.abc.Mapping[str, Any]) -> None:
        self._result.notes.append(dict(raw))
        note_type: str = raw["type"]
        if note_type == note.TYPE_PICKUP:
            self._result.pickups.append(raw["id"])
        elif note_type == note.TYPE_EMIT:
            self._answer_emit(raw)
        elif note_type == note.TYPE_DONE:
            self._result.dones[raw["id"]] = dict(raw)

    def _answer_emit(self, raw: collections.abc.Mapping[str, Any]) -> None:
        ack: dict[str, Any] = {"type": note.TYPE_EMIT_ACK, "id": raw["id"], "status": note.STATUS_OK}
        if raw["selector"] in self._outputs:
            self._result.published.append(
                Published(selector=raw["selector"], data=raw["data"], deliver=raw.get("deliver", note.START_ID))
            )
        else:
            ack["status"] = note.STATUS_ERROR
            ack["error"] = "selector not in declared outputs"
        wire.write_frame(self._engine_write, ack)


@pytest.fixture
def make_engine() -> Callable[[agent.Agent, collections.abc.Sequence[str]], FakeEngine]:
    """A factory that builds a FakeEngine for an agent and its declared outputs."""

    def factory(target_agent: agent.Agent, outputs: collections.abc.Sequence[str]) -> FakeEngine:
        return FakeEngine(target_agent, outputs)

    return factory


def deliver_note(deliver_id: int, selector: str, data: collections.abc.Mapping[str, Any]) -> dict[str, Any]:
    """Build a deliver note body the engine would put on the wire."""
    return {
        "type": note.TYPE_DELIVER,
        "id": deliver_id,
        "selector": selector,
        "data": dict(data),
        "meta": {"message_id": f"m-{deliver_id}"},
    }
