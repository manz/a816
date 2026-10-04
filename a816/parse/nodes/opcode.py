"""OpcodeNode: emit one assembled 65c816 instruction."""

from __future__ import annotations

import logging
from typing import cast

from a816.cpu.cpu_65c816 import BlockMoveOpcode, NoOpcodeForOperandSize, Opcode, guess_value_size, snes_opcode_table
from a816.cpu.mapping import Address
from a816.cpu.types import AddressingMode, ValueSize
from a816.diagnostics.suggest import did_you_mean_hint as _did_you_mean_hint
from a816.error_codes import E_CODEGEN_BRANCH_RANGE, E_CODEGEN_BRANCH_UNMAPPED
from a816.error_codes import E_CODEGEN_IMMEDIATE_OVERFLOW as _E_IMMEDIATE_OVERFLOW
from a816.error_codes import E_SYMBOL_NOT_DEFINED as _E_SYMBOL_NOT_DEFINED
from a816.exceptions import BranchOutOfRangeError, BranchTargetUnmappedError, SymbolNotDefined
from a816.parse.nodes.errors import NodeError, format_node_warning
from a816.parse.nodes.expr import ExpressionNode
from a816.parse.tokens import Token
from a816.protocols import NodeBase, OpcodeProtocol, ValueNodeProtocol
from a816.symbols import Resolver

logger = logging.getLogger("a816")

# Opcodes after which the assembler can no longer know M/X: `plp` pulls the
# flags from the stack; the rest end straight-line flow, so the next
# instruction is only reachable through a label with its own entry state.
_FORGETS_REGISTER_SIZES = frozenset({"plp", "bra", "brl", "jmp", "jml", "rts", "rtl", "rti"})


