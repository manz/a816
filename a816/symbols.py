import logging
from collections.abc import ItemsView
from typing import Any

from a816.context import AssemblyContext
from a816.cpu.mapping import Address, Bus
from a816.cpu.types import RomType
from a816.exceptions import ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.nodes import BlockAstNode
from script import Table

logger = logging.getLogger("a816")


class Scope:
    """A symbol scope for managing labels, symbols, and tables.

    Scopes form a hierarchy for symbol resolution, allowing nested namespaces
    (e.g., inside macros or named scopes). Each scope can contain:
    - Labels (code addresses)
    - Symbols (numeric or string constants)
    - Code symbols (macro-like block definitions)
    - External symbol declarations

    Symbol lookup traverses up the parent chain until found or root is reached.
    """

    def __init__(self, resolver: "Resolver", parent: "Scope | None" = None) -> None:
        """Initialize a new scope.

        Args:
            resolver: The parent Resolver managing this scope.
            parent: Optional parent scope for hierarchical lookup.
        """
        self.symbols: dict[str, int | str] = {}
        self.code_symbols: dict[str, BlockAstNode] = {}
        self.external_symbols: set[str] = set()
        self.parent = parent
        self.resolver: Resolver = resolver
        self.table: Table | None = None
        self.labels: dict[str, int] = {}

    def add_label(self, label: str, value: Address) -> None:
        self.labels[label] = value.logical_value
        self.add_symbol(label, value.logical_value)

    def get_labels(self) -> ItemsView[str, int]:
        return self.labels.items()

    def add_symbol(self, symbol: str, value: int | BlockAstNode | str) -> None:
        if isinstance(value, BlockAstNode):
            if symbol in self.code_symbols:
                logger.warning(f"Symbol already defined ({symbol})")
            self.code_symbols[symbol] = value
        else:
            if symbol in self.symbols:
                logger.warning(f"Symbol already defined ({symbol})")
            self.symbols[symbol] = value

    def add_external_symbol(self, symbol: str) -> None:
        """Mark a symbol as external (defined in another object file)"""
        self.external_symbols.add(symbol)

    def is_external_symbol(self, symbol: str) -> bool:
        """Check if a symbol is marked as external"""
        if symbol in self.external_symbols:
            return True
        if self.parent:
            return self.parent.is_external_symbol(symbol)
        return False

    def __getitem__(self, item: str) -> int | str | BlockAstNode:
        try:
            return self.code_symbols[item]
        except KeyError:
            pass

        try:
            return self.symbols[item]
        except KeyError:
            # Check if this is an external symbol
            if self.is_external_symbol(item):
                raise ExternalSymbolReference(item) from None
            else:
                raise SymbolNotDefined(item) from None

    def get_table(self) -> Table | None:
        if self.table is None:
            if self.parent:
                return self.parent.get_table()
            else:
                return None
        else:
            return self.table

    def value_for(self, symbol: str) -> int | str | BlockAstNode | None:
        if self.parent:
            if symbol in self.symbols or symbol in self.code_symbols:
                return self[symbol]
            else:
                return self.parent.value_for(symbol)
        else:
            try:
                return self[symbol]
            except ExternalSymbolReference as e:
                # Re-raise the exception so expression evaluator can detect it
                raise e


class InternalScope(Scope):
    pass


class NamedScope(Scope):
    def __init__(self, name: str, resolver: "Resolver", parent: Scope | None = None):
        super().__init__(resolver, parent)
        self.name = name


low_rom_bus = Bus("low_rom_default_mapping")

low_rom_bus.map("1", (0x00, 0x6F), (0x8000, 0xFFFF), mask=0x8000, mirror_bank_range=(0x80, 0xCF))
low_rom_bus.map("2", (0x7E, 0x7F), (0, 0xFFFF), mask=0x1_0000, writeable=True)

low_rom_bus.editable = False

high_rom_bus = Bus("high_rom_default_mapping")

high_rom_bus.map("1", (0x40, 0x7F), (0, 0xFFFF), mask=0x1_0000, mirror_bank_range=(0xC0, 0xFF))
high_rom_bus.map("2", (0x7E, 0x7F), (0, 0xFFFF), mask=0x1_0000, writeable=True)

