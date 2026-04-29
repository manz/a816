from pathlib import Path
from typing import Any, Protocol, cast

from a816.cpu.types import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import (
    AsciiAstNode,
    AssignAstNode,
    AstNode,
    BlockAstNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CommentAstNode,
    CompoundAstNode,
    DataNode,
    DebugAstNode,
    DocstringAstNode,
    ExpressionAstNode,
    ExternAstNode,
    FileInfoAstNode,
    ForAstNode,
    IfAstNode,
    ImportAstNode,
    IncludeAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    LabelAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapAstNode,
    OpcodeAstNode,
    RegisterSizeAstNode,
    ScopeAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    Term,
    TextAstNode,
)
from a816.parse.nodes import (
    AsciiNode,
    BinaryNode,
    ByteNode,
    CodePositionNode,
    DebugNode,
    ExpressionNode,
    ExternNode,
    IncludeIpsNode,
    LabelNode,
    LinkedModuleNode,
    LongNode,
    NodeError,
    OpcodeNode,
    PopScopeNode,
    RegisterSizeNode,
    RelocationAddressNode,
    ScopeNode,
    SymbolNode,
    TableNode,
    TextNode,
    WordNode,
)
from a816.parse.tokens import Token, TokenType
from a816.protocols import NodeProtocol
from a816.symbols import Resolver

MacroDefinitions = dict[str, Any]
GenNodes = list[NodeProtocol]


class CodeGenFuncProtocol(Protocol):
    def __call__(
        self,
        node: AstNode,
        resolver: Resolver,
        macro_definitions: MacroDefinitions,
        file_info: Token,
    ) -> GenNodes:
        """Protocol for codegen functions."""


def code_gen(ast_nodes: list[AstNode], resolver: Resolver) -> GenNodes:
    macro_definitions: MacroDefinitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)


def _get_file_info(node: AstNode) -> Token:
    return node.file_info


