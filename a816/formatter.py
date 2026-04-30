from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from a816.cpu.cpu_65c816 import AddressingMode
from a816.exceptions import FormattingError
from a816.parse.ast.nodes import (
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
    MacroApplyAstNode,
    MacroAstNode,
    OpcodeAstNode,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    TextAstNode,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.mzparser import MZParser


class FormattingOptions:
    """Configuration for code formatting"""

    def __init__(
        self,
        *,
        indent_size: int = 4,
        opcode_indent: int | None = None,
        operand_alignment: int = 16,
        comment_alignment: int = 40,
        preserve_empty_lines: bool = True,
        max_empty_lines: int = 2,
        align_labels: bool = True,
        space_after_comma: bool = True,
    ):
        self.indent_size = indent_size
        self.opcode_indent = opcode_indent if opcode_indent is not None else indent_size
        self.operand_alignment = operand_alignment
        self.comment_alignment = comment_alignment
        self.preserve_empty_lines = preserve_empty_lines
        self.max_empty_lines = max_empty_lines
        self.align_labels = align_labels
        self.space_after_comma = space_after_comma


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
            SymbolAffectationAstNode,
            CodePositionAstNode,
            CodeRelocationAstNode,
            CodeLookupAstNode,
        )

    def format_text(self, content: str, file_path: str | None = None) -> str:
        """Format assembly code from text content"""
        source = file_path or "<input>"
        try:
            # Parse the content into an AST
            result = MZParser.parse_as_ast(content, source)

            if result.error:
                raise FormattingError(f"Unable to format {source}:\n{result.error}")

            # Preserve blank lines from original content
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

    def _format_compound(self, ast: CompoundAstNode, indent_instructions: bool, indent_after_label: bool) -> list[str]:
        lines: list[str] = []
        prev_was_label = False
        for node in ast.body:
            should_indent = indent_instructions or (indent_after_label and prev_was_label)
            node_lines = self._format_ast(node, should_indent, indent_after_label=indent_after_label)
            if should_indent and isinstance(node, self._instruction_like_nodes):
                node_lines = [
                    self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
                ]
            lines.extend(node_lines)
            prev_was_label = isinstance(node, LabelAstNode)
        return lines

    def _format_block(self, ast: BlockAstNode, indent_after_label: bool) -> list[str]:
        lines: list[str] = []
        for node in ast.body:
            lines.extend(self._format_ast(node, True, indent_after_label=indent_after_label))
        return lines

    def _format_comment(self, ast: CommentAstNode) -> list[str]:
        comment = ast.comment.strip()
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
        OpcodeAstNode: lambda self, ast: self._format_opcode(ast),
        TextAstNode: lambda self, ast: f'.text "{ast.text}"',
        AsciiAstNode: lambda self, ast: f'.ascii "{ast.text}"',
        DataNode: lambda self, ast: self._format_data(ast),
        MacroApplyAstNode: lambda self, ast: self._format_macro_apply(ast),
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

    def _format_opcode(self, opcode_ast: OpcodeAstNode) -> str:
        """Format an opcode instruction"""
        # Build opcode with size specifier (always lowercase)
        opcode = opcode_ast.opcode.lower()
        if opcode_ast.value_size:
            opcode += f".{opcode_ast.value_size.lower()}"

        # Add operand if present
        operand = self._format_operand(opcode_ast)

        # Format with single space between opcode and operand (no indentation here)
        if operand:
            return f"{opcode} {operand}"
        else:
            return f"{opcode}"

    def _format_operand(self, opcode_ast: OpcodeAstNode) -> str:
        """Format an opcode operand according to its addressing mode"""
        if not opcode_ast.operand:
            return ""

        operand = opcode_ast.operand.to_canonical().strip()
        addressing_mode = opcode_ast.addressing_mode
        index = opcode_ast.index.lower() if opcode_ast.index else None
        comma = ", " if self.options.space_after_comma else ","

        def with_index(base: str) -> str:
            return f"{base}{comma}{index}" if index else base

        if addressing_mode == AddressingMode.immediate:
            return operand if operand.startswith("#") else f"#{operand}"
        if addressing_mode == AddressingMode.indirect:
            return f"({operand})"
        if addressing_mode == AddressingMode.indirect_indexed:
            return with_index(f"({operand})")
        if addressing_mode == AddressingMode.indirect_long:
            return f"[{operand}]"
        if addressing_mode == AddressingMode.indirect_indexed_long:
            return with_index(f"[{operand}]")
        if addressing_mode == AddressingMode.dp_or_sr_indirect_indexed:
            inner = f"{operand}{comma}{index}" if index else operand
            return f"({inner})"
        if addressing_mode == AddressingMode.stack_indexed_indirect_indexed:
            return with_index(f"({operand}{comma}s)")
        return with_index(operand)

    def _format_data(self, data_ast: DataNode) -> str:
        """Format a data directive"""
        directive = f".{data_ast.kind}"
        values = []

        for expr in data_ast.data:
            values.append(expr.to_canonical())

        operand = ", ".join(values) if self.options.space_after_comma else ",".join(values)

        # Format with single space between directive and operand (no indentation here)
        return f"{directive} {operand}"

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

    def _format_macro_apply(self, apply_ast: MacroApplyAstNode) -> str:
        """Format a macro application"""
        if apply_ast.args:
            args = []
            for arg in apply_ast.args:
                args.append(arg.to_canonical())
            arg_str = ", ".join(args) if self.options.space_after_comma else ",".join(args)
            return f"{apply_ast.name}({arg_str})"
        else:
            return f"{apply_ast.name}()"

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
        indent = " " * (self.options.indent_size * indent_level)
        if "\n" not in text:
            return [f'{indent}"""{text}"""']

        formatted = [f'{indent}"""']
        for line in text.splitlines():
            formatted.append(f"{indent}{line}")
        formatted.append(f'{indent}"""')
        return formatted

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
        """Indent a sequence of lines representing a block"""
        indented: list[str] = []
        for line in lines:
            if not line.strip():
                indented.append("")
            else:
                indented.append(self._indent(line, levels))
        return indented

    def _collapse_empty_lines(self, lines: list[str]) -> list[str]:
        if not self.options.preserve_empty_lines:
            return [line for line in lines if line.strip()]
        result: list[str] = []
        empty_count = 0
        for line in lines:
            if line.strip():
                empty_count = 0
                result.append(line)
            else:
                empty_count += 1
                if empty_count <= self.options.max_empty_lines:
                    result.append("")
        return result

    @staticmethod
    def _separate_labels(lines: list[str]) -> list[str]:
        adjusted: list[str] = []
        for line in lines:
            if (
                line.strip().endswith(":")
                and not line.startswith(":")
                and not line.startswith(".")
                and adjusted
                and adjusted[-1].strip()
            ):
                adjusted.append("")
            adjusted.append(line)
        return adjusted

    @staticmethod
    def _collect_inline_comment_groups(lines: list[str]) -> dict[int, list[tuple[int, str, str]]]:
        groups: dict[int, list[tuple[int, str, str]]] = {}
        for index, line in enumerate(lines):
            if ";" not in line or line.lstrip().startswith(";"):
                continue
            semicolon_index = line.find(";")
            if semicolon_index <= 0:
                continue
            indent = len(line) - len(line.lstrip())
            code_part = line[indent:semicolon_index].rstrip()
            if not code_part:
                continue
            comment_part = line[semicolon_index + 1 :].strip()
            groups.setdefault(indent, []).append((index, code_part, comment_part))
        return groups

    def _align_inline_comments(self, lines: list[str]) -> None:
        groups = self._collect_inline_comment_groups(lines)
        for indent, entries in groups.items():
            if not entries:
                continue
            max_code_len = max(len(code_part) for _, code_part, _ in entries)
            target_column = max(indent + max_code_len + 1, self.options.comment_alignment)
            for index, code_part, comment_part in entries:
                padding = max(target_column - (indent + len(code_part)), 1)
                comment_text = f"; {comment_part}" if comment_part else ";"
                lines[index] = f"{' ' * indent}{code_part}{' ' * padding}{comment_text}"

    def _finalize_formatting(self, lines: list[str]) -> str:
        """Strip trailing whitespace, collapse blanks, separate labels, align inline comments."""
        lines = [line.rstrip() for line in lines]
        lines = self._collapse_empty_lines(lines)
        lines = self._separate_labels(lines)
        self._align_inline_comments(lines)
        content = "\n".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        return content

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
        if on_same_line_as_prev:
            formatted[-1] = formatted[-1].rstrip() + " " + comment_text.strip()
        elif in_label_section and comment_text.strip():
            formatted.append(self._indent(comment_text))
        else:
            formatted.extend(node_lines)

    def _emit_instruction_like(
        self, node: AstNode, node_lines: list[str], in_label_section: bool, formatted: list[str]
    ) -> bool:
        if isinstance(node, CodePositionAstNode | CodeRelocationAstNode):
            if formatted and formatted[-1].strip():
                formatted.append("")
            formatted.extend(line.lstrip() for line in node_lines)
            return True
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

    def _format_with_preserved_blanks(self, content: str, nodes: list[AstNode]) -> str:
        """Format AST nodes preserving original blank lines."""
        original_lines = content.splitlines()
        formatted: list[str] = []
        processed: set[int] = set()
        current_idx = 0
        in_label_section = False
        last_emitted_line_num: int | None = None

        node_positions = sorted(
            ((line, n) for n in nodes if (line := self._node_line_num(n)) is not None),
            key=lambda x: x[0],
        )

        for line_num, node in node_positions:
            current_idx = self._emit_preserved_blanks(original_lines, processed, current_idx, line_num, formatted)
            node_line = self._node_line_num(node)

            if isinstance(node, CompoundAstNode):
                in_label_section = False
                self._emit_compound(node, formatted)
                last_emitted_line_num = None
            else:
                node_lines = self._format_ast(node)
                if isinstance(node, LabelAstNode):
                    in_label_section = self._emit_label(node_lines, formatted)
                elif isinstance(node, CommentAstNode):
                    self._emit_comment(node_lines, node_line, last_emitted_line_num, in_label_section, formatted)
                elif isinstance(node, self._instruction_like_nodes):
                    in_label_section = self._emit_instruction_like(node, node_lines, in_label_section, formatted)
                else:
                    in_label_section = False
                    formatted.extend(node_lines)
                if node_line is not None:
                    last_emitted_line_num = node_line

            processed.add(line_num)
            current_idx = max(current_idx, line_num + 1)

        self._emit_preserved_blanks(original_lines, processed, current_idx, len(original_lines), formatted)
        return self._finalize_formatting(formatted)
