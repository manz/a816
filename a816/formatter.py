from pathlib import Path

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
        except Exception as exc:
            raise FormattingError(f"Unexpected formatter failure for {source}: {exc}") from exc

    def format_file(self, file_path: str | Path) -> str:
        """Format assembly code from a file"""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.format_text(content, str(path))

    def _format_ast(
        self, ast: AstNode, indent_instructions: bool = False, *, indent_after_label: bool = True
    ) -> list[str]:
        """Format an AST node into lines of text"""
        if isinstance(ast, CompoundAstNode):
            lines = []
            prev_was_label = False

            for node in ast.body:
                # Check if we should indent instructions (after labels)
                should_indent = indent_instructions or (indent_after_label and prev_was_label)
                node_lines = self._format_ast(node, should_indent, indent_after_label=indent_after_label)

                # If this node is an instruction and should be indented, indent it
                if should_indent and isinstance(node, self._instruction_like_nodes):
                    node_lines = [
                        self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
                    ]

                lines.extend(node_lines)
                prev_was_label = isinstance(node, LabelAstNode)

            return lines

        elif isinstance(ast, BlockAstNode):
            lines = []
            for node in ast.body:
                node_lines = self._format_ast(node, True, indent_after_label=indent_after_label)  # Always indent
                lines.extend(node_lines)
            return lines

        elif isinstance(ast, LabelAstNode):
            return [ast.to_canonical()]

        elif isinstance(ast, CommentAstNode):
            comment = ast.comment.strip()
            if not comment.startswith(";"):
                comment = f"; {comment}"
            return [comment]

        elif isinstance(ast, OpcodeAstNode):
            return [self._format_opcode(ast)]

        elif isinstance(ast, TextAstNode):
            return [f'.text "{ast.text}"']

        elif isinstance(ast, AsciiAstNode):
            return [f'.ascii "{ast.text}"']

        elif isinstance(ast, DataNode):
            return [self._format_data(ast)]

        elif isinstance(ast, ScopeAstNode):
            lines = [f".scope {ast.name} {{"]
            if ast.docstring:
                lines.extend(self._format_docstring(ast.docstring, indent_level=1))
            lines.extend(self._indent_block_lines(self._format_ast(ast.body, True)))
            lines.append("}")
            return lines

        elif isinstance(ast, MacroAstNode):
            return self._format_macro(ast)

        elif isinstance(ast, MacroApplyAstNode):
            return [self._format_macro_apply(ast)]

        elif isinstance(ast, DocstringAstNode):
            return self._format_docstring(ast.text)

        elif isinstance(ast, IfAstNode):
            return self._format_if(ast)

        elif isinstance(ast, ForAstNode):
            return self._format_for(ast)

        elif isinstance(ast, IncludeAstNode):
            return [f'.include "{ast.file_path}"']

        elif isinstance(ast, IncludeIpsAstNode):
            return [f'.include_ips "{ast.file_path}"']

        elif isinstance(ast, IncludeBinaryAstNode):
            return [f'.incbin "{ast.file_path}"']

        elif isinstance(ast, TableAstNode):
            return [f'.table "{ast.file_path}"']

        elif isinstance(ast, StructAstNode):
            return ast.to_canonical().splitlines()

        elif isinstance(ast, ExternAstNode):
            return [f".extern {ast.symbol}"]

        elif isinstance(ast, SymbolAffectationAstNode):
            value = ast.value.to_canonical() if ast.value else ""
            return [f"{ast.symbol} = {value}"]

        elif isinstance(ast, CodePositionAstNode):
            return [ast.to_canonical()]

        elif isinstance(ast, CodeRelocationAstNode):
            return [ast.to_canonical()]

        elif isinstance(ast, CodeLookupAstNode):
            return [f"{{{{ {ast.symbol} }}}}"]

        else:
            # Fallback to canonical representation; split to preserve structure
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

        if addressing_mode == AddressingMode.immediate:
            return operand if operand.startswith("#") else f"#{operand}"

        if addressing_mode == AddressingMode.indirect:
            return f"({operand})"

        if addressing_mode == AddressingMode.indirect_indexed:
            base = f"({operand})"
            return f"{base}{comma}{index}" if index else base

        if addressing_mode == AddressingMode.indirect_long:
            return f"[{operand}]"

        if addressing_mode == AddressingMode.indirect_indexed_long:
            base = f"[{operand}]"
            return f"{base}{comma}{index}" if index else base

        if addressing_mode == AddressingMode.dp_or_sr_indirect_indexed:
            inner = f"{operand}{comma}{index}" if index else operand
            return f"({inner})"

        if addressing_mode == AddressingMode.stack_indexed_indirect_indexed:
            inner = f"{operand}{comma}s"
            base = f"({inner})"
            return f"{base}{comma}{index}" if index else base

        if index:
            return f"{operand}{comma}{index}"

        return operand

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

    def _finalize_formatting(self, lines: list[str]) -> str:
        """Finalize formatting by cleaning up whitespace"""
        # Remove trailing whitespace
        lines = [line.rstrip() for line in lines]

        # Handle empty lines
        if self.options.preserve_empty_lines:
            # Limit consecutive empty lines
            result_lines = []
            empty_count = 0

            for line in lines:
                if not line.strip():
                    empty_count += 1
                    if empty_count <= self.options.max_empty_lines:
                        result_lines.append("")
                else:
                    empty_count = 0
                    result_lines.append(line)

            lines = result_lines
        else:
            # Remove all empty lines
            lines = [line for line in lines if line.strip()]

        # Ensure labels are separated by a blank line
        adjusted: list[str] = []
        for line in lines:
            if line.strip().endswith(":") and (not line.startswith(".")):
                if adjusted and adjusted[-1].strip():
                    adjusted.append("")
            adjusted.append(line)
        lines = adjusted

        # Align inline comments within blocks sharing the same indentation
        inline_groups: dict[int, list[tuple[int, str, str]]] = {}
        for index, line in enumerate(lines):
            if ";" not in line:
                continue
            stripped = line.lstrip()
            if stripped.startswith(";"):
                continue
            semicolon_index = line.find(";")
            if semicolon_index <= 0:
                continue
            indent = len(line) - len(line.lstrip())
            code_part = line[indent:semicolon_index].rstrip()
            if not code_part:
                continue
            comment_part = line[semicolon_index + 1 :].strip()
            inline_groups.setdefault(indent, []).append((index, code_part, comment_part))

        for indent, entries in inline_groups.items():
            if not entries:
                continue
            max_code_len = max(len(code_part) for _, code_part, _ in entries)
            target_column = max(indent + max_code_len + 1, self.options.comment_alignment)
            for index, code_part, comment_part in entries:
                padding = target_column - (indent + len(code_part))
                if padding < 1:
                    padding = 1
                comment_text = f"; {comment_part}" if comment_part else ";"
                lines[index] = f"{' ' * indent}{code_part}{' ' * padding}{comment_text}"

        # Join with newlines and ensure single trailing newline
        content = "\n".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"

        return content

    def _format_with_preserved_blanks(self, content: str, nodes: list[AstNode]) -> str:
        """Format AST nodes while preserving blank lines from original content"""
        original_lines = content.splitlines()
        formatted_lines = []
        current_line_idx = 0

        # Track which lines have been processed
        processed_lines = set()

        # Create a mapping of AST nodes to their line positions
        node_positions = []
        for node in nodes:
            if hasattr(node, "file_info") and node.file_info and node.file_info.position:
                line_num = node.file_info.position.line
                node_positions.append((line_num, node))

        # Sort by line number
        node_positions.sort(key=lambda x: x[0])

        # Process each node while preserving blank lines
        in_label_section = False
        last_emitted_line_num: int | None = None

        for line_num, node in node_positions:
            # Add any blank lines before this node
            while current_line_idx < line_num:
                if current_line_idx not in processed_lines:
                    original_line = original_lines[current_line_idx] if current_line_idx < len(original_lines) else ""
                    if not original_line.strip():
                        # Preserve blank line
                        formatted_lines.append("")
                processed_lines.add(current_line_idx)
                current_line_idx += 1

            # Format the current node
            if isinstance(node, CompoundAstNode):
                in_label_section = False
                block_lines = self._format_ast(node, indent_after_label=False)
                formatted_lines.append("{")
                formatted_lines.extend(self._indent_block_lines(block_lines))
                formatted_lines.append("}")
                last_emitted_line_num = None
            else:
                node_lines = self._format_ast(node)
                node_line = None
                if hasattr(node, "file_info") and node.file_info and node.file_info.position:
                    node_line = node.file_info.position.line

                # Apply context-aware indentation
                if isinstance(node, LabelAstNode):
                    if formatted_lines and formatted_lines[-1].strip():
                        formatted_lines.append("")
                    in_label_section = True
                    formatted_lines.extend(node_lines)
                    last_emitted_line_num = node_line
                elif isinstance(node, CommentAstNode):
                    comment_text = node_lines[0] if node_lines else ""
                    if (
                        last_emitted_line_num is not None
                        and node_line is not None
                        and node_line == last_emitted_line_num
                        and formatted_lines
                        and formatted_lines[-1].strip()
                    ):
                        formatted_lines[-1] = formatted_lines[-1].rstrip() + " " + comment_text.strip()
                    else:
                        if in_label_section and comment_text.strip():
                            formatted_lines.append(self._indent(comment_text))
                        else:
                            formatted_lines.extend(node_lines)
                    if node_line is not None:
                        last_emitted_line_num = node_line
                elif isinstance(node, self._instruction_like_nodes):
                    if isinstance(node, (CodePositionAstNode, CodeRelocationAstNode)):
                        if formatted_lines and formatted_lines[-1].strip():
                            formatted_lines.append("")
                        formatted_lines.extend([line.lstrip() for line in node_lines])
                        in_label_section = True
                        if node_line is not None:
                            last_emitted_line_num = node_line
                    else:
                        if in_label_section:
                            node_lines = [
                                self._indent(line) if line.strip() and not line.startswith(" ") else line
                                for line in node_lines
                            ]
                        formatted_lines.extend(node_lines)
                        if node_line is not None:
                            last_emitted_line_num = node_line
                else:
                    if not isinstance(node, CommentAstNode):
                        in_label_section = False
                    formatted_lines.extend(node_lines)
                    if node_line is not None:
                        last_emitted_line_num = node_line

            processed_lines.add(line_num)
            current_line_idx = max(current_line_idx, line_num + 1)

        # Add any remaining blank lines at the end
        while current_line_idx < len(original_lines):
            if current_line_idx not in processed_lines:
                original_line = original_lines[current_line_idx]
                if not original_line.strip():
                    formatted_lines.append("")
            current_line_idx += 1

        return self._finalize_formatting(formatted_lines)
