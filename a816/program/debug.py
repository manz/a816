"""DebugMixin: `.adbg` debug-info capture for assembled (non-linked) output."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a816.parse.nodes import LinkedModuleNode
from a816.protocols import NodeProtocol

if TYPE_CHECKING:
    from a816.symbols import Resolver


class DebugMixin:
    """Debug-info handler set. Mixed into `Program`."""

    if TYPE_CHECKING:
        resolver: Resolver
        logger: logging.Logger
        _debug_capture: bool
        _debug_lines: list[tuple[int, str, int, int]]
        _program_nodes: list[NodeProtocol]

    def enable_debug_capture(self) -> None:
        """Turn on per-node line capture for `.adbg` emission."""
        self._debug_capture = True
        self._debug_lines = []

    def build_debug_info(self, main_source: Path | str) -> Any:
        """Build a DebugInfo from captured state. Returns a a816.debug_info.DebugInfo."""
        from a816.debug_info import DebugInfo, LineEntry, ModuleEntry, SymbolEntry, SymbolKind, SymbolScope

        info = DebugInfo()
        main_path = str(Path(main_source))
        info.add_file(main_path)
        # Index 0 covers the entry-point translation unit; module 0 is __main__.
        info.modules.append(ModuleEntry(name="__main__", file_idx=0, base=0))

        # One entry per LinkedModuleNode: source file, name, load base.
        module_index_by_name: dict[str, int] = {"__main__": 0}
        for node in self._program_nodes:
            if not isinstance(node, LinkedModuleNode):
                continue
            base = getattr(node, "base_address", 0)
            file_idx = info.add_file(node.module_name + ".s")
            module_index_by_name[node.module_name] = info.add_module(node.module_name, file_idx, base)

        # Symbols: every label gets a SymbolEntry. Module ownership resolves
        # by walking known module bases; falls back to NO_MODULE for the main TU.
        # `.label` declarations land in `absolute_labels`; emit them with the
        # same LABEL kind so `lookup_label(addr)` resolves.
        for name, value in (*self.resolver.get_all_labels(), *self.resolver.get_all_absolute_labels()):
            module_idx = self._guess_module(value, info)
            info.symbols.append(
                SymbolEntry(
                    name=name,
                    address=value,
                    scope=SymbolScope.GLOBAL,
                    module_idx=module_idx,
                    kind=SymbolKind.LABEL,
                )
            )

        # Line entries collected during emit().
        for address, filename, line, column in self._debug_lines:
            file_idx = info.add_file(filename)
            module_idx = self._guess_module_by_filename(filename, module_index_by_name)
            info.lines.append(
                LineEntry(
                    address=address,
                    file_idx=file_idx,
                    line=line,
                    column=column,
                    module_idx=module_idx,
                )
            )

        return info

    def _guess_module(self, address: int, info: Any) -> int:
        """Pick the module whose base is closest to (and ≤) `address`."""
        from a816.debug_info import NO_MODULE

        best_idx = NO_MODULE
        best_base = -1
        for idx, module in enumerate(info.modules):
            if module.base <= address and module.base > best_base:
                best_idx = idx
                best_base = module.base
        return best_idx

    def _guess_module_by_filename(self, filename: str, by_name: dict[str, int]) -> int:
        from a816.debug_info import NO_MODULE

        # Module sources end with `<name>.s`; match by basename without extension.
        stem = Path(filename).stem
        return by_name.get(stem, NO_MODULE)