class OpcodeNode(NodeBase):
    def __init__(
        self,
        opcode: str,
        *,
        size: ValueSize | None = None,
        addressing_mode: AddressingMode,
        index: str | None = None,
        value_node: ValueNodeProtocol | None = None,
        value_node2: ValueNodeProtocol | None = None,
        file_info: Token,
        resolver: Resolver,
    ) -> None:
        self.opcode = opcode.lower()
        self.addressing_mode = addressing_mode
        self.index = index
        self.value_node = value_node
        # Second operand, block move only (`mvn src, dst`).
        self.value_node2 = value_node2
        self.size = size
        self.file_info = file_info
        self.resolver = resolver
        # Emit may run more than once for the same node (debug capture,
        # LSP rebuilds); warn about a width mismatch only the first time.
        self._width_warned = False

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
        # Emission is its own walk, reset to power-on sizes: replay the
        # tracked `rep`/`sep` mutation `pc_after` did on the label passes
        # so the next opcode is sized the same way here.
        self._maybe_update_register_sizes()
        self._update_known_register_sizes()
        opcode_emitter = self._get_emitter()
        if self.addressing_mode is AddressingMode.block_move:
            assert self.value_node is not None and self.value_node2 is not None
            return cast(BlockMoveOpcode, opcode_emitter).emit_block_move(
                self.value_node, self.value_node2, self.resolver
            )
        try:
            emitted = opcode_emitter.emit(self.value_node, self.resolver, self.size)
        except NoOpcodeForOperandSize as size_error:
            assert self.value_node is not None
            guessed_size = guess_value_size(self.value_node, self.size)
            raise NodeError(
                f"{self.opcode} does not supports size ({guessed_size}).",
                self.file_info,
            ) from size_error
        except SymbolNotDefined as undefined:
            raise NodeError(
                f"`{undefined}` is not defined in the current scope",
                undefined.token or self.file_info,
                code=str(_E_SYMBOL_NOT_DEFINED),
                hint=_did_you_mean_hint(str(undefined), self.resolver.current_scope),
            ) from undefined
        except BranchOutOfRangeError as out_of_range:
            raise NodeError(
                str(out_of_range),
                self._operand_token(),
                code=str(E_CODEGEN_BRANCH_RANGE),
                hint="use `brl` (16-bit offset) or `jmp` to reach a distant target",
            ) from out_of_range
        except BranchTargetUnmappedError as unmapped:
            raise NodeError(str(unmapped), self._operand_token(), code=str(E_CODEGEN_BRANCH_UNMAPPED)) from unmapped
        self._check_byte_immediate_overflow(opcode_emitter)
        self._warn_on_immediate_width_mismatch(opcode_emitter)
        return emitted

    def _operand_token(self) -> Token:
        """First token of the operand expression, falling back to the opcode."""
        if isinstance(self.value_node, ExpressionNode) and self.value_node.expression.tokens:
            return self.value_node.expression.tokens[0].token
        return self.file_info

    def _check_byte_immediate_overflow(self, emitter: OpcodeProtocol) -> None:
        """Reject a `.b` immediate whose value does not fit one byte.

        Only the byte width errors: `.w`/`.l` immediates keep masking
        (`lda.w #symbol` loads the low word of an address on purpose).
        A byte accepts -0x100..0xFF, i.e. the bits above bit 7 are all
        zero or all one, so `#-1` and `#~0x80` stay valid. External
        symbols resolve to 0 here and are left to the linker.
        """
        if self.addressing_mode is not AddressingMode.immediate or not isinstance(emitter, Opcode):
            return
        assert self.value_node is not None
        if guess_value_size(self.value_node, self.size, self.resolver, emitter.is_a, emitter.is_x) != "b":
            return
        value = self.value_node.get_value()
        if not isinstance(value, int) or value >> 8 in (0, -1):
            return
        raise NodeError(
            f"immediate {value:#x} does not fit in a byte (`{self.opcode}.b` takes -0x100..0xFF)",
            self.file_info,
            code=str(_E_IMMEDIATE_OVERFLOW),
            hint=f"use `{self.opcode}.w` if the register is 16-bit, or mask the value explicitly (`& 0xFF`)",
        )

    def _known_register_width(self, emitter: Opcode) -> tuple[str, int] | None:
        """`(register, bits)` the immediate is read at, if the source asserted it."""
        if emitter.is_a and self.resolver.a_size_known:
            return "A", self.resolver.a_size
        if emitter.is_x and self.resolver.i_size_known:
            return "X", self.resolver.i_size
        return None

    def _warn_on_immediate_width_mismatch(self, emitter: OpcodeProtocol) -> None:
        """Warn when an M/X-sized immediate disagrees with the known register size.

        The CPU reads the operand at the width its M/X flag says, so a
        mismatch desyncs every byte after it. Emission is unchanged (an
        explicit suffix wins, the value drives width otherwise); unknown
        register state never warns.
        """
        immediate = self.addressing_mode is AddressingMode.immediate
        if self._width_warned or not immediate or not isinstance(emitter, Opcode):
            return
        known = self._known_register_width(emitter)
        if known is None:
            return
        register, bits = known
        assert self.value_node is not None
        emitted = guess_value_size(self.value_node, self.size, self.resolver, emitter.is_a, emitter.is_x)
        expected = "b" if bits == 8 else "w"
        if emitted == expected:
            return
        self._width_warned = True
        message = (
            f"immediate width mismatch: `{self.opcode}` emits a .{emitted} operand but {register} is {bits}-bit here"
        )
        hint = f"write `{self.opcode}.{expected}`, or re-assert the register size before this line"
        logger.warning(format_node_warning(message, self.file_info, hint=hint))

    def _update_known_register_sizes(self) -> None:
        """Keep `a_size_known` / `i_size_known` honest across flag-changing opcodes.

        `plp` restores M/X from the stack, and code after an unconditional
        transfer is only reached via a label from elsewhere, so both become
        unknown. An
        untracked `rep`/`sep` changes the touched register at runtime
        while the assembler keeps its size, so that register becomes
        unknown; a tracked one makes it known (sizes set by
        `_maybe_update_register_sizes`).
        """
        if self.opcode in _FORGETS_REGISTER_SIZES:
            self.resolver.forget_register_sizes()
            return
        flags = self._rep_sep_flags()
        if flags is None:
            return
        known = self.resolver.track_register_size
        if flags & 0x20:
            self.resolver.a_size_known = known
        if flags & 0x10:
            self.resolver.i_size_known = known

    def _rep_sep_flags(self) -> int | None:
        """Constant immediate of a `rep`/`sep`, else None."""
        if self.opcode not in ("rep", "sep") or self.addressing_mode is not AddressingMode.immediate:
            return None
        assert self.value_node is not None
        try:
            value = self.value_node.get_value()
        except SymbolNotDefined:
            return None
        return value if isinstance(value, int) else None

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
        if not self.resolver.track_register_size:
            return
        # An operand pass 1 cannot evaluate (forward label) raises E0200
        # here on purpose: labels bind on pass 1 only, so skipping the
        # `rep`/`sep` would size the code after it at the old width.
        value = self._rep_sep_flags()
        if value is None:
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
