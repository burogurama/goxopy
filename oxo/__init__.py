"""The Python SDK for writing OXO agents hosted by the goxo engine.

An author registers a handler per selector, then calls run. Messages arrive as
plain dicts and the package handles the wire; the engine owns the protobuf
codec, so a handler never sees protobuf.

For the engine and the note protocol, see https://github.com/burogurama/goxo.
"""

from oxo import agent
from oxo import connection
from oxo import context

Agent = agent.Agent
Context = context.Context
Message = context.Message
Identity = context.Identity
Meta = context.Meta

Error = agent.Error
ProtocolError = agent.ProtocolError
EmitRejectedError = connection.EmitRejectedError
EngineClosedError = connection.EngineClosedError

__all__ = [
    "Agent",
    "Context",
    "Message",
    "Identity",
    "Meta",
    "Error",
    "ProtocolError",
    "EmitRejectedError",
    "EngineClosedError",
]
