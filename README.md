# goxopy

The Python SDK for writing [OXO](https://github.com/Ostorlab/oxo) agents.

goxopy is the thin Python client for the
[goxo](https://github.com/burogurama/goxo) engine: goxo does the OXO
heavy-lifting and drives your agent as a handler process, and goxopy lets
you write that handler in plain Python. You register a function per selector, an
optional start hook, and call `run`; messages arrive as plain dicts and you
never touch the wire, protobuf, or any OXO detail.

For the engine, the note protocol, and how to package a handler into an agent
image, see the goxo repo's README. This README is just the Python API.

Zero runtime dependencies — standard library only.

## Quick start

```python
import oxo

agent = oxo.Agent()


@agent.on_start
def start(ctx: oxo.Context) -> None:
    ctx.log.info("starting: agent=%s universe=%s", ctx.identity.agent, ctx.identity.universe)


@agent.on_message("v3.asset.ip")
def handle(ctx: oxo.Context, msg: oxo.Message) -> None:
    host = msg.data.get("host", "")
    ctx.emit(
        "v3.report.vuln",
        {
            "title": "host reachable",
            "risk_rating": "INFO",
            "technical_detail": f"host {host} responded",
        },
    )


if __name__ == "__main__":
    agent.run()
```

See [`examples/reporter`](examples/reporter) for the runnable version.

## API

```python
agent = oxo.Agent()
agent.on_message(selector, fn)   # one handler per input selector; also usable as @agent.on_message(selector)
agent.on_start(fn)               # optional, runs once at boot; also usable as @agent.on_start
agent.run()                      # serve the process until the engine closes stdin, then return
```

`on_message` and `on_start` may be used as decorators or called directly; called
directly they return the agent for chaining. A handler that raises fails its
phase — the engine nacks a failed message and treats a failed start hook as a
start failure. `run` itself raises only if the IPC breaks (a malformed note, or
stdout could not be written); a handler error is reported to the engine, not
raised from `run`.

Inside a handler, the **`Context`** is your window onto the run:

- `ctx.identity` — the agent instance: `agent`, `key`, `universe`.
- `ctx.config` — the agent config as a dict (`None` if none).
- `ctx.log` — a `logging.Logger` writing to **stderr** (stdout is reserved for the protocol).
- `ctx.emit(selector, data)` — publish a dict on a declared output selector,
  synchronously; it raises if the engine rejected the emit (an undeclared
  selector, or a publish failure) or closed the connection before acknowledging it.

A **`Message`** carries `selector`, `data` (the proto fields as a plain dict),
and `meta` (`message_id`, `headers`).

## Packaging

A handler becomes an OXO agent when packaged into a Docker image alongside the
goxo engine. Your handler imports `oxo` only, never goxo, so the engine is
chosen at packaging time. See goxo's README for the image contract and the
build steps.

## Development

```bash
pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy .
pytest
```

Requires Python 3.14+.
