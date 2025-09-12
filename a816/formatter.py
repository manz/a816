"""
Assembly code formatter for a816
Uses the AST to provide consistent code formatting
"""

from pathlib import Path

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

    def format_text(self, content: str, file_path: str | None = None) -> str:
        """Format assembly code from text content"""
        try:
            # Parse the content into an AST
            result = MZParser.parse_as_ast(content, file_path or "<input>")

            if result.error:
                return content
                # If parsing fails, use fallback formatting
                return self._fallback_format(content)

            # Preserve blank lines from original content
            return self._format_with_preserved_blanks(content, result.nodes)

        except (ParserSyntaxError, Exception):
            # If parsing fails, return original content with minimal formatting
            return self._fallback_format(content)

    def format_file(self, file_path: str | Path) -> str:
        """Format assembly code from a file"""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.format_text(content, str(path))

    def _format_ast(self, ast: AstNode, indent_instructions: bool = False) -> list[str]:
        """Format an AST node into lines of text"""
        if isinstance(ast, CompoundAstNode):
            lines = []
            prev_was_label = False

            for node in ast.body:
                # Check if we should indent instructions (after labels)
                should_indent = indent_instructions or prev_was_label
                node_lines = self._format_ast(node, should_indent)

                # If this node is an instruction and should be indented, indent it
                if should_indent and isinstance(node, OpcodeAstNode | DataNode | TextAstNode | AsciiAstNode):
                    node_lines = [
                        self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
                    ]

                lines.extend(node_lines)
                prev_was_label = isinstance(node, LabelAstNode)

            return lines

        elif isinstance(ast, BlockAstNode):
            lines = []
            for node in ast.body:
                node_lines = self._format_ast(node, True)  # Always indent in blocks
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
            lines.extend(self._format_ast(ast.body, True))
            lines.append("}")
            return lines

        elif isinstance(ast, MacroAstNode):
            return [self._format_macro(ast)]

        elif isinstance(ast, MacroApplyAstNode):
            return [self._format_macro_apply(ast)]

        elif isinstance(ast, IfAstNode):
            return [self._format_if(ast)]

        elif isinstance(ast, ForAstNode):
            return [self._format_for(ast)]

        elif isinstance(ast, IncludeAstNode):
            return [f'.include "{ast.file_path}"']

        elif isinstance(ast, IncludeIpsAstNode):
            return [f'.include_ips "{ast.file_path}"']

        elif isinstance(ast, IncludeBinaryAstNode):
            return [f'.incbin "{ast.file_path}"']

        elif isinstance(ast, TableAstNode):
            return [f'.table "{ast.file_path}"']

        elif isinstance(ast, StructAstNode):
            return [self._format_struct(ast)]

        elif isinstance(ast, ExternAstNode):
            return [f"    .extern {ast.symbol}"]

        elif isinstance(ast, SymbolAffectationAstNode):
            value = ast.value.to_canonical() if ast.value else ""
            return [f"{ast.symbol} = {value}"]

        elif isinstance(ast, CodePositionAstNode):
            return [ast.to_canonical()]

        elif isinstance(ast, CodeRelocationAstNode):
            return [ast.to_canonical()]

        elif isinstance(ast, CodeLookupAstNode):
            return [f"    {{{{ {ast.symbol} }}}}"]

        else:
            # Fallback to canonical representation
            return [ast.to_canonical()]

    def _format_opcode(self, opcode_ast: OpcodeAstNode) -> str:
        """Format an opcode instruction"""
        # Build opcode with size specifier (always lowercase)
        opcode = opcode_ast.opcode.lower()
        if opcode_ast.value_size:
            opcode += f".{opcode_ast.value_size.lower()}"

        # Add operand if present
        operand = ""
        if opcode_ast.operand:
            operand = opcode_ast.operand.to_canonical()

            # Add index if present
            if opcode_ast.index:
                operand += f",{opcode_ast.index.lower()}"

            # Add space after comma if configured
            if self.options.space_after_comma and "," in operand:
                operand = operand.replace(",", ", ")

        # Format with single space between opcode and operand (no indentation here)
        if operand:
            return f"{opcode} {operand}"
        else:
            return f"{opcode}"

    def _format_data(self, data_ast: DataNode) -> str:
        """Format a data directive"""
        directive = f".{data_ast.kind}"
        values = []

        for expr in data_ast.data:
            values.append(expr.to_canonical())

        operand = ", ".join(values) if self.options.space_after_comma else ",".join(values)

        # Format with single space between directive and operand (no indentation here)
        return f"{directive} {operand}"

    def _format_macro(self, macro_ast: MacroAstNode) -> str:
        """Format a macro definition"""
        params = ", ".join(macro_ast.args) if self.options.space_after_comma else ",".join(macro_ast.args)
        result = f"macro {macro_ast.name}"
        if params:
            result += f"({params})"
        return result

    def _format_macro_apply(self, apply_ast: MacroApplyAstNode) -> str:
        """Format a macro application"""
        if apply_ast.args:
            args = []
            for arg in apply_ast.args:
                args.append(arg.to_canonical())
            arg_str = ", ".join(args) if self.options.space_after_comma else ",".join(args)
            return f"    {apply_ast.name}({arg_str})"
        else:
            return f"    {apply_ast.name}()"

    def _format_if(self, if_ast: IfAstNode) -> str:
        """Format an if statement"""
        condition = if_ast.expression.to_canonical()
        return f"if {condition}"

    def _format_for(self, for_ast: ForAstNode) -> str:
        """Format a for loop"""
        symbol = for_ast.symbol
        min_val = for_ast.min_value.to_canonical()
        max_val = for_ast.max_value.to_canonical()
        return f"for {symbol} {min_val} {max_val}"

    def _format_struct(self, struct_ast: StructAstNode) -> str:
        """Format a struct definition"""
        return f"struct {struct_ast.name}"

    def _indent(self, line: str, levels: int = 1) -> str:
        """Add indentation to a line"""
        if not line.strip():
            return line
        indent = " " * (self.options.indent_size * levels)
        return f"{indent}{line.lstrip()}"

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
            node_lines = self._format_ast(node)

            # Apply context-aware indentation
            if isinstance(node, LabelAstNode):
                in_label_section = True
                formatted_lines.extend(node_lines)
            elif isinstance(node, OpcodeAstNode | DataNode | TextAstNode | AsciiAstNode):
                if in_label_section:
                    node_lines = [
                        self._indent(line) if line.strip() and not line.startswith(" ") else line for line in node_lines
                    ]
                formatted_lines.extend(node_lines)
            else:
                if not isinstance(node, CommentAstNode):
                    in_label_section = False
                formatted_lines.extend(node_lines)

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

    def _fallback_format(self, content: str) -> str:
        """Fallback formatting when AST parsing fails"""
        lines = content.splitlines()
        formatted_lines = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # Preserve blank lines in fallback mode too
                if self.options.preserve_empty_lines:
                    formatted_lines.append("")
                continue

            # Basic indentation for opcodes and directives
            if stripped.startswith(";"):
                # Comment - keep as is
                formatted_lines.append(stripped)
            elif ":" in stripped and not stripped.startswith("."):
                # Label
                label_part = stripped.split(":")[0]
                rest = ":".join(stripped.split(":")[1:])
                if rest.strip():
                    formatted_lines.append(f"{label_part}:")
                    formatted_lines.append(f"    {rest.strip()}")
                else:
                    formatted_lines.append(f"{label_part}:")
            elif stripped.startswith("."):
                # Directive
                formatted_lines.append(f"    {stripped}")
            else:
                # Likely an opcode
                formatted_lines.append(f"    {stripped}")

        return self._finalize_formatting(formatted_lines)
