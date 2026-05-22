"""Hover handler mixin for the LSP server.

Owns `_handle_hover` + the per-kind hover renderers (opcode/keyword,
label/scope, macro, struct field, pool directive, `.include`/`.import`
module directive). Resolves include and module paths via the main
server's resolvers (declared as TYPE_CHECKING stubs).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from lsprotocol.types import Hover, HoverParams, MarkupContent, MarkupKind

from a816.cpu.cpu_65c816 import snes_opcode_table
from a816.parse.ast.nodes import StructAstNode
from a816.parse.scanner_states import KEYWORDS

if TYPE_CHECKING:
    from a816.lsp.document import A816Document
    from a816.lsp.workspace import WorkspaceIndex


class HoverMixin:
    """Hover handler set. Mixed into `A816LanguageServer`."""

    if TYPE_CHECKING:
        documents: dict[str, A816Document]

        def _ensure_workspace_index(self) -> WorkspaceIndex | None: ...
        def _resolve_include_path(self, include_path: str, current_uri: str) -> str | None: ...
        def _resolve_module_path(self, module_name: str, current_uri: str) -> str | None: ...

    _BIT_FIELD_TYPE_RE: ClassVar[re.Pattern[str]] = re.compile(r"u(\d+)")

    @staticmethod
    def _word_span(line: str, char_pos: int) -> tuple[int, int]:
        start = char_pos
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
            start -= 1
        end = char_pos
        while end < len(line) and (line[end].isalnum() or line[end] == "_"):
            end += 1
        return start, end

    @staticmethod
    def _markdown_hover(value: str) -> Hover:
        return Hover(contents=MarkupContent(kind=MarkupKind.Markdown, value=value))

    def _hover_for_opcode_or_keyword(self, base_word: str, word: str) -> Hover | None:
        if base_word in snes_opcode_table:
            return self._markdown_hover(
                f"**{word.upper()}** - 65c816 Instruction\n\nSupported addressing modes: {len(snes_opcode_table[base_word])}"
            )
        if base_word in KEYWORDS:
            return self._markdown_hover(f"**{word.upper()}** - Assembler directive")
        return None

    def _hover_for_label_or_scope(
        self, doc: A816Document, workspace: WorkspaceIndex | None, raw_word: str, word: str
    ) -> Hover | None:
        label_doc = doc.label_docstrings.get(word) or (workspace.get_label_doc(raw_word) if workspace else None)
        if label_doc:
            return self._markdown_hover(f"**{raw_word}**\n\n{label_doc}")
        scope_doc = doc.scope_docstrings.get(word) or (workspace.get_scope_doc(raw_word) if workspace else None)
        if scope_doc:
            return self._markdown_hover(f"**{raw_word}**\n\n{scope_doc}")
        return None

    def _hover_for_macro(
        self, doc: A816Document, workspace: WorkspaceIndex | None, raw_word: str, word: str
    ) -> Hover | None:
        macro_name = next((name for name in doc.macros if name.lower() == word), None)
        if macro_name:
            doc_text = doc.macro_docstrings.get(word)
            if doc_text:
                params_str = self._format_macro_params(doc.macro_params.get(macro_name, []))
                return self._markdown_hover(f"**{macro_name}{params_str}**\n\n{doc_text}")
        if workspace:
            macro_location = workspace.get_macro_location(raw_word)
            if macro_location:
                _, actual = macro_location
                doc_text = workspace.get_macro_doc(actual)
                if doc_text:
                    params_str = self._format_macro_params(workspace.get_macro_params(actual))
                    return self._markdown_hover(f"**{actual}{params_str}**\n\n{doc_text}")
        return None

    @staticmethod
    def _format_macro_params(param_list: list[str]) -> str:
        return f"({', '.join(param_list)})" if param_list else "()"

    def _handle_hover(self, params: HoverParams) -> Hover | None:
        doc = self.documents.get(params.text_document.uri)
        if not doc:
            return None
        line_num = params.position.line
        if line_num >= len(doc.lines):
            return None
        line = doc.lines[line_num]

        workspace = self._ensure_workspace_index()
        directive_hover = self._hover_for_module_directive(line, params.text_document.uri, workspace)
        if directive_hover:
            return directive_hover

        word_start, word_end = self._word_span(line, params.position.character)
        if word_start >= word_end:
            return None
        raw_word = line[word_start:word_end]
        word = raw_word.lower()
        base_word = word.split(".")[0] if "." in word else word

        opcode_or_keyword = self._hover_for_opcode_or_keyword(base_word, word)
        if opcode_or_keyword:
            return opcode_or_keyword
        pool_hover = self._hover_for_pool_directive(doc, raw_word)
        if pool_hover:
            return pool_hover
        dotted_word = self._dotted_word_span(line, params.position.character)
        struct_hover = self._hover_for_struct_field(dotted_word)
        if struct_hover:
            return struct_hover
        return self._hover_for_label_or_scope(doc, workspace, raw_word, word) or self._hover_for_macro(
            doc, workspace, raw_word, word
        )

    @staticmethod
    def _dotted_word_span(line: str, char_pos: int) -> str:
        """Like `_word_span` but stretches across `.` boundaries so
        `Type.field.mask` extracts as one string."""
        start = char_pos
        while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
            start -= 1
        end = char_pos
        while end < len(line) and (line[end].isalnum() or line[end] in "_."):
            end += 1
        return line[start:end]

    def _hover_for_struct_field(self, word: str) -> Hover | None:
        """Auto-doc for `Type.field`, `Type.field.mask`, `Type.field.shift`.

        Reaches into the workspace-shared struct registry built during AST
        indexing. Bit-field aux symbols get computed mask / shift values
        inlined into the hover so the user doesn't need to mentally compute
        them while editing.
        """
        struct, field, aux = self._parse_struct_field_path(word)
        if struct is None or field is None:
            return None
        meta = self._struct_field_meta(struct, field)
        if meta is None:
            return None
        field_type, bit_width, mask, shift = meta
        if aux == "mask":
            body = (
                f"**`{struct}.{field}.mask`** = `0x{mask:02X}`\n\n"
                f"Pre-shifted bit-mask for the `{field}` field "
                f"({bit_width}-bit, shift {shift}). Use as an immediate operand."
            )
            return self._markdown_hover(body)
        if aux == "shift":
            body = (
                f"**`{struct}.{field}.shift`** = `{shift}`\n\n"
                f"LSB position of the `{field}` field inside its containing byte."
            )
            return self._markdown_hover(body)
        if bit_width is not None:
            body = (
                f"**`{struct}.{field}`** — `{field_type}` bit-field\n\n"
                f"Width: `{bit_width}` bits, shift `{shift}`, mask `0x{mask:02X}`."
            )
        else:
            body = f"**`{struct}.{field}`** — `{field_type}` field of struct `{struct}`."
        return self._markdown_hover(body)

    @staticmethod
    def _parse_struct_field_path(word: str) -> tuple[str | None, str | None, str | None]:
        """Split `Type.field` / `Type.field.mask` / `Type.field.shift` into parts."""
        parts = word.split(".")
        if len(parts) == 2:
            return parts[0], parts[1], None
        if len(parts) == 3 and parts[2] in ("mask", "shift"):
            return parts[0], parts[1], parts[2]
        return None, None, None

    def _struct_field_meta(self, struct_name: str, field_name: str) -> tuple[str, int | None, int, int] | None:
        """Return `(declared_type, bit_width, mask, shift)` for a struct field, or None."""
        for doc in self.documents.values():
            for node in doc.ast_nodes:
                if isinstance(node, StructAstNode) and node.name == struct_name:
                    meta = self._field_meta_in_struct(node, field_name)
                    if meta is not None:
                        return meta
        return None

    def _field_meta_in_struct(self, node: StructAstNode, field_name: str) -> tuple[str, int | None, int, int] | None:
        bit_position = 0
        for fname, ftype in node.fields:
            bit_match = self._BIT_FIELD_TYPE_RE.fullmatch(ftype)
            if bit_match:
                width = int(bit_match.group(1))
                if fname == field_name:
                    shift = bit_position % 8
                    mask = ((1 << width) - 1) << shift
                    return ftype, width, mask, shift
                bit_position += width
                continue
            bit_position = 0  # primitive flushes the current bit run
            if fname == field_name:
                return ftype, None, 0, 0
        return None

    def _hover_for_pool_directive(self, doc: A816Document, word: str) -> Hover | None:
        if word in doc.pools:
            detail = doc.pool_details.get(word, "")
            consumer_count = len(doc.pool_consumers.get(word, []))
            body = f"**`.pool {word}`**\n\n{detail}\n\n{consumer_count} consumer(s) in this document"
            return self._markdown_hover(body)
        if word in doc.allocs:
            pool_name = doc.alloc_target_pool.get(word, "?")
            detail = doc.pool_details.get(pool_name, "")
            kind = ".relocate" if word in doc.allocs and pool_name in doc.alloc_target_pool.values() else ".alloc"
            del kind  # cannot disambiguate cheaply; show generic
            body = f"**`{word}`** in pool `{pool_name}`\n\n{detail}"
            return self._markdown_hover(body)
        return None

    def _hover_for_module_directive(
        self, line: str, current_uri: str, workspace: WorkspaceIndex | None
    ) -> Hover | None:
        """Return the target module's leading docstring when cursor lands on
        an `.include "path"` or `.import "module"` line."""
        if workspace is None:
            return None
        include_match = re.search(r'\.include\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
        import_match = re.search(r'\.import\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
        target_uri: str | None = None
        label: str | None = None
        if include_match:
            resolved = self._resolve_include_path(include_match.group(1), current_uri)
            if resolved:
                target_uri = Path(resolved).as_uri()
                label = include_match.group(1)
        elif import_match:
            resolved = self._resolve_module_path(import_match.group(1), current_uri)
            if resolved:
                target_uri = Path(resolved).as_uri()
                label = import_match.group(1)
        if not target_uri or label is None:
            return None
        docstring = workspace.module_docstrings.get(target_uri)
        if not docstring:
            return None
        return self._markdown_hover(f"**{label}**\n\n{docstring}")
