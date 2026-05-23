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
        self._maybe_update_register_sizes()
        opcode_emitter = self._get_emitter()
        return current_pc + opcode_emitter.supposed_length(self.value_node, self.size, self.resolver)

    def _maybe_update_register_sizes(self) -> None:
        """`rep`/`sep` change M/X at runtime; the assembler-time analog
        is `.a8` / `.a16` / `.i8` / `.i16`. Without bridging the two,
        source has to repeat itself after every `rep` / `sep`:

            rep #0x30
            .a16        ; redundant — assembler should infer this
            .i16

        Bridge: when we see `rep`/`sep` with an immediate operand
        whose value resolves to a constant, mutate
        `resolver.a_size` / `i_size` the same way the CPU would.
        Subsequent opcode-width inference picks the right form
        without the explicit directive. Explicit `.a*` / `.i*`
        still wins because it runs through `RegisterSizeNode` which
        sets the size directly; running after a `rep`/`sep` just
        re-asserts what the inference already chose.
        """
        if self.opcode not in ("rep", "sep") or self.addressing_mode is not AddressingMode.immediate:
            return
        if self.value_node is None:
            return
        try:
            value = self.value_node.get_value()
        except Exception:  # noqa: BLE001 — expression may reference an unresolved forward symbol
            return
        if not isinstance(value, int):
            return
        # `rep #N` clears the named flag bits → 16-bit register.
        # `sep #N` sets them → 8-bit register.
        new_size = 16 if self.opcode == "rep" else 8
        if value & 0x20:
            self.resolver.a_size = new_size
        if value & 0x10:
            self.resolver.i_size = new_size

    def __str__(self) -> str:
        return f"OpcodeNode({self.opcode}, {self.addressing_mode}, {self.index}, {self.value_node})"
