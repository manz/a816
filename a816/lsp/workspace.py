"""`WorkspaceIndex`: cross-file symbol resolution for the LSP server.

Discovers the project's entrypoint (pragma → `a816.toml` → first `*.s`),
walks `.include` / `.import` chains from there, and indexes every reachable
document's labels / symbols / macros / pools / allocs into per-name lookup
tables the language server queries for completion / goto-def / hover.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from lsprotocol.types import Diagnostic, DiagnosticSeverity, Location, Position, Range

from a816.lsp.document import A816Document
from a816.stdlib import resolve_stdlib_module
from a816.util import uri_to_path

logger = logging.getLogger(__name__)


class WorkspaceIndex:
    """Indexes workspace files to provide cross-file symbol resolution."""

    ENTRYPOINT_PRAGMA = ";! a816-lsp entrypoint"

    def __init__(self, root_path: Path | str | None):
        self.root_path = Path(root_path).resolve() if root_path else None
        self.entrypoint: Path | None = None
        self.include_paths: list[Path] = []
        self.module_paths: list[Path] = []
        self.documents: dict[str, A816Document] = {}
        self.labels: dict[str, tuple[Position, str]] = {}
        self.symbols: dict[str, tuple[Position, str]] = {}
        self.macros: dict[str, tuple[Position, str]] = {}
        self.pools: dict[str, tuple[Position, str]] = {}
        self.allocs: dict[str, tuple[Position, str]] = {}
        self.macro_params: dict[str, list[str]] = {}
        self.macro_docstrings: dict[str, str] = {}
        self.label_docstrings: dict[str, str] = {}
        self.scope_docstrings: dict[str, str] = {}
        # uri -> module-level docstring, surfaced when hovering an `.import`
        # or `.include` token that resolves to that file.
        self.module_docstrings: dict[str, str] = {}
        self.doc_labels: dict[str, set[str]] = {}
        self.doc_symbols: dict[str, set[str]] = {}
        self.doc_macros: dict[str, set[str]] = {}
        self.doc_label_docstrings: dict[str, set[str]] = {}
        self.doc_macro_docstrings: dict[str, set[str]] = {}
        self.doc_scope_docstrings: dict[str, set[str]] = {}
        self.doc_macro_params: dict[str, set[str]] = {}
        self.label_name_lookup: dict[str, str] = {}
        self.macro_name_lookup: dict[str, str] = {}
        self.scope_name_lookup: dict[str, str] = {}
        self.built = False

    def clear(self) -> None:
        self.documents.clear()
        self.include_paths.clear()
        self.module_paths.clear()
        self.labels.clear()
        self.symbols.clear()
        self.macros.clear()
        self.pools.clear()
        self.allocs.clear()
        self.macro_params.clear()
        self.macro_docstrings.clear()
        self.label_docstrings.clear()
        self.scope_docstrings.clear()
        self.module_docstrings.clear()
        self.doc_labels.clear()
        self.doc_symbols.clear()
        self.doc_macros.clear()
        self.doc_label_docstrings.clear()
        self.doc_macro_docstrings.clear()
        self.doc_scope_docstrings.clear()
        self.doc_macro_params.clear()
        self.label_name_lookup.clear()
        self.macro_name_lookup.clear()
        self.scope_name_lookup.clear()

    def rebuild(self) -> None:
        """Re-index the workspace from the detected entrypoint."""
        self.clear()
        self.entrypoint = self._detect_entrypoint()
        if not self.entrypoint:
            logger.debug("WorkspaceIndex: no entrypoint detected")
            self.built = True
            return
        self._explore_from(self.entrypoint)
        self.built = True

    def replace_document(self, doc: A816Document) -> None:
        """Add or update a document inside the workspace index.

        Also walks the doc's `.import` chain so a file opened outside
        the auto-detected entrypoint still pulls its dependencies'
        symbols (pool decls in particular). Without this, an LSP
        rooted at a multi-project repo would only know about the
        entrypoint's project, and other projects' alloc sites would
        all error with `undeclared pool`."""
        if not doc.uri:
            return
        self._prune_previous_entries(doc.uri)
        self._store_document(doc)
        self._crawl_imports_from(uri_to_path(doc.uri), doc.content)
        self.built = True

    def _crawl_imports_from(self, source_path: Path, content: str) -> None:
        """Index every file transitively reachable via `.include` / `.import`
        from `source_path`. Skips files already in the index so updates
        from open buffers aren't clobbered by their on-disk versions."""
        queue: list[Path] = list(self._extract_includes(source_path, content))
        visited: set[Path] = {source_path.resolve()}
        while queue:
            current = queue.pop().resolve()
            if current in visited:
                continue
            visited.add(current)
            if current.as_uri() in self.documents:
                continue
            sub_content = self._read_or_log(current)
            if sub_content is None:
                continue
            self._store_document(A816Document(current.as_uri(), sub_content, include_paths=self.include_paths))
            queue.extend(self._extract_includes(current, sub_content))

    def remove_document(self, uri: str) -> None:
        """Remove a document from the index."""
        self._prune_previous_entries(uri)
        self.documents.pop(uri, None)

    def reload_document_from_disk(self, uri: str) -> None:
        """Reload a document from disk after closing it."""
        path = uri_to_path(uri)
        if not path.exists():
            self.remove_document(uri)
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            self.remove_document(uri)
            return
        doc = A816Document(path.as_uri(), content, include_paths=self.include_paths)
        self.replace_document(doc)

    def get_label_location(self, name: str) -> Location | None:
        actual_name = name if name in self.labels else self.label_name_lookup.get(name.lower())
        if not actual_name:
            return None
        position, uri = self.labels.get(actual_name, (None, None))
        if position is None or uri is None:
            return None
        end = Position(line=position.line, character=position.character + len(actual_name))
        return Location(uri=uri, range=Range(start=position, end=end))

    def get_symbol_location(self, name: str) -> Location | None:
        entry = self.symbols.get(name)
        if not entry:
            return None
        position, uri = entry
        end = Position(line=position.line, character=position.character + len(name))
        return Location(uri=uri, range=Range(start=position, end=end))

    def get_macro_location(self, name: str) -> tuple[Location, str] | None:
        actual_name = name if name in self.macros else self.macro_name_lookup.get(name.lower())
        if not actual_name:
            return None
        position, uri = self.macros.get(actual_name, (None, None))
        if position is None or uri is None:
            return None
        end = Position(line=position.line, character=position.character + len(actual_name))
        location = Location(uri=uri, range=Range(start=position, end=end))
        return location, actual_name

    def get_label_doc(self, name: str) -> str | None:
        return self.label_docstrings.get(name.lower())

    def get_macro_doc(self, name: str) -> str | None:
        return self.macro_docstrings.get(name.lower())

    def get_scope_doc(self, name: str) -> str | None:
        return self.scope_docstrings.get(name.lower())

    def get_macro_params(self, name: str) -> list[str]:
        return self.macro_params.get(name, [])

    def _detect_entrypoint(self) -> Path | None:
        if not self.root_path:
            return None
        pragma = self._entry_from_pragma()
        if pragma:
            return pragma
        config = self._entry_from_config()
        if config:
            return config
        return self._fallback_entrypoint()

    def _file_declares_entrypoint(self, path: Path) -> bool:
        """Look for ENTRYPOINT_PRAGMA in the first 64 lines of the file."""
        try:
            with path.open("r", encoding="utf-8") as handle:
                for _ in range(64):
                    line = handle.readline()
                    if not line:
                        return False
                    if self.ENTRYPOINT_PRAGMA in line:
                        return True
        except OSError:
            return False
        return False

    def _entry_from_pragma(self) -> Path | None:
        if not self.root_path:
            return None
        for path in sorted(self.root_path.rglob("*.s")):
            if self._file_declares_entrypoint(path):
                return path.resolve()
        return None

    def _find_a816_toml(self) -> Path | None:
        if self.root_path is None:
            return None
        current = self.root_path
        while True:
            candidate = current / "a816.toml"
            if candidate.exists():
                return candidate
            if current.parent == current:
                return None
            current = current.parent

    def _merge_path_list(self, target: list[Path], config_root: Path, paths: list[str]) -> None:
        for p in paths:
            resolved = (config_root / p).resolve()
            if resolved not in target:
                target.append(resolved)

    def _entry_from_config(self) -> Path | None:
        if tomllib is None:
            return None
        config_file = self._find_a816_toml()
        if config_file is None:
            return None
        try:
            with config_file.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None

        config_root = config_file.parent
        self._merge_path_list(self.include_paths, config_root, data.get("include-paths", []))
        self._merge_path_list(self.module_paths, config_root, data.get("module-paths", []))

        entry = data.get("entrypoint")
        if not entry:
            return None
        result = (config_root / entry).resolve()
        return result if result.exists() else None

    def _fallback_entrypoint(self) -> Path | None:
        if not self.root_path:
            return None
        default = (self.root_path / "ff4.s").resolve()
        if default.exists():
            return default
        for path in sorted(self.root_path.rglob("*.s")):
            return path.resolve()
        return None

    def _read_or_log(self, current: Path) -> str | None:
        if not current.exists():
            logger.debug("WorkspaceIndex: include not found %s", current)
            return None
        try:
            return current.read_text(encoding="utf-8")
        except OSError:
            logger.debug("WorkspaceIndex: unable to read %s", current)
            return None

    def _explore_from(self, entrypoint: Path) -> None:
        queue: list[Path] = [entrypoint.resolve()]
        visited: set[Path] = set()
        while queue:
            current = queue.pop()
            if current in visited:
                continue
            visited.add(current)
            content = self._read_or_log(current)
            if content is None:
                continue
            self._store_document(A816Document(current.as_uri(), content, include_paths=self.include_paths))
            queue.extend(include for include in self._extract_includes(current, content) if include not in visited)

    @staticmethod
    def _resolve_in_paths(file_name: str, file_dir: Path, search_paths: list[Path]) -> Path | None:
        candidate = (file_dir / file_name).resolve()
        if candidate.exists():
            return candidate
        for search_dir in search_paths:
            candidate = (search_dir / file_name).resolve()
            if candidate.exists():
                return candidate
        return None

    def _extract_includes(self, file_path: Path, content: str) -> list[Path]:
        found_paths: set[Path] = set()
        for match in re.finditer(r"\.include\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            resolved = self._resolve_in_paths(match.group(1).strip(), file_path.parent, self.include_paths)
            if resolved:
                found_paths.add(resolved)
        for match in re.finditer(r"\.import\s+['\"]([^'\"]+)['\"]", content, re.IGNORECASE):
            module_name = match.group(1).strip()
            # Stdlib `@std/...` modules live inside the wheel — check there
            # first so the crawler indexes them like any user module.
            stdlib_path = resolve_stdlib_module(module_name, ".s")
            if stdlib_path is not None:
                found_paths.add(stdlib_path)
                continue
            resolved = self._resolve_in_paths(module_name + ".s", file_path.parent, self.module_paths)
            if resolved:
                found_paths.add(resolved)
        return list(found_paths)

    def _index_labels(self, doc: A816Document) -> set[str]:
        names = set(doc.labels.keys())
        for label in names:
            self.labels[label] = doc.labels[label]
            self.label_name_lookup[label.lower()] = label
        return names

    def _index_symbols(self, doc: A816Document) -> set[str]:
        names = set(doc.symbols.keys())
        for symbol in names:
            self.symbols[symbol] = doc.symbols[symbol]
        return names

    def _index_macros(self, doc: A816Document) -> set[str]:
        names = set(doc.macros.keys())
        for macro in names:
            self.macros[macro] = doc.macros[macro]
            self.macro_name_lookup[macro.lower()] = macro
            if macro in doc.macro_params:
                self.macro_params[macro] = doc.macro_params[macro]
        return names

    def _index_pools(self, doc: A816Document) -> set[str]:
        names = set(doc.pools.keys())
        for pool in names:
            self.pools[pool] = doc.pools[pool]
        return names

    def undeclared_pool_diagnostics(self, doc: A816Document) -> list[Diagnostic]:
        """Flag `.alloc / .relocate / .reclaim` references to pool names
        not declared anywhere in the workspace. Lives here (not on the
        document) because pool decls cross file boundaries — the
        preamble owns them, sub-modules consume them."""
        return [
            Diagnostic(
                range=Range(
                    start=pos,
                    end=Position(line=pos.line, character=pos.character + len(pool_name)),
                ),
                message=f".{kind} references undeclared pool {pool_name!r}",
                severity=DiagnosticSeverity.Error,
                source="a816 pool",
            )
            for pool_name, refs in doc.pool_consumers.items()
            if pool_name not in self.pools
            for pos, kind in refs
        ]

    def _index_allocs(self, doc: A816Document) -> set[str]:
        names = set(doc.allocs.keys())
        for alloc in names:
            self.allocs[alloc] = doc.allocs[alloc]
        return names

    def _merge_docstrings(self, doc: A816Document) -> None:
        self.label_docstrings.update(doc.label_docstrings)
        self.macro_docstrings.update(doc.macro_docstrings)
        for key, value in doc.scope_docstrings.items():
            self.scope_docstrings[key] = value
            self.scope_name_lookup[key] = key

    def _store_document(self, doc: A816Document) -> None:
        if not doc.uri:
            return
        self.documents[doc.uri] = doc
        new_labels = self._index_labels(doc)
        new_symbols = self._index_symbols(doc)
        new_macros = self._index_macros(doc)
        self._index_pools(doc)
        self._index_allocs(doc)
        self._merge_docstrings(doc)
        if doc.module_docstring:
            self.module_docstrings[doc.uri] = doc.module_docstring
        else:
            self.module_docstrings.pop(doc.uri, None)

        self.doc_labels[doc.uri] = new_labels
        self.doc_symbols[doc.uri] = new_symbols
        self.doc_macros[doc.uri] = new_macros
        self.doc_label_docstrings[doc.uri] = set(doc.label_docstrings.keys())
        self.doc_macro_docstrings[doc.uri] = set(doc.macro_docstrings.keys())
        self.doc_scope_docstrings[doc.uri] = set(doc.scope_docstrings.keys())
        self.doc_macro_params[doc.uri] = set(doc.macro_params.keys())

    @staticmethod
    def _drop_owned(
        store: dict[str, tuple[Position, str]],
        names: set[str],
        uri: str,
        lookup: dict[str, str] | None = None,
    ) -> None:
        for name in names:
            entry = store.get(name)
            if entry and entry[1] == uri:
                store.pop(name, None)
                if lookup is not None:
                    lookup.pop(name.lower(), None)

    def _prune_previous_entries(self, uri: str) -> None:
        self._drop_owned(self.labels, self.doc_labels.get(uri, set()), uri, self.label_name_lookup)
        self._drop_owned(self.symbols, self.doc_symbols.get(uri, set()), uri)
        self._drop_owned(self.macros, self.doc_macros.get(uri, set()), uri, self.macro_name_lookup)
        for macro in self.doc_macros.get(uri, set()):
            self.macro_params.pop(macro, None)
        for key in self.doc_label_docstrings.get(uri, set()):
            self.label_docstrings.pop(key, None)
        for key in self.doc_macro_docstrings.get(uri, set()):
            self.macro_docstrings.pop(key, None)
        for key in self.doc_scope_docstrings.get(uri, set()):
            self.scope_docstrings.pop(key, None)
            self.scope_name_lookup.pop(key, None)
        self.module_docstrings.pop(uri, None)
        for store in (
            self.doc_labels,
            self.doc_symbols,
            self.doc_macros,
            self.doc_label_docstrings,
            self.doc_macro_docstrings,
            self.doc_scope_docstrings,
            self.doc_macro_params,
        ):
            store.pop(uri, None)