def generate_block(
    node: CompoundAstNode | BlockAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # Anonymous `{}` blocks scope their labels — don't leak names like
    # `loop`/`exit` into the parent scope.
    resolver.append_scope()
    resolver.use_next_scope()
    code: list[NodeProtocol] = [ScopeNode(resolver)]
    code += _code_gen(node.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope(exports=False)
    return code


def generate_scope(
    node: ScopeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    name = node.name
    resolver.append_named_scope(name)
    resolver.use_next_scope()
    code: list[NodeProtocol] = [ScopeNode(resolver)]

    code += _code_gen(node.body.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope(exports=False)
    return code


def generate_map(
    node: MapAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    attributes = node.args

    resolver.bus.map(
        str(attributes["identifier"]),
        attributes["bank_range"],
        attributes["addr_range"],
        attributes["mask"],
        writeable=attributes.get("writable", False),
        mirror_bank_range=attributes.get("mirror_bank_range"),
    )
    return []


def generate_opcode(
    node: OpcodeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: list[NodeProtocol] = []
    size = None

    if isinstance(node.operand, BlockAstNode):
        raise NodeError("Opcode operand must not be code", file_info)

    size = node.value_size
    opcode = node.opcode
    mode = node.addressing_mode
    if mode == AddressingMode.none:
        code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info, resolver=resolver))
    else:
        operand = node.operand
        assert operand is not None

        if mode in (
            AddressingMode.direct_indexed,
            AddressingMode.indirect_indexed,
            AddressingMode.indirect_indexed_long,
            AddressingMode.dp_or_sr_indirect_indexed,
            AddressingMode.stack_indexed_indirect_indexed,
        ):
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    index=node.index,
                    file_info=file_info,
                    resolver=resolver,
                )
            )
        else:
            code.append(
                OpcodeNode(
                    opcode,
                    addressing_mode=mode,
                    size=size,
                    value_node=ExpressionNode(operand, resolver, file_info),
                    file_info=file_info,
                    resolver=resolver,
                )
            )

    return code


def generate_include_ips(
    node: IncludeIpsAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [IncludeIpsNode(node.file_path, resolver, node.expression)]


def generate_include(
    node: IncludeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Inline the AST captured from an .include directive while honouring original scoping."""
    code: GenNodes = []
    if node.included_nodes:
        code.extend(_code_gen(node.included_nodes, resolver, macro_definitions))
    return code


def generate_docstring(
    node: DocstringAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Docstrings are metadata-only and do not emit code."""
    return []


def generate_incbin(
    node: IncludeBinaryAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [BinaryNode(node.file_path, resolver)]


def _generate_data(
    node: DataNode,
    node_type: type[ByteNode] | type[WordNode] | type[LongNode],
    resolver: Resolver,
    file_info: Token,
) -> GenNodes:
    """Generate data nodes for .db, .dw, or .dl directives."""
    code: GenNodes = []
    for expr in node.data:
        assert isinstance(expr, ExpressionAstNode)
        code.append(node_type(ExpressionNode(expr, resolver, file_info)))
    return code


def generate_dl(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, LongNode, resolver, file_info)


def generate_dw(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, WordNode, resolver, file_info)


def generate_db(
    node: DataNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return _generate_data(node, ByteNode, resolver, file_info)


def generate_symbol(
    node: SymbolAffectationAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # If the RHS references a symbol already known to be external, register an
    # alias eagerly so subsequent code-gen sees the LHS as external too. This
    # only triggers when an extern is reachable at this point (forward refs to
    # locally defined symbols still go through the lazy SymbolNode.pc_after path).
    if isinstance(node.value, ExpressionAstNode) and resolver.context.is_object_mode:
        from a816.parse.tokens import TokenType

        references_extern = any(
            term.token.type == TokenType.IDENTIFIER
            and resolver.current_scope.is_external_symbol(term.token.value)
            for term in node.value.tokens
        )
        if references_extern:
            from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference

            try:
                eval_expression(node.value, resolver)
            except (ExternalExpressionReference, ExternalSymbolReference) as e:
                expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
                resolver.current_scope.add_external_alias(node.symbol, expr_str)
                object_writer = resolver.context.object_writer
                if object_writer is not None:
                    object_writer.add_alias(node.symbol, expr_str)

    return [SymbolNode(node.symbol, node.value, resolver)]


def generate_extern(
    node: ExternAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # In object mode, register the extern eagerly so subsequent code-gen
    # (e.g. `font_ptr = extern_sym + N`) sees it as external. In direct mode
    # we leave it to ExternNode.pc_after to avoid shadowing real definitions
    # provided by included files.
    if resolver.context.is_object_mode:
        resolver.current_scope.add_external_symbol(node.symbol)
    return [ExternNode(node.symbol, resolver)]


def generate_import(
    node: ImportAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Generate nodes for all public symbols in an imported module.

    Module resolution order:
    1. Try .o file (compiled object) - extracts GLOBAL symbols and code
    2. Fall back to .s file (source) - extracts public labels (non-dot-prefixed)

    When a .o file is found:
    - If we're compiling to an object file (resolver has _object_writer), create ExternNode
    - Otherwise (direct assembly), create LinkedModuleNode that emits code and binds symbols

    The module is searched in:
    1. Directory of the current file
    2. Module search paths (if configured in resolver)
    """
    from a816.object_file import ObjectFile, SymbolType

    module_name = node.module_name
    code: GenNodes = []

    # Get the base directory from the current file
    base_dir = None
    if file_info.position and file_info.position.file:
        from a816.util import uri_to_path

        base_dir = uri_to_path(file_info.position.file.filename).parent

    # Build search paths
    search_paths: list[Path] = []
    if base_dir:
        search_paths.append(base_dir)
    search_paths.extend(resolver.context.module_paths)

    # Check compilation mode:
    # - Object file compilation: generate ExternNode (external references)
    # - Direct assembly mode: generate LinkedModuleNode (include code and bind symbols)
    # - Other (parsing only, tests): generate ExternNode
    compiling_to_object = resolver.context.is_object_mode
    direct_assembly_mode = resolver.context.is_direct_mode

    # Try to find .o file first
    obj_path = _resolve_module_path(module_name, ".o", search_paths)
    if obj_path:
        try:
            obj_file = ObjectFile.read(str(obj_path))

            if direct_assembly_mode and not compiling_to_object:
                # Direct assembly - create LinkedModuleNode that emits code and binds symbols
                # Convert symbols to format expected by LinkedModuleNode
                symbols_data = [
                    (name, address, sym_type.value, section.value)
                    for name, address, sym_type, section in obj_file.symbols
                ]
                # Pass expression relocations so they can be applied at emit time
                expr_relocs = list(obj_file.expression_relocations) if obj_file.expression_relocations else []
                code.append(LinkedModuleNode(module_name, obj_file.code, symbols_data, resolver, expr_relocs))
            else:
                # Object file compilation or parsing - mark symbols as external references
                for name, _address, sym_type, _section in obj_file.symbols:
                    if sym_type == SymbolType.GLOBAL:
                        code.append(ExternNode(name, resolver))

            return code
        except (FileNotFoundError, ValueError):
            pass  # Fall through to source file resolution

    # Fall back to .s file
    src_path = _resolve_module_path(module_name, ".s", search_paths)
    if src_path:
        try:
            if direct_assembly_mode and not compiling_to_object:
                # Direct assembly without .o file - parse and include the source file
                # This is similar to .include but triggered by .import
                from a816.parse.mzparser import MZParser

                content = src_path.read_text(encoding="utf-8")
                result = MZParser.parse_as_ast(content, str(src_path))
                if result.nodes:
                    code.extend(_code_gen(result.nodes, resolver, macro_definitions))
                return code
            else:
                # Object file compilation or parsing - extract symbols as external references
                symbols = _extract_public_symbols_from_source(src_path)
                for symbol_name in symbols:
                    code.append(ExternNode(symbol_name, resolver))
                return code
        except (FileNotFoundError, OSError):
            pass

    raise NodeError(f'Module not found: "{module_name}"', file_info)


def _resolve_module_path(module_name: str, extension: str, search_paths: list[Path]) -> Path | None:
    """Resolve a module name to a file path.

    Args:
        module_name: The module name (e.g., "vwf" or "battle/sram")
        extension: File extension to try (e.g., ".o" or ".s")
        search_paths: List of directories to search

    Returns:
        Path to the module file if found, None otherwise
    """
    # Module name can contain path separators (e.g., "battle/sram")
    module_file = module_name + extension

    for search_path in search_paths:
        candidate = search_path / module_file
        if candidate.exists():
            return candidate

    return None


def _extract_public_symbols_from_source(source_path: Path) -> list[str]:
    """Extract public symbols from a source file using the AST parser.

    Public symbols are:
    - Labels that don't start with a dot (.)
    - Symbol assignments that don't start with a dot

    Uses the full parser to correctly handle comments, strings, conditionals, etc.
    """
    from a816.parse.mzparser import MZParser

    symbols: list[str] = []
    content = source_path.read_text(encoding="utf-8")

    # Parse using the actual parser
    result = MZParser.parse_as_ast(content, str(source_path))

    # Extract symbols from AST nodes
    _collect_public_symbols(result.nodes, symbols)

    return symbols


def _collect_public_symbols(nodes: list[AstNode], symbols: list[str]) -> None:
    """Recursively collect public symbols from AST nodes.

    Public symbols don't start with underscore (_); underscored symbols are
    treated as module-private.
    """
    from a816.parse.ast.visitor import walk

    for node in walk(nodes):
        name: str | None = None
        if isinstance(node, LabelAstNode):
            name = node.label
        elif isinstance(node, SymbolAffectationAstNode | AssignAstNode):
            name = node.symbol

        if name is not None and not name.startswith("_") and name not in symbols:
            symbols.append(name)


def generate_register_size(
    node: RegisterSizeAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [RegisterSizeNode(node.register, node.size, resolver)]


def generate_assign(
    node: AssignAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference

    try:
        value = eval_expression(node.value, resolver)
        resolver.current_scope.add_symbol(node.symbol, value)
    except (ExternalExpressionReference, ExternalSymbolReference) as e:
        if not resolver.context.is_object_mode:
            raise NodeError(
                f"{node.symbol} = {node.value.to_canonical()}: "
                f"external symbols only allowed in object compilation mode.",
                file_info,
            ) from e
        expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
        resolver.current_scope.add_external_alias(node.symbol, expr_str)
        object_writer = resolver.context.object_writer
        if object_writer is not None:
            object_writer.add_alias(node.symbol, expr_str)

    return []


def generate_label(
    node: LabelAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [LabelNode(node.label, resolver)]


def generate_text(
    node: TextAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [TextNode(node.text, resolver, file_info)]


def generate_ascii(
    node: AsciiAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [AsciiNode(node.text, resolver)]


def generate_table(
    node: TableAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [TableNode(node.file_path, resolver)]


def generate_at_eq(
    node: CodeRelocationAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [RelocationAddressNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_star_eq(
    node: CodePositionAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    return [CodePositionNode(ExpressionNode(node.expression, resolver, file_info), resolver)]


def generate_for(
    node: ForAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    from_val = cast(int, eval_expression(node.min_value, resolver))
    to_val = cast(int, eval_expression(node.max_value, resolver))
    for k in range(from_val, to_val):
        resolver.append_internal_scope()
        resolver.use_next_scope()
        code.append(ScopeNode(resolver))
        code.append(
            SymbolNode(
                node.symbol,
                ExpressionAstNode([Term(Token(TokenType.NUMBER, str(k)))]),
                resolver,
            )
        )
        code += _code_gen(node.body.body, resolver, macro_definitions)
        code.append(PopScopeNode(resolver))
        resolver.restore_scope()
    return code


def generate_if(
    node: IfAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code = []
    if_branch_true = node.block
    if_branch_false = node.else_block

    try:
        condition = eval_expression(node.expression, resolver)
    except (KeyError, SymbolNotDefined):
        # Symbol not yet defined - this can happen with forward label references
        # like `.if END_OF_FREE_SPACE > 0x1ffff`. Labels are resolved in a later
        # pass, so we treat unresolved symbols as false during code generation.
        condition = False
    if condition:
        code += _code_gen(if_branch_true.body, resolver, macro_definitions)
    elif if_branch_false:
        code += _code_gen(if_branch_false.body, resolver, macro_definitions)
    return code


def generate_code_lookup(
    node: CodeLookupAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    value = resolver.current_scope.value_for(node.symbol)

    if isinstance(value, BlockAstNode):
        return _code_gen(value.body, resolver, macro_definitions)
    else:
        raise NodeError(f"{node.symbol} is not a code block ({value})", file_info)


def generate_macro_application(
    node: MacroApplyAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    macro_def: MacroAstNode = macro_definitions[node.name]
    macro_code = macro_def.block
    macro_args = macro_def.args
    macro_args_values = node.args

    if len(macro_args_values) != len(macro_args):
        raise NodeError(
            f"Macro '{node.name}' expects {len(macro_args)} argument(s), got {len(macro_args_values)}", file_info
        )

    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference

    for index, arg in enumerate(macro_args):
        value = macro_args_values[index]
        try:
            if isinstance(value, BlockAstNode):
                resolver.current_scope.add_symbol(arg, value)
            else:
                resolver.current_scope.add_symbol(arg, eval_expression(value, resolver))
        except SymbolNotDefined:
            # Defer the resolve to the emit part.
            code.append(SymbolNode(arg, value, resolver))
        except (ExternalExpressionReference, ExternalSymbolReference) as e:
            # Macro argument expression references externs; treat the bound
            # name as an alias locally. Do NOT publish to the object writer:
            # the binding is invocation-local, and any extern relocations
            # generated inside the macro body inline the alias on the way out.
            expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
            resolver.current_scope.add_external_alias(arg, expr_str)
    code += _code_gen(macro_code.body, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


def generate_macro(
    node: MacroAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    macro_definitions[node.name] = node
    return []


def generate_compound(
    node: CompoundAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    code: GenNodes = []
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    code += generate_block(node, resolver, macro_definitions, file_info)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


def generate_comment(
    node: CommentAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: FileInfoAstNode
) -> list[NodeProtocol]:
    # Comments don't generate executable code, so return empty list
    return []


def generate_debug(
    node: DebugAstNode, resolver: Resolver, macro_definitions: MacroDefinitions, file_info: FileInfoAstNode
) -> list[NodeProtocol]:
    return [DebugNode(node.message, resolver)]


generators = {
    "block": generate_block,
    "scope": generate_scope,
    "map": generate_map,
    "compound": generate_compound,
    "macro": generate_macro,
    "macro_apply": generate_macro_application,
    "code_lookup": generate_code_lookup,
    "if": generate_if,
    "for": generate_for,
    "at_eq": generate_at_eq,
    "star_eq": generate_star_eq,
    "table": generate_table,
    "text": generate_text,
    "ascii": generate_ascii,
    "db": generate_db,
    "dw": generate_dw,
    "dl": generate_dl,
    "pointer": generate_dl,
    "symbol": generate_symbol,
    "extern": generate_extern,
    "import": generate_import,
    "assign": generate_assign,
    "label": generate_label,
    "opcode": generate_opcode,
    "incbin": generate_incbin,
    "docstring": generate_docstring,
    "include": generate_include,
    "include_ips": generate_include_ips,
    "comment": generate_comment,
    "debug": generate_debug,
    "register_size": generate_register_size,
}


def _code_gen(ast_nodes: list[AstNode], resolver: Resolver, macro_definitions: MacroDefinitions) -> list[NodeProtocol]:
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        generator = generators.get(node.kind)
        if generator:
            code += generator(node, resolver, macro_definitions, file_info)  # type:ignore
        else:
            raise RuntimeError("Left over node", node)

    return code
