"""Shared protocol definitions for the a816 assembler.

This module contains Protocol classes that define interfaces used across
multiple modules. Extracting these to a separate module eliminates
circular dependencies between cpu_65c816.py, symbols.py, and nodes.py.
"""

from typing import TYPE_CHECKING, Literal, Protocol

from a816.cpu.mapping import Address
from a816.cpu.types import ValueSize

if TYPE_CHECKING:  # pragma: nocover
    from a816.symbols import Resolver


class ValueNodeProtocol(Protocol):
    """Protocol for nodes that represent values (numbers, expressions)."""

    def get_value(self) -> int:
        """Returns the value of the node as an int."""

    def get_value_string_len(self) -> int:
        """Returns the length in bytes of the value."""

    def get_operand_size(self) -> Literal["b", "w", "l"]:
        """Returns the operand size as b w l.

        Default implementation based on value string length:
        - 1-2 hex digits: byte ('b')
        - 3-4 hex digits: word ('w')
        - 5+ hex digits: long ('l')
        """
        retval: Literal["b", "w", "l"]

        value_length = self.get_value_string_len()
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
        value_node: "ValueNodeProtocol | None",
        resolver: "Resolver",
        size: ValueSize | None = None,
    ) -> bytes:
        """Emit machine code bytes for this opcode."""

    def supposed_length(
        self,
        value_node: "ValueNodeProtocol | None",
        size: ValueSize | None = None,
    ) -> int:
        """Return the expected byte length of this opcode."""
