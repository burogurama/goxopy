"""Tests that drive the SDK the way the goxo engine does.

Each test writes init plus a phase into the SDK's stdin and reads back the
pickup, emit, and done frames from its stdout, then asserts ordering, concurrent
dispatch, the emit/emit_ack round-trip, and the error and missing-handler cases.
"""

from __future__ import annotations

import collections.abc
import threading
from typing import BinaryIO
from typing import Callable

from oxo import agent
from oxo import context
from oxo._note import note
from oxo._note import wire
from tests.oxo import conftest

EngineFactory = Callable[[agent.Agent, collections.abc.Sequence[str]], conftest.FakeEngine]
WritePhase = Callable[[BinaryIO], None]


def _write_deliver(deliver_id: int, selector: str, data: collections.abc.Mapping[str, object]) -> WritePhase:
    def write_phase(w: BinaryIO) -> None:
        wire.write_frame(w, conftest.deliver_note(deliver_id, selector, data))

    return write_phase


def _write_start() -> WritePhase:
    def write_phase(w: BinaryIO) -> None:
        wire.write_frame(w, {"type": note.TYPE_START})

    return write_phase


def testRun_whenDeliverHandled_repliesDoneOk(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_message("v3.asset.ip", lambda ctx, msg: None)

    result = make_engine(target, []).run(_write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1)

    assert result.run_error is None
    assert result.dones[1]["status"] == note.STATUS_OK


def testRun_whenDeliverHandled_handlerSeesPayloadAndIdentity(
    make_engine: EngineFactory,
) -> None:
    seen: dict[str, object] = {}
    target = agent.Agent()

    @target.on_message("v3.asset.ip")
    def handle(ctx: context.Context, msg: context.Message) -> None:
        seen["host"] = msg.data.get("host")
        seen["message_id"] = msg.meta.message_id
        seen["agent"] = ctx.identity.agent
        seen["universe"] = ctx.identity.universe

    make_engine(target, []).run(_write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1)

    assert seen["host"] == "10.0.0.1"
    assert seen["message_id"] == "m-1"
    assert seen["agent"] == "agent/test"
    assert seen["universe"] == "u-1"


def testRun_whenHandlerEmits_publishesOnSelector(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_message("v3.asset.ip", lambda ctx, msg: ctx.emit("v3.report.vuln", {"title": "x"}))

    result = make_engine(target, ["v3.report.vuln"]).run(
        _write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1
    )

    assert result.dones[1]["status"] == note.STATUS_OK
    assert len(result.published) == 1
    assert result.published[0].selector == "v3.report.vuln"


def testRun_whenPickupSent_precedesEmitAndDone(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_message("v3.asset.ip", lambda ctx, msg: ctx.emit("v3.report.vuln", {"title": "x"}))

    result = make_engine(target, ["v3.report.vuln"]).run(
        _write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1
    )

    assert result.pickups == [1]
    pickup_index, done_index = result.order_of(1)
    emit_index = next(i for i, n in enumerate(result.notes) if n["type"] == note.TYPE_EMIT)
    assert pickup_index is not None
    assert done_index is not None
    assert pickup_index < emit_index
    assert pickup_index < done_index


def testRun_whenHandlerRaises_repliesDoneError(
    make_engine: EngineFactory,
) -> None:
    def raise_boom(ctx: context.Context, msg: context.Message) -> None:
        raise RuntimeError("boom")

    target = agent.Agent()
    target.on_message("v3.asset.ip", raise_boom)

    result = make_engine(target, []).run(_write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1)

    assert result.dones[1]["status"] == note.STATUS_ERROR
    assert "boom" in result.dones[1]["error"]


def testRun_whenSelectorUnregistered_repliesDoneError(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_message("v3.asset.ip", lambda ctx, msg: None)

    result = make_engine(target, []).run(_write_deliver(1, "v3.unknown", {}), expected_dones=1)

    assert result.dones[1]["status"] == note.STATUS_ERROR
    assert "v3.unknown" in result.dones[1]["error"]


def testRun_whenEmitRejectedAndIgnored_repliesDoneOk(
    make_engine: EngineFactory,
) -> None:
    emit_errors: list[Exception] = []

    def handle(ctx: context.Context, msg: context.Message) -> None:
        try:
            ctx.emit("v3.not.declared", {"x": 1})
        except Exception as e:  # noqa: BLE001 — the test asserts the rejection surfaced.
            emit_errors.append(e)

    target = agent.Agent()
    target.on_message("v3.asset.ip", handle)

    result = make_engine(target, ["v3.report.vuln"]).run(
        _write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1
    )

    assert len(emit_errors) == 1
    assert result.dones[1]["status"] == note.STATUS_OK
    assert len(result.published) == 0


def testRun_whenSecondEmit_incrementsEmitID(
    make_engine: EngineFactory,
) -> None:
    def handle(ctx: context.Context, msg: context.Message) -> None:
        ctx.emit("v3.report.vuln", {"n": 1})
        ctx.emit("v3.report.vuln", {"n": 2})

    target = agent.Agent()
    target.on_message("v3.asset.ip", handle)

    result = make_engine(target, ["v3.report.vuln"]).run(
        _write_deliver(1, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1
    )

    emit_ids = [n["id"] for n in result.notes if n["type"] == note.TYPE_EMIT]
    assert emit_ids == [1, 2]


def testRun_whenHandlerEmits_tagsDeliverID(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_message("v3.asset.ip", lambda ctx, msg: ctx.emit("v3.report.vuln", {"x": 1}))

    result = make_engine(target, ["v3.report.vuln"]).run(
        _write_deliver(7, "v3.asset.ip", {"host": "10.0.0.1"}), expected_dones=1
    )

    assert len(result.published) == 1
    assert result.published[0].deliver == 7


def testRun_whenTwoDelivers_dispatchesConcurrently(
    make_engine: EngineFactory,
) -> None:
    arrived = threading.Barrier(2, timeout=2.0)

    def handle(ctx: context.Context, msg: context.Message) -> None:
        arrived.wait()
        ctx.emit("v3.report.vuln", msg.data)

    target = agent.Agent()
    target.on_message("v3.asset.ip", handle)

    def write_phase(w: BinaryIO) -> None:
        wire.write_frame(w, conftest.deliver_note(1, "v3.asset.ip", {"n": 1}))
        wire.write_frame(w, conftest.deliver_note(2, "v3.asset.ip", {"n": 2}))

    result = make_engine(target, ["v3.report.vuln"]).run(write_phase, expected_dones=2)

    assert len(result.dones) == 2
    assert result.dones[1]["status"] == note.STATUS_OK
    assert result.dones[2]["status"] == note.STATUS_OK
    assert sorted(result.pickups) == [1, 2]
    assert len(result.published) == 2


def testRun_whenStartHandled_repliesDoneOk(
    make_engine: EngineFactory,
) -> None:
    ran: list[bool] = []
    target = agent.Agent()
    target.on_start(lambda ctx: ran.append(True))

    result = make_engine(target, []).run(_write_start(), expected_dones=1)

    assert ran == [True]
    assert result.dones[note.START_ID]["status"] == note.STATUS_OK


def testRun_whenStartEmits_carriesNoDeliverID(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()
    target.on_start(lambda ctx: ctx.emit("v3.report.vuln", {"title": "started"}))

    result = make_engine(target, ["v3.report.vuln"]).run(_write_start(), expected_dones=1)

    assert result.dones[note.START_ID]["status"] == note.STATUS_OK
    assert len(result.published) == 1
    emit = next(n for n in result.notes if n["type"] == note.TYPE_EMIT)
    assert "deliver" not in emit


def testRun_whenStartHookRaises_repliesDoneError(
    make_engine: EngineFactory,
) -> None:
    def raise_boom(ctx: context.Context) -> None:
        raise RuntimeError("start boom")

    target = agent.Agent()
    target.on_start(raise_boom)

    result = make_engine(target, []).run(_write_start(), expected_dones=1)

    assert result.dones[note.START_ID]["status"] == note.STATUS_ERROR
    assert "start boom" in result.dones[note.START_ID]["error"]


def testRun_whenNoStartHook_stillRepliesDoneOk(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()

    result = make_engine(target, []).run(_write_start(), expected_dones=1)

    assert result.dones[note.START_ID]["status"] == note.STATUS_OK


def testRun_whenWrongProtocolVersion_raisesProtocolError(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()

    result = make_engine(target, []).run(_write_start(), expected_dones=0, protocol=note.PROTOCOL_VERSION + 1)

    assert isinstance(result.run_error, agent.ProtocolError)


def testRun_whenStdinEmpty_raisesProtocolError(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()

    result = make_engine(target, []).run(lambda w: None, expected_dones=0, send_init=False)

    assert isinstance(result.run_error, agent.ProtocolError)


def testRun_whenInitThenEOF_exitsCleanly(
    make_engine: EngineFactory,
) -> None:
    target = agent.Agent()

    result = make_engine(target, []).run(lambda w: None, expected_dones=0)

    assert result.run_error is None
