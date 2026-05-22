"""OpcodeNode: emit one assembled 65c816 instruction."""

from __future__ import annotations

from a816.cpu.cpu_65c816 import NoOpcodeForOperandSize, guess_value_size, snes_opcode_table
from a816.cpu.mapping import Address
from a816.cpu.types import AddressingMode, ValueSize
from a816.diagnostics.suggest import did_you_mean_hint as _did_you_mean_hint
from a816.error_codes import E_SYMBOL_NOT_DEFINED as _E_SYMBOL_NOT_DEFINED
from a816.exceptions import SymbolNotDefined
from a816.parse.nodes.errors import NodeError
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol, OpcodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver


class OpcodeNode(NodeProtocol):
    def __init__(
        self,
        opcode: str,
        *,
        size: ValueSize | None = None,
        addressing_mode: AddressingMode,
        index: str | None = None,
        value_node: ValueNodeProtocol | None = None,
        file_info: Token,
        resolver: Resolver,
    ) -> None:
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        self.size = size
        self.file_info = file_info
        self.resolver = resolver

    def _get_emitter(self) -> OpcodeProtocol:
        try:
            opcode_emitter = snes_opcode_table[self.opcode][self.addressing_mode]
        except KeyError as e:
            raise NodeError(
                f"Addressing mode ({self.addressing_mode.name}) for opcode_def ({self.opcode}) is not defined.",
                file_info=self.file_info,
            ) from e

        if isinstance(opcode_emitter, dict):
            if self.index is not None:
                opcode_emitter = opcode_emitter[self.index]
            else:
                raise NodeError(
                    f"Addressing mode ({self.addressing_mode.name}) for opcode_def ({self.opcode}) needs an index.",
                    file_info=self.file_info,
                )
        return opcode_emitter

    def emit(self, current_pc: Address) -> bytes:
        opcode_emitter = self._get_emitter()
        try:
            return opcode_emitter.emit(self.value_node, self.resolver, self.size)
        except NoOpcodeForOperandSize as e:
            assert self.value_node is not None
            guessed_size = guess_value_size(self.value_node, self.size)
            raise NodeError(
                f"{self.opcode} does not supports size ({guessed_size}).",
                self.file_info,
            ) from e
        except SymbolNotDefined as e:
            raise NodeError(
                f"`{e}` is not defined in the current scope",
                self.file_info,
                code=str(_E_SYMBOL_NOT_DEFINED),
                hint=_did_you_mean_hint(str(e), self.resolver.current_scope),
            ) from e

    def pc_after(self, current_pc: Address) -> Address:
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size, self.resolver)

    def __str__(self) -> str:
        return f"OpcodeNode({self.opcode}, {self.addressing_mode}, {self.index}, {self.value_node})"
