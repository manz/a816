import logging
from typing import Any, Dict, ItemsView, List, Optional, Tuple, Union

from a816.cpu.cpu_65c816 import RomType
from a816.cpu.mapping import Address, Bus
from a816.exceptions import SymbolNotDefined
from a816.parse.ast.nodes import BlockAstNode
from script import Table

logger = logging.getLogger("a816")


class Scope:
    def __init__(self, resolver: "Resolver", parent: Optional["Scope"] = None) -> None:
        self.symbols: Dict[str, int] = {}
        self.code_symbols: Dict[str, BlockAstNode] = {}
        self.parent = parent
        self.resolver: Resolver = resolver
        self.table: Optional[Table] = None
        self.labels: Dict[str, int] = {}

    def add_label(self, label: str, value: Address) -> None:
        self.labels[label] = value.logical_value
        self.add_symbol(label, value.logical_value)

    def get_labels(self) -> ItemsView[str, int]:
        return self.labels.items()

    def add_symbol(self, symbol: str, value: Union[int, BlockAstNode]) -> None:
        if isinstance(value, BlockAstNode):
            if symbol in self.code_symbols:
                logger.warning("Symbol already defined (%s)" % symbol)
            self.code_symbols[symbol] = value
        else:
            if symbol in self.symbols:
                logger.warning("Symbol already defined (%s)" % symbol)
            self.symbols[symbol] = value

    def __getitem__(self, item: str) -> Union[int, BlockAstNode]:
        try:
            return self.code_symbols[item]
        except KeyError as e:
            pass

        try:
            return self.symbols[item]
        except KeyError as e:
            raise SymbolNotDefined(item) from e

    def get_table(self) -> Optional[Table]:
        if self.table is None:
            if self.parent:
                return self.parent.get_table()
            else:
                return None
        else:
            return self.table

    def value_for(self, symbol: str) -> Optional[Union[int, BlockAstNode]]:
        if self.parent:
            if symbol in self.symbols or symbol in self.code_symbols:
                return self[symbol]
            else:
                return self.parent.value_for(symbol)
        else:
            return self[symbol]


class InternalScope(Scope):
    pass


class NamedScope(Scope):
    def __init__(self, name: str, resolver: "Resolver", parent: Optional[Scope] = None):
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
    def __init__(self, pc: int = 0x000000):
        self.reloc = False
        self.a = False
        self.x = False
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope: Scope = Scope(self)
        self.scopes = [self.current_scope]
        self.bus = Bus()
        self.pc = 0
        self.reloc_address: Address
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

        self.reloc_address = addr
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

    def _dump_symbols(self, symbols: Dict[str, Any]) -> None:
        keys = sorted(symbols.keys())
        for key in keys:
            value = symbols[key]
            if isinstance(value, dict):
                print("namedscope", key)
                self._dump_symbols(value)
            else:
                if isinstance(value, tuple):
                    print("%s %s" % (key.ljust(32), value))
                else:
                    print("%s 0x%02x" % (key.ljust(32), value))

    def dump_symbol_map(self) -> None:
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                print("Scope\n")
                self._dump_symbols(scope.symbols)

    def get_all_labels(self) -> List[Tuple[str, int]]:
        labels: List[Tuple[str, int]] = []
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                labels += scope.get_labels()

        return labels
