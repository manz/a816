"""A816Formatter: AST → formatted assembly source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from a816.exceptions import FormattingError
from a816.formatter.operand_emit import format_data, format_macro_apply, format_opcode
from a816.formatter.options import FormattingOptions
from a816.formatter.post_process import finalize_formatting
from a816.parse.ast.nodes import (
    AllocAstNode,
    AsciiAstNode,
    AstNode,
    BlockAstNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    CompoundAstNode,
    DataNode,
    DocstringAstNode,
    ExternAstNode,
    ForAstNode,
    IfAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    LabelAstNode,
    LabelDeclAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    OpcodeAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.mzparser import A816Parser


class A816Formatter:
    """Assembly code formatter using AST"""

    def __init__(self, options: FormattingOptions | None = None):
        self.options = options or FormattingOptions()
        self._instruction_like_nodes = (
            OpcodeAstNode,
            DataNode,
            TextAstNode,
            AsciiAstNode,
            MacroApplyAstNode,
            IncludeAstNode,
            DocstringAstNode,
            ExternAstNode,
            LabelDeclAstNode,
            SymbolAffectationAstNode,
            CodePositionAstNode,
            CodeRelocationAstNode,
            CodeLookupAstNode,
        )

    def format_text(
        self,
        content: str,
        file_path: str | None = None,
        include_paths: list[Path] | None = None,
    ) -> str:
        """Format assembly code from text content"""
        source = file_path or "<input>"
        try:
            result = A816Parser.parse_as_ast(content, source, include_paths=include_paths)

            if result.error:
                raise FormattingError(f"Unable to format {source}:\n{result.error}")

            return self._format_with_preserved_blanks(content, result.nodes)

        except ParserSyntaxError as exc:
            raise FormattingError(f"Unable to format {source}: {exc}") from exc
        except (AttributeError, KeyError, IndexError, TypeError) as exc:
            raise FormattingError(f"Unexpected formatter failure for {source}: {exc}") from exc

    def format_file(self, file_path: str | Path) -> str:
        """Format assembly code from a file"""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.format_text(content, str(path))

    def _emit_blank_gap(self, lines: list[str], prev_line: int | None, node_line: int | None) -> None:
        if prev_line is None or node_line is None or node_line - prev_line <= 1:
            return
        gap = min(node_line - prev_line - 1, self.options.max_empty_lines)
        for _ in range(gap):
            lines.append("")

    @staticmethod
    def _try_fold_inline_comment(node: AstNode, node_line: int | None, prev_line: int | None, lines: list[str]) -> bool:
        """Fold a same-source-line comment onto the previous instruction.
        Returns True when the fold succeeded so the caller skips the
        normal node-emission path.
        """
        if not isinstance(node, CommentAstNode) or node_line is None or node_line != prev_line:
            return False
        if not lines or not lines[-1].strip() or lines[-1].lstrip().startswith(";"):
            return False
        comment = node.comment.strip()
        if not comment.startswith(";"):
            comment = f"; {comment}"
        lines[-1] = f"{lines[-1].rstrip()} {comment}"
        return True

    def _emit_node_lines(
        self,
        node: AstNode,
        should_indent: bool,
        indent_after_label: bool,
        lines: list[str],
    ) -> None:
        node_lines = self._format_ast(node, should_indent, indent_after_label=indent_after_label)
        # Docstrings after a label hug it flush-left, no body indent.
        indent_body_node = (
            should_indent and isinstance(node, self._instruction_like_nodes) and not isinstance(node, DocstringAstNode)
        )
        if indent_body_node:
            node_lines = [
                self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
            ]
        lines.extend(node_lines)

    _BLOCK_LIKE_AST: ClassVar[tuple[type, ...]] = (IfAstNode, ForAstNode, MacroAstNode, ScopeAstNode, CompoundAstNode)

    def _advance_prev_line(self, node: AstNode, node_line: int | None) -> int | None:
        # Closing braces of block-like nodes sit on source lines with no
        # AST entry; bump past them so the gap heuristic doesn't read the
        # brace lines as blank padding. For nested block-likes (e.g.
        # `.if OUTER { .if INNER { ... } }`) every nested block contributes
        # its own closing brace line — scan the source for the consecutive
        # `}` lines that immediately follow the tail leaf.
        tail_line = self._ast_max_line(node) or node_line
        if tail_line is None:
            return None
        if isinstance(node, self._BLOCK_LIKE_AST):
            return self._skip_trailing_close_braces(node, tail_line)
        return tail_line

    def _skip_trailing_close_braces(self, node: AstNode, tail_line: int) -> int:
        """Return the source line number of the outermost closing `}`
        that belongs to `node`. Walks forward from `tail_line` and stops
        when a non-`}`-only line is encountered.

        Uses the source file recorded on the node's `file_info.position`
        when available; falls back to `tail_line + 1` (legacy single-brace
        behaviour) when the source isn't reachable.
        """
        position = getattr(node.file_info, "position", None)
        source_file = getattr(position, "file", None) if position else None
        source_lines = getattr(source_file, "lines", None) if source_file else None
        if not source_lines:
            return tail_line + 1
        i = tail_line + 1
        last = tail_line
        while i < len(source_lines):
            stripped = source_lines[i].strip()
            if stripped == "}":
                last = i
                i += 1
                continue
            break
        return last

    def _format_compound(self, ast: CompoundAstNode, indent_instructions: bool, indent_after_label: bool) -> list[str]:
        lines: list[str] = []
        prev_was_label = False
        prev_line: int | None = None
        for node in ast.body:
            node_line = self._node_line_num(node)
            self._emit_blank_gap(lines, prev_line, node_line)

            if self._try_fold_inline_comment(node, node_line, prev_line, lines):
                prev_was_label = False
                prev_line = node_line
                continue

            # A nested `{ ... }` block scopes its contents (anonymous
            # scope or function body); preserve the braces so symbol
            # affectations inside don't leak into the parent scope and
            # collide with siblings — fluff was previously dropping the
            # braces, which fused two adjacent local equates into one
            # duplicate-symbol error at link time.
            if isinstance(node, CompoundAstNode):
                inner_lines = self._format_ast(node, True, indent_after_label=indent_after_label)
                lines.append("{")
                lines.extend(self._indent_block_lines(inner_lines))
                lines.append("}")
                prev_was_label = False
                prev_line = self._advance_prev_line(node, node_line)
                continue

            should_indent = indent_instructions or (indent_after_label and prev_was_label)
            self._emit_node_lines(node, should_indent, indent_after_label, lines)
            prev_was_label = isinstance(node, LabelAstNode)
            prev_line = self._advance_prev_line(node, node_line)
        return lines

    def _format_block(self, ast: BlockAstNode, indent_after_label: bool) -> list[str]:
        # Mirror `_format_compound`'s blank-line preservation and inline-comment
        # folding so `.alloc { ... }` bodies (which are BlockAstNode) keep the
        # author's vertical whitespace and trailing `; comment` association.
        lines: list[str] = []
        prev_line: int | None = None
        for node in ast.body:
            node_line = self._node_line_num(node)
            self._emit_blank_gap(lines, prev_line, node_line)

            if self._try_fold_inline_comment(node, node_line, prev_line, lines):
                prev_line = node_line
                continue

            if isinstance(node, CompoundAstNode):
                inner_lines = self._format_ast(node, True, indent_after_label=indent_after_label)
                lines.append("{")
                lines.extend(self._indent_block_lines(inner_lines))
                lines.append("}")
                prev_line = self._advance_prev_line(node, node_line)
                continue

            lines.extend(self._format_ast(node, True, indent_after_label=indent_after_label))
            prev_line = self._advance_prev_line(node, node_line)
        return lines

    def _format_comment(self, ast: CommentAstNode) -> list[str]:
        comment = ast.comment.strip()
        # C-style block comments emit verbatim (one output line per
        # source line) so the closing `*/` keeps its position; the prior
        # behavior turned `/* multi\nline */` into `; /* multi\nline */`
        # which broke the second line.
        if comment.startswith("/*") and comment.endswith("*/"):
            return comment.splitlines()
        if not comment.startswith(";"):
            comment = f"; {comment}"
        return [comment]

    def _format_scope(self, ast: ScopeAstNode) -> list[str]:
        lines = [f".scope {ast.name} {{"]
        if ast.docstring:
            lines.extend(self._format_docstring(ast.docstring, indent_level=1))
        lines.extend(self._indent_block_lines(self._format_ast(ast.body, True)))
        lines.append("}")
        return lines

    def _format_symbol_affectation(self, ast: SymbolAffectationAstNode) -> list[str]:
        value = ast.value.to_canonical() if ast.value else ""
        return [f"{ast.symbol} = {value}"]

    # Single-line emitters keyed by AST node type.
    _SINGLE_LINE_EMITTERS: ClassVar[dict[type, Callable[[Any, Any], str]]] = {
        LabelAstNode: lambda self, ast: ast.to_canonical(),
        OpcodeAstNode: lambda self, ast: format_opcode(ast, self.options),
        TextAstNode: lambda self, ast: f'.text "{ast.text}"',
        AsciiAstNode: lambda self, ast: f'.ascii "{ast.text}"',
        DataNode: lambda self, ast: format_data(ast, self.options),
        MacroApplyAstNode: lambda self, ast: format_macro_apply(ast, self.options),
        IncludeAstNode: lambda self, ast: f'.include "{ast.file_path}"',
        IncludeIpsAstNode: lambda self, ast: f'.include_ips "{ast.file_path}"',
        IncludeBinaryAstNode: lambda self, ast: f'.incbin "{ast.file_path}"',
        TableAstNode: lambda self, ast: f'.table "{ast.file_path}"',
        ExternAstNode: lambda self, ast: f".extern {ast.symbol}",
        CodePositionAstNode: lambda self, ast: ast.to_canonical(),
        CodeRelocationAstNode: lambda self, ast: ast.to_canonical(),
        CodeLookupAstNode: lambda self, ast: f"{{{{ {ast.symbol} }}}}",
    }

    def _format_ast(
        self, ast: AstNode, indent_instructions: bool = False, *, indent_after_label: bool = True
    ) -> list[str]:
        """Format an AST node into lines of text."""
        if isinstance(ast, CompoundAstNode):
            return self._format_compound(ast, indent_instructions, indent_after_label)
        if isinstance(ast, BlockAstNode):
            return self._format_block(ast, indent_after_label)
        if isinstance(ast, CommentAstNode):
            return self._format_comment(ast)
        if isinstance(ast, ScopeAstNode):
            return self._format_scope(ast)
        if isinstance(ast, AllocAstNode):
            return self._format_alloc(ast)
        if isinstance(ast, RelocateAstNode):
            return self._format_relocate(ast)
        if isinstance(ast, PoolAstNode):
            return self._format_pool(ast)
        if isinstance(ast, ReclaimAstNode):
            return [self._format_reclaim(ast)]
        if isinstance(ast, MacroAstNode):
            return self._format_macro(ast)
        if isinstance(ast, DocstringAstNode):
            return self._format_docstring(ast.text)
        if isinstance(ast, IfAstNode):
            return self._format_if(ast)
        if isinstance(ast, ForAstNode):
            return self._format_for(ast)
        if isinstance(ast, StructAstNode):
            return ast.to_canonical().splitlines()
        if isinstance(ast, SymbolAffectationAstNode):
            return self._format_symbol_affectation(ast)

        emitter = self._SINGLE_LINE_EMITTERS.get(type(ast))
        if emitter is not None:
            return [emitter(self, ast)]
        return ast.to_canonical().splitlines()

    def _format_macro(self, macro_ast: MacroAstNode) -> list[str]:
        """Format a macro definition with its body"""
        params = ", ".join(macro_ast.args) if self.options.space_after_comma else ",".join(macro_ast.args)
        header = f".macro {macro_ast.name}"
        if macro_ast.args:
            header += f"({params})"
        else:
            header += "()"
        header += " {"

        body_lines = []
        if macro_ast.docstring:
            body_lines.extend(self._format_docstring(macro_ast.docstring, indent_level=1))
        body_lines.extend(self._indent_block_lines(self._format_ast(macro_ast.block)))

        return [header, *body_lines, "}"]

    def _format_alloc(self, ast: AllocAstNode) -> list[str]:
        """Format `.alloc NAME in POOL { body }`."""
        lines = [f".alloc {ast.name} in {ast.pool_name} {{"]
        lines.extend(self._indent_block_lines(self._format_ast(ast.body, True)))
        lines.append("}")
        return lines

    def _format_relocate(self, ast: RelocateAstNode) -> list[str]:
        """Format `.relocate SYMBOL OLD_START OLD_END into POOL { body }`."""
        old_start = ast.old_start.to_canonical()
        old_end = ast.old_end.to_canonical()
        lines = [f".relocate {ast.symbol} {old_start} {old_end} into {ast.pool_name} {{"]
        lines.extend(self._indent_block_lines(self._format_ast(ast.body, True)))
        lines.append("}")
        return lines

    def _format_pool(self, ast: PoolAstNode) -> list[str]:
        """Format `.pool NAME { range / fill / strategy ... }`.

        Omits `fill` when it evaluates to the default 0 — the formatter
        can't tell whether `fill 0` was source-explicit or parser-injected,
        so we err toward not materialising defaults. Users who genuinely
        want `fill 0` in the source will see it round-tripped to nothing,
        but the semantics are identical."""
        lines = [f".pool {ast.pool_name} {{"]
        for lo, hi in ast.ranges:
            lines.append(f"    range {lo.to_canonical()} {hi.to_canonical()}")
        fill_canonical = ast.fill.to_canonical()
        if fill_canonical.strip() != "0":
            lines.append(f"    fill {fill_canonical}")
        lines.append(f"    strategy {ast.strategy}")
        lines.append("}")
        return lines

    def _format_reclaim(self, ast: ReclaimAstNode) -> str:
        return f".reclaim {ast.pool_name} {ast.start.to_canonical()} {ast.end.to_canonical()}"

    def _format_if(self, if_ast: IfAstNode) -> list[str]:
        """Format an if statement with explicit braces"""
        condition = if_ast.expression.to_canonical()
        lines = [f".if {condition} {{"]
        lines.extend(self._indent_block_lines(self._format_ast(if_ast.block)))
        if if_ast.else_block:
            lines.append("} else {")
            lines.extend(self._indent_block_lines(self._format_ast(if_ast.else_block)))
        lines.append("}")
        return lines

    def _format_docstring(self, text: str, indent_level: int = 0) -> list[str]:
        """Emit a docstring with indent left untouched.

        The formatter only owns the `\"\"\"` markers (which sit at
        `indent_level`); the content between them is the author's
        verbatim text. Trailing whitespace per line is trimmed and
        blank wrapper lines are dropped, but no dedent / reindent ever
        runs — alignment with the target is enforced by `DOC007`, not
        rewritten silently.
        """
        indent = " " * (self.options.indent_size * indent_level)
        if "\n" not in text:
            return [f'{indent}"""{text}"""']

        lines = [line.rstrip() for line in text.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return [f'{indent}"""', *lines, f'{indent}"""']

    def _format_for(self, for_ast: ForAstNode) -> list[str]:
        """Format a for loop"""
        symbol = for_ast.symbol
        min_val = for_ast.min_value.to_canonical()
        max_val = for_ast.max_value.to_canonical()
        comma = ", " if self.options.space_after_comma else ","
        header = f".for {symbol} := {min_val}{comma}{max_val} {{"
        lines = [header]
        lines.extend(self._indent_block_lines(self._format_ast(for_ast.body)))
        lines.append("}")
        return lines

    def _indent(self, line: str, levels: int = 1) -> str:
        """Add indentation to a line"""
        if not line.strip():
            return line
        indent = " " * (self.options.indent_size * levels)
        return f"{indent}{line.lstrip()}"

    def _indent_block_lines(self, lines: list[str], levels: int = 1) -> list[str]:
        """Indent a sequence of lines representing a block.

        Three structural exceptions:
        - Inner labels (lines ending in `:` that aren't directives) stay
          flush-left so they read as section markers inside the routine —
          the same convention `_loop:` / `_not_found:` follow in
          idiomatic a816 code.
        - Stand-alone comments stay flush-left. Authors use them as
          section banners (`;------`) or as block dumps of disassembly /
          notes that should not visually merge with the indented body.
          Inline comments (folded onto an instruction) are unaffected
          because they're appended to the instruction line, not emitted
          as their own entry here.
        - Stand-alone docstrings (the `\"\"\"` markers and every line in
          between) pass through unchanged. They sit above a label, so the
          docstring needs to share that label's flush-left indentation
          and any relative indent the author baked into the content has
          to survive intact — which `_indent` would destroy by lstripping.
        """
        indented: list[str] = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                indented.append("")
                continue
            triple_count = stripped.count('"""')
            if in_docstring or triple_count:
                indented.append(line.rstrip())
                if triple_count % 2 == 1:
                    in_docstring = not in_docstring
                continue
            is_label = stripped.endswith(":") and not stripped.startswith(":") and not stripped.startswith(".")
            if is_label or stripped.startswith(";"):
                indented.append(stripped)
            else:
                indented.append(self._indent(line, levels))
        return indented

    @staticmethod
    def _node_line_num(node: AstNode) -> int | None:
        if hasattr(node, "file_info") and node.file_info and node.file_info.position:
            return node.file_info.position.line
        return None

    @staticmethod
    def _emit_preserved_blanks(
        original_lines: list[str], processed: set[int], current_idx: int, until: int, formatted: list[str]
    ) -> int:
        while current_idx < until:
            if current_idx not in processed:
                original_line = original_lines[current_idx] if current_idx < len(original_lines) else ""
                if not original_line.strip():
                    formatted.append("")
            processed.add(current_idx)
            current_idx += 1
        return current_idx

    def _emit_label(self, node_lines: list[str], formatted: list[str]) -> bool:
        if formatted and formatted[-1].strip():
            formatted.append("")
        formatted.extend(node_lines)
        return True

    def _emit_comment(
        self,
        node_lines: list[str],
        node_line: int | None,
        last_emitted_line_num: int | None,
        in_label_section: bool,
        formatted: list[str],
    ) -> None:
        comment_text = node_lines[0] if node_lines else ""
        on_same_line_as_prev = (
            last_emitted_line_num is not None
            and node_line is not None
            and node_line == last_emitted_line_num
            and formatted
            and formatted[-1].strip()
        )
        # A comment separated from the previous emitted line by a blank
        # line is acting as a leading comment for whatever follows
        # (typically the next label / function), not as the body of the
        # current label section. Don't indent it as body code.
        separated_by_blank = (
            last_emitted_line_num is not None and node_line is not None and node_line - last_emitted_line_num > 1
        )
        # If the previous emitted line is itself a flush-left comment,
        # this one is a continuation of the same paragraph and should
        # also stay flush-left — otherwise a leading-paragraph comment
        # ends up dedented while its continuation lines stay indented,
        # which reads as a misaligned block.
        prev_is_flush_comment = (
            bool(formatted) and formatted[-1].lstrip().startswith(";") and formatted[-1] == formatted[-1].lstrip()
        )
        if on_same_line_as_prev:
            formatted[-1] = formatted[-1].rstrip() + " " + comment_text.strip()
        elif prev_is_flush_comment:
            formatted.extend(node_lines)
        elif in_label_section and comment_text.strip() and not separated_by_blank:
            formatted.append(self._indent(comment_text))
        else:
            formatted.extend(node_lines)

    def _emit_instruction_like(
        self,
        node: AstNode,
        node_lines: list[str],
        in_label_section: bool,
        formatted: list[str],
        prev_was_label: bool = False,
    ) -> bool:
        if isinstance(node, CodePositionAstNode | CodeRelocationAstNode):
            if formatted and formatted[-1].strip():
                formatted.append("")
            formatted.extend(line.lstrip() for line in node_lines)
            return True
        # A docstring immediately after a label documents the symbol and
        # stays flush-left, hugging the label with no blank line in
        # between. After a `*=` (section opener) the docstring documents
        # the section and follows body indentation like any other body
        # statement — fall through to the normal indent path.
        if isinstance(node, DocstringAstNode) and prev_was_label:
            while formatted and not formatted[-1].strip():
                formatted.pop()
            formatted.extend(node_lines)
            return in_label_section
        if in_label_section:
            node_lines = [
                self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
            ]
        formatted.extend(node_lines)
        return in_label_section

    def _emit_compound(self, node: CompoundAstNode, formatted: list[str]) -> None:
        block_lines = self._format_ast(node, indent_after_label=False)
        formatted.append("{")
        formatted.extend(self._indent_block_lines(block_lines))
        formatted.append("}")

    def _node_positions_sorted(self, nodes: list[AstNode]) -> list[tuple[int, AstNode]]:
        positioned: list[tuple[int, AstNode]] = []
        for n in nodes:
            line = self._node_line_num(n)
            if line is not None:
                positioned.append((line, n))
        return sorted(positioned, key=lambda x: x[0])

    def _emit_non_compound(
        self,
        node: AstNode,
        node_line: int | None,
        last_line: int | None,
        in_label: bool,
        formatted: list[str],
        prev_was_label: bool = False,
    ) -> bool:
        node_lines = self._format_ast(node)
        if isinstance(node, LabelAstNode):
            return self._emit_label(node_lines, formatted)
        if isinstance(node, CommentAstNode):
            self._emit_comment(node_lines, node_line, last_line, in_label, formatted)
            return in_label
        if isinstance(node, self._instruction_like_nodes):
            return self._emit_instruction_like(node, node_lines, in_label, formatted, prev_was_label)
        formatted.extend(node_lines)
        return False

    _AST_CHILD_ATTRS: ClassVar[tuple[str, ...]] = ("body", "block", "else_block", "nodes", "items", "children")

    @staticmethod
    def _ast_children(node: AstNode) -> list[AstNode]:
        """Collect AstNode children reachable through the conventional
        container attributes used across the AST classes. Returns a
        flat list; non-AstNode values and missing attrs are skipped.
        """
        out: list[AstNode] = []
        for attr in A816Formatter._AST_CHILD_ATTRS:
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                out.extend(c for c in child if isinstance(c, AstNode))
            elif isinstance(child, AstNode):
                out.append(child)
        return out

    @staticmethod
    def _ast_max_line(node: AstNode) -> int | None:
        """Walk a subtree iteratively and return the largest source line
        number among leaf-like descendants. Skips CompoundAstNode
        positions because the scanner records their position at the
        closing brace, which would otherwise overshoot the actual extent
        of the block's content and swallow boundary blank lines.

        Returns None if no descendant carries usable position info.
        """
        max_line: int | None = None
        stack: list[AstNode] = [node]
        while stack:
            current = stack.pop()
            if not isinstance(current, CompoundAstNode):
                line = A816Formatter._node_line_num(current)
                if line is not None and (max_line is None or line > max_line):
                    max_line = line
            stack.extend(A816Formatter._ast_children(current))
        return max_line

    @dataclass
    class _PreservedBlankState:
        """Mutable state threaded through `_format_with_preserved_blanks`'s
        per-node helpers so the loop body itself stays linear.
        """

        current_idx: int = 0
        in_label_section: bool = False
        last_emitted_line_num: int | None = None
        prev_was_label: bool = False

    def _emit_one_top_level(
        self,
        node: AstNode,
        line_num: int,
        formatted: list[str],
        original_lines: list[str],
        processed: set[int],
        state: A816Formatter._PreservedBlankState,
    ) -> None:
        state.current_idx = self._emit_preserved_blanks(
            original_lines, processed, state.current_idx, line_num, formatted
        )
        node_line = self._node_line_num(node)

        if isinstance(node, CompoundAstNode):
            state.in_label_section = False
            self._emit_compound(node, formatted)
            state.last_emitted_line_num = None
            state.prev_was_label = False
        else:
            state.in_label_section = self._emit_non_compound(
                node,
                node_line,
                state.last_emitted_line_num,
                state.in_label_section,
                formatted,
                state.prev_was_label,
            )
            if node_line is not None:
                state.last_emitted_line_num = node_line
            state.prev_was_label = isinstance(node, LabelAstNode)

        processed.add(line_num)
        # Advance past lines the node already consumed (descendants) so
        # source blanks WITHIN the body aren't re-emitted at the
        # boundary. Source blanks AFTER the last child but BEFORE the
        # next sibling stay reachable.
        tail = self._ast_max_line(node)
        if tail is not None and tail >= line_num:
            processed.update(range(line_num, tail + 1))
            state.current_idx = max(state.current_idx, tail + 1)
        else:
            state.current_idx = max(state.current_idx, line_num + 1)

    def _format_with_preserved_blanks(self, content: str, nodes: list[AstNode]) -> str:
        """Format AST nodes preserving original blank lines."""
        original_lines = content.splitlines()
        formatted: list[str] = []
        processed: set[int] = set()
        state = A816Formatter._PreservedBlankState()

        for line_num, node in self._node_positions_sorted(nodes):
            self._emit_one_top_level(node, line_num, formatted, original_lines, processed, state)

        self._emit_preserved_blanks(original_lines, processed, state.current_idx, len(original_lines), formatted)
        return finalize_formatting(formatted, self.options)
