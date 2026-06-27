"""The goxo engine handler IPC: small JSON "notes" framed over stdin/stdout.

This is the handler side of the protocol the goxo engine speaks. A handler
process is long-lived and may have several messages in flight at once, so notes
carry ids that disambiguate which message they concern. Each deliver carries a
message id; the matching done echoes it. An emit may name the message it was
produced for (its deliver id) and carries its own id that the emit_ack echoes.
"""

from oxo._note import note
from oxo._note import wire

__all__ = ["note", "wire"]
