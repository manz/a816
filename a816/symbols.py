import logging
from collections.abc import ItemsView
from typing import Any

from a816.context import AssemblyContext
from a816.cpu.mapping import Address, Bus
from a816.cpu.types import RomType
from a816.exceptions import ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.nodes import BlockAstNode
from script import Table


def _is_exportable(name: str) -> bool:
    """Names with a single leading underscore are private to their scope.

    Mirrors the object-mode export rule: `_helper` stays local, `helper`
    promotes, and dunder names like `__size` (struct size symbol) keep
    promoting because they're system-injected, not user-private.
    """
    return not name.startswith("_") or name.startswith("__")


def _bubble_anon_into_named(scope: "Scope", parent: "NamedScope") -> None:
    """Surface labels/symbols from an anonymous scope into its NamedScope parent.

    A macro's arg-binding scope (or a plain `{ }` block) is anonymous, so
    its labels would be discarded on restore. When such a scope sits inside
    a NamedScope, we copy the exportable names up so the next
    NamedScope.restore_scope(exports=True) publishes them as `Name.label`.
    """
    for label_name, label_value in scope.labels.items():
        if _is_exportable(label_name) and label_name not in parent.labels:
            parent.labels[label_name] = label_value
    for sym_name, sym_value in scope.symbols.items():
        if _is_exportable(sym_name) and sym_name not in parent.symbols:
            parent.symbols[sym_name] = sym_value


def _publish_named_dotted(scope: "NamedScope", parent: "Scope") -> None:
    """Promote `Name.label` and `Name.symbol` into the parent scope.

    Both labels and symbols carry the dotted prefix so the object writer
    can distinguish CODE labels (need link-time rebasing) from DATA
    constants without re-walking scopes.
    """
    parent.symbols |= {f"{scope.name}.{k}": v for k, v in scope.symbols.items() if _is_exportable(k)}
    parent.labels |= {f"{scope.name}.{k}": v for k, v in scope.labels.items() if _is_exportable(k)}


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
        # Aliased externals: name -> expression string (e.g. "extern_sym + 1").
        # The alias name behaves like an external symbol locally; the linker
        # resolves it once the underlying externs are known.
        self.external_aliases: dict[str, str] = {}
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

    def add_external_alias(self, symbol: str, expression_str: str) -> None:
        """Register an alias whose value is a deferred expression over externs.

        The alias is treated as an external symbol locally so references
        emit deferred relocations. The linker resolves the alias by
        evaluating ``expression_str`` against the final symbol map.
        """
        self.external_symbols.add(symbol)
        self.external_aliases[symbol] = expression_str

    def lookup_alias(self, symbol: str) -> str | None:
        """Walk the scope chain looking for a registered external alias."""
        scope: Scope | None = self
        while scope is not None:
            if symbol in scope.external_aliases:
                return scope.external_aliases[symbol]
            scope = scope.parent
        return None

    def find_label_scope(self, name: str) -> "Scope | None":
        """Walk the scope chain looking for the scope that owns ``name`` as a label."""
        scope: Scope | None = self
        while scope is not None:
            if name in scope.labels:
                return scope
            scope = scope.parent
        return None

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
            # External alias declared at this level (e.g. macro arg bound to
            # an extern expression) needs to be visible here so eval can defer.
            if symbol in self.external_aliases:
                return self[symbol]
            return self.parent.value_for(symbol)
        else:
            return self[symbol]


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
        scope = self.current_scope
        parent = scope.parent
        if parent is None:
            raise RuntimeError("Current scope has no parent...")

        if not isinstance(scope, NamedScope) and isinstance(parent, NamedScope):
            _bubble_anon_into_named(scope, parent)
        if exports and isinstance(scope, NamedScope):
            _publish_named_dotted(scope, parent)

        self.current_scope = parent

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

    def get_all_labels(self, mangle_nested: bool = False) -> list[tuple[str, int]]:
        """Return labels across all non-internal scopes.

        When ``mangle_nested`` is true, labels in nested anonymous scopes are
        prefixed with ``__sc<idx>__`` to mirror the export naming used by
        :meth:`get_all_symbols`.
        """
        labels: list[tuple[str, int]] = []
        for idx, scope in enumerate(self.scopes):
            if isinstance(scope, InternalScope):
                continue
            mangle = mangle_nested and idx > 0 and not isinstance(scope, NamedScope)
            for name, value in scope.get_labels():
                exported = f"__sc{idx}__{name}" if mangle and not name.startswith("_") else name
                labels.append((exported, value))
        return labels

    @staticmethod
    def _mangle(name: str, idx: int, mangle: bool) -> str:
        # Names starting with `_` are explicitly local; keep their short form.
        return f"__sc{idx}__{name}" if mangle and not name.startswith("_") else name

    def _scope_int_symbols(self, scope: "Scope") -> list[tuple[str, int]]:
        return [(name, value) for name, value in scope.symbols.items() if isinstance(value, int)]

    def get_all_symbols(self) -> list[tuple[str, int]]:
        """All labels + int-valued assignments, mangled with __sc<idx>__ in nested anon scopes."""
        symbols: list[tuple[str, int]] = []
        seen: set[str] = set()
        for idx, scope in enumerate(self.scopes):
            if isinstance(scope, InternalScope):
                continue
            mangle = idx > 0 and not isinstance(scope, NamedScope)
            for source in (scope.get_labels(), self._scope_int_symbols(scope)):
                for name, value in source:
                    exported = self._mangle(name, idx, mangle)
                    if exported not in seen:
                        symbols.append((exported, value))
                        seen.add(exported)
        return symbols

    def is_root_scope_symbol(self, name: str) -> bool:
        """Check whether ``name`` is defined directly in the root scope.

        Used by the object-file emitter to decide whether a symbol should be
        exported as GLOBAL (root scope or named scope) or LOCAL (nested
        anonymous block).
        """
        if not self.scopes:
            return False
        root = self.scopes[0]
        if name in root.symbols or name in root.code_symbols or name in root.labels:
            return True
        # NamedScope contributes its dotted exports back into the root via
        # restore_scope(exports=True), so "name.foo" entries also live at the
        # root once the scope is closed.
        for scope in self.scopes:
            if isinstance(scope, NamedScope):
                qualified = f"{scope.name}.{name}"
                if qualified in root.symbols or qualified in root.labels:
                    return True
        return False