high_rom_bus.editable = False

BUS_MAPPING = {RomType.low_rom: low_rom_bus, RomType.high_rom: high_rom_bus}


class Resolver:
    """Symbol resolver managing scopes, addresses, and CPU state during assembly.

    The Resolver is the central state manager during assembly, tracking:
    - Current program counter (PC) and relocation address
    - Symbol scopes (hierarchical namespaces)
    - CPU register sizes (A and X/Y for 65c816)
    - ROM memory mapping type
    - Address bus configuration

    It provides symbol lookup, address calculation, and state management
    across multiple assembly passes.
    """

    def __init__(self, pc: int = 0x000000):
        """Initialize the resolver with default state.

        Args:
            pc: Initial program counter value (default: 0x000000).
        """
        self.reloc = False
        self.a_size: int = 8  # Accumulator size: 8 or 16 bits
        self.i_size: int = 8  # Index register size: 8 or 16 bits
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope: Scope = Scope(self)
        self.scopes = [self.current_scope]
        self.bus = Bus()
        self.pc = 0
        self.reloc_address: Address
        self.context = AssemblyContext()
        self.set_position(pc)

    def get_bus(self) -> Bus:
        if self.bus.has_mappings():
            bus = self.bus
        else:
            bus = BUS_MAPPING[self.rom_type]
        return bus

    def set_position(self, pc: int) -> None:
        addr = self.get_bus().get_address(pc)
        physical = addr.physical

        if physical is not None:
            self.pc = physical

        # Normalize through the bus mapping so reloc_address.logical_value is
        # always the mapped logical address (e.g. 0x8000 for LoROM bank 0).
        self.reloc_address = addr + 0
        self.reloc = False

    def append_named_scope(self, name: str) -> None:
        scope = NamedScope(name, self, self.current_scope)
        self.scopes.append(scope)

    def append_scope(self) -> None:
        scope = Scope(self, self.current_scope)
        self.scopes.append(scope)

    def append_internal_scope(self) -> None:
        scope = InternalScope(self, self.current_scope)
        self.scopes.append(scope)

    def use_next_scope(self) -> None:
        self.last_used_scope += 1
        self.current_scope = self.scopes[self.last_used_scope]

    def restore_scope(self, exports: bool = False) -> None:
        if exports and isinstance(self.current_scope, NamedScope):
            scope = self.current_scope
            if scope.parent is not None:
                scope.parent.symbols |= {f"{scope.name}.{k}": v for k, v in scope.symbols.items()}
        if self.current_scope.parent is not None:
            self.current_scope = self.current_scope.parent
        else:
            raise RuntimeError("Current scope has no parent...")

    def _dump_symbols(self, symbols: dict[str, Any]) -> None:
        keys = sorted(symbols.keys())
        for key in keys:
            value = symbols[key]
            if isinstance(value, dict):
                print("namedscope", key)
                self._dump_symbols(value)
            else:
                if isinstance(value, tuple):
                    print(f"{key.ljust(32)} {value}")
                elif isinstance(value, int):
                    print(f"{key.ljust(32)} 0x{value:02x}")
                else:
                    print(f"{key.ljust(32)} {value}")

    def dump_symbol_map(self) -> None:
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                print("Scope\n")
                self._dump_symbols(scope.symbols)

    def get_all_labels(self) -> list[tuple[str, int]]:
        labels: list[tuple[str, int]] = []
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                labels += scope.get_labels()

        return labels

    def get_all_symbols(self) -> list[tuple[str, int]]:
        """Get all symbols including labels and assignments for object file export"""
        symbols: list[tuple[str, int]] = []
        seen_symbols: set[str] = set()

        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                # Add labels first (since labels are also in the symbols dict)
                for name, value in scope.get_labels():
                    if name not in seen_symbols:
                        symbols.append((name, value))
                        seen_symbols.add(name)

                # Add regular symbols (assignments) that aren't already labels
                for name, symbol_value in scope.symbols.items():
                    if isinstance(symbol_value, int) and name not in seen_symbols:  # Only export numeric symbols
                        symbols.append((name, symbol_value))
                        seen_symbols.add(name)
        return symbols
