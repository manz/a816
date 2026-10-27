"""Shared protocol definitions for the a816 assembler.

This module contains Protocol classes that define interfaces used across
multiple modules. Extracting these to a separate module eliminates
circular dependencies between cpu_65c816.py, symbols.py, and nodes.py.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from a816.cpu.mapping import Address
from a816.cpu.types import ValueSize
from a816.link_expr import RelocKind

if TYPE_CHECKING:  # pragma: nocover
    from a816.symbols import Resolver


@dataclass
class StandIn:
    """While an opcode sizes itself on a label pass, a label not bound yet
    reads as `value` (the opcode's own address): it lies at or after the PC
    in the same bank, so it needs the same operand size."""

    value: int
    used: bool = False
    """Set once a lookup read the stand-in: the size picked is provisional."""


@dataclass(frozen=True)
class OperandContext:
    """Where the opcode whose operand is evaluated sits.

    Passed to the emitter, never stored on the resolver: what an operand
    means depends on its opcode, and only for that call.
    """

    pc: int | None = None
    """The opcode's own address: a bare `jsr` / `jmp` picks its form from
    the target's bank against it, and a branch after `@=` counts from it."""
    byte_check: RelocKind = RelocKind.VALUE
    """The range check a one-byte operand carries to the linker
    (`RelocKind.BYTE_IMMEDIATE`, `DIRECT_PAGE`, ...)."""
    stand_in: StandIn | None = None
    """What an unbound label reads as while the opcode sizes itself."""


NO_OPERAND_CONTEXT = OperandContext()
"""An operand looked at outside an opcode's emit or sizing."""


class ValueNodeProtocol(Protocol):
    """Protocol for nodes that represent values (numbers, expressions)."""

    def get_value(self, operand: OperandContext = NO_OPERAND_CONTEXT) -> int:
        """Returns the value of the node as an int."""

    def get_value_string_len(self, operand: OperandContext = NO_OPERAND_CONTEXT) -> int:
        """Returns the length in bytes of the value."""

    def get_operand_size(self, operand: OperandContext = NO_OPERAND_CONTEXT) -> Literal["b", "w", "l"]:
        """Returns the operand size as b w l.

        Default implementation based on value string length:
        - 1-2 hex digits: byte ('b')
        - 3-4 hex digits: word ('w')
        - 5+ hex digits: long ('l')
        """
        retval: Literal["b", "w", "l"]

        value_length = self.get_value_string_len(operand)
        if value_length <= 2:
            retval = "b"
        elif value_length <= 4:
            retval = "w"
        else:
            retval = "l"

        return retval


class NodeProtocol(Protocol):
    """Protocol for executable nodes in the assembly output."""

    def emit(self, current_addr: Address) -> bytes:
        """Emits the node as bytes."""

    def pc_after(self, current_pc: Address) -> Address:
        """Returns the program counter address after the node was emitted."""


class OpcodeProtocol(Protocol):
    """Protocol for opcode emitters that generate machine code."""

    def emit(
        self,
        value_node: ValueNodeProtocol | None,
        resolver: Resolver,
        size: ValueSize | None = None,
        operand: OperandContext = NO_OPERAND_CONTEXT,
    ) -> bytes:
        """Emit machine code bytes for this opcode."""

    def supposed_length(
        self,
        value_node: ValueNodeProtocol | None,
        size: ValueSize | None = None,
        resolver: Resolver | None = None,
        operand: OperandContext = NO_OPERAND_CONTEXT,
    ) -> int:
        """Return the expected byte length of this opcode."""


class NodeBase:
    """Nominal base for executable nodes.

    Implementations satisfy `NodeProtocol` structurally; subclassing the
    Protocol itself would give them `_ProtocolMeta`, which routes every
    `isinstance` on a node through a Python-level check on the hot path.
    """

    def emit(self, current_addr: Address) -> bytes:
        raise NotImplementedError

    def pc_after(self, current_pc: Address) -> Address:
        raise NotImplementedError


class OpcodeBase:
    """Nominal base for opcode emitters; see `NodeBase` for why not `OpcodeProtocol`."""

    def emit(
        self,
        value_node: ValueNodeProtocol | None,
        resolver: Resolver,
        size: ValueSize | None = None,
        operand: OperandContext = NO_OPERAND_CONTEXT,
    ) -> bytes:
        raise NotImplementedError

    def supposed_length(
        self,
        value_node: ValueNodeProtocol | None,
        size: ValueSize | None = None,
        resolver: Resolver | None = None,
        operand: OperandContext = NO_OPERAND_CONTEXT,
    ) -> int:
        raise NotImplementedError
