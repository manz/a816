"""Completions handler mixin for the LSP server.

Owns `_handle_completions` + the per-source completion builders (opcodes,
keywords, registers, local labels/symbols/macros, workspace labels/
symbols/macros), plus the context-aware pool-name completion when the
cursor sits after `.alloc … in`, `.relocate … into`, or `.reclaim`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lsprotocol.types import CompletionItem, CompletionItemKind, CompletionList, CompletionParams

from a816.cpu.cpu_65c816 import snes_opcode_table
from a816.parse.scanner_states import KEYWORDS
from a816.util import uri_to_path

if TYPE_CHECKING:
    from a816.lsp.document import A816Document
    from a816.lsp.workspace import WorkspaceIndex


class CompletionsMixin:
    """Completion handler set. Mixed into `A816LanguageServer`."""

    if TYPE_CHECKING:
        documents: dict[str, A816Document]
        _opcode_completions: list[CompletionItem]
        _keyword_completions: list[CompletionItem]
        _register_completions: list[CompletionItem]

        def _ensure_workspace_index(self) -> WorkspaceIndex | None: ...

    def _local_completions(self, doc: A816Document) -> list[CompletionItem]:
        items: list[CompletionItem] = []
        for label in doc.labels:
            items.append(
                CompletionItem(
                    label=label,
                    kind=CompletionItemKind.Function,
                    detail="Label",
                    documentation=doc.label_docstrings.get(label.lower()),
                )
            )
        for symbol in doc.symbols:
            items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))
        for macro_name in doc.macros:
            macro_parameters = doc.macro_params.get(macro_name, [])
            param_sig = f"({', '.join(macro_parameters)})" if macro_parameters else "()"
            macro_doc = doc.macro_docstrings.get(macro_name.lower())
            documentation = macro_doc or (
                f"User-defined macro with {len(macro_parameters)} parameters"
                if macro_parameters
                else "User-defined macro"
            )
            items.append(
                CompletionItem(
                    label=macro_name,
                    kind=CompletionItemKind.Function,
                    detail=f"User Macro{param_sig}",
                    documentation=documentation,
                )
            )
        return items

    def _handle_completions(self, params: CompletionParams) -> CompletionList:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return CompletionList(is_incomplete=False, items=[])
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return CompletionList(is_incomplete=False, items=[])

        line = doc.lines[line_num]
        char_pos = params.position.character
        word_start = char_pos
        while word_start > 0 and line[word_start - 1].isalnum():
            word_start -= 1
        current_word = line[word_start:char_pos].lower()

        # Context-aware: cursor after `in` / `into` / `.reclaim` → pool names only.
        pool_items = self._pool_name_completions_in_context(line, char_pos, doc)
        if pool_items is not None:
            filtered = (
                [item for item in pool_items if item.label.lower().startswith(current_word)]
                if current_word
                else pool_items
            )
            return CompletionList(is_incomplete=False, items=filtered[:50])

        all_items: list[CompletionItem] = []
        all_items.extend(self._opcode_completions)
        all_items.extend(self._keyword_completions)
        all_items.extend(self._register_completions)
        all_items.extend(self._build_labels_completions(doc))
        all_items.extend(self._local_completions(doc))
        workspace = self._ensure_workspace_index()
        if workspace:
            all_items.extend(self._build_workspace_label_completions(doc, workspace))
            all_items.extend(self._build_workspace_symbol_completions(doc, workspace))
            all_items.extend(self._build_workspace_macro_completions(doc, workspace))

        filtered = (
            [item for item in all_items if item.label.lower().startswith(current_word)] if current_word else all_items
        )
        return CompletionList(is_incomplete=False, items=filtered[:50])

    def _pool_name_completions_in_context(
        self, line: str, char_pos: int, doc: A816Document
    ) -> list[CompletionItem] | None:
        """When the cursor sits where a pool name is expected (after
        `.alloc X in `, `.relocate X N N into `, or `.reclaim `), suggest
        only declared pool names. Returns None when context doesn't match
        so the default completion list is used."""
        prefix = line[:char_pos].rstrip()
        triggers = (".alloc", ".relocate", ".reclaim")
        if not any(t in prefix for t in triggers):
            return None
        last = prefix.split()[-1].lower()
        if last not in ("in", "into", ".reclaim"):
            return None
        workspace = self._ensure_workspace_index()
        names: set[str] = set(doc.pools)
        if workspace:
            names |= set(workspace.pools)
        return [CompletionItem(label=name, kind=CompletionItemKind.Module, detail=".pool") for name in sorted(names)]

    def _build_opcode_completions(self) -> list[CompletionItem]:
        """Build completion items for all opcodes"""
        completions = []
        for opcode in snes_opcode_table.keys():
            completions.append(
                CompletionItem(
                    label=opcode.upper(),
                    kind=CompletionItemKind.Keyword,
                    detail="65c816 Instruction",
                    documentation=f"65c816 instruction with {len(snes_opcode_table[opcode])} addressing modes",
                )
            )

            # Add size variants
            for size in ["b", "w", "l"]:
                completions.append(
                    CompletionItem(
                        label=f"{opcode.upper()}.{size.upper()}",
                        kind=CompletionItemKind.Keyword,
                        detail=f"65c816 Instruction ({size.upper()})",
                        documentation=f"65c816 instruction with {size}-size specifier",
                    )
                )

        return completions

    def _build_keyword_completions(self) -> list[CompletionItem]:
        """Build completion items for assembler keywords"""
        return [
            CompletionItem(label=keyword.upper(), kind=CompletionItemKind.Keyword, detail="Assembler directive")
            for keyword in KEYWORDS
        ]

    def _build_register_completions(self) -> list[CompletionItem]:
        """Build completion items for registers"""
        registers = ["X", "Y", "S", "A"]
        return [
            CompletionItem(label=reg, kind=CompletionItemKind.Variable, detail="65c816 Register") for reg in registers
        ]

    def _build_labels_completions(self, doc: A816Document) -> list[CompletionItem]:
        return [
            CompletionItem(label=sym, kind=CompletionItemKind.Variable, detail="Symbol") for sym in doc.symbols.keys()
        ]

    def _build_workspace_label_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_labels = set(doc.labels.keys())
        items: list[CompletionItem] = []
        for label in workspace.labels.keys():
            if label in doc_labels:
                continue
            doc_text = workspace.get_label_doc(label)
            items.append(
                CompletionItem(
                    label=label,
                    kind=CompletionItemKind.Function,
                    detail="Label",
                    documentation=doc_text,
                )
            )
        return items

    def _build_workspace_symbol_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_symbols = set(doc.symbols.keys())
        items: list[CompletionItem] = []
        for symbol in workspace.symbols.keys():
            if symbol in doc_symbols:
                continue
            items.append(CompletionItem(label=symbol, kind=CompletionItemKind.Variable, detail="Symbol"))
        return items

    def _build_workspace_macro_completions(self, doc: A816Document, workspace: WorkspaceIndex) -> list[CompletionItem]:
        doc_macros = set(doc.macros.keys())
        items: list[CompletionItem] = []
        for macro in workspace.macros.keys():
            if macro in doc_macros:
                continue
            macro_params = workspace.get_macro_params(macro)
            param_sig = f"({', '.join(macro_params)})" if macro_params else ""
            macro_doc = workspace.get_macro_doc(macro)
            documentation = macro_doc or (
                f"User-defined macro with {len(macro_params)} parameters" if macro_params else "User-defined macro"
            )
            items.append(
                CompletionItem(
                    label=macro,
                    kind=CompletionItemKind.Function,
                    detail=f"User Macro{param_sig}",
                    documentation=documentation,
                )
            )
        return items

    def _workspace_container_name(self, uri: str, workspace: WorkspaceIndex) -> str | None:
        try:
            path = uri_to_path(uri)
        except ValueError:
            return None
        root = workspace.root_path
        if root:
            try:
                rel = path.relative_to(root)
                parent = rel.parent
                if parent and parent != Path(""):
                    return parent.as_posix()
                return None
            except ValueError:
                pass
        parent = path.parent
        if parent and parent != Path(""):
            return parent.as_posix()
        return None
