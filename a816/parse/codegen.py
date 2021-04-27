from a816.cpu.cpu_65c816 import AddressingMode
from a816.exceptions import SymbolNotDefined
from a816.expressions import eval_expr
from a816.parse.nodes import ScopeNode, PopScopeNode, SymbolNode, CodePositionNode, ExpressionNode, \
    RelocationAddressNode, TableNode, TextNode, LabelNode, ByteNode, WordNode, LongNode, BinaryNode, IncludeIpsNode, \
    OpcodeNode, AsciiNode


def code_gen(ast_nodes, resolver):
    macro_definitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)


def _get_file_info(node):
    last_node_item = node[-1]
    if isinstance(last_node_item, tuple) and last_node_item[0] == 'fileinfo':
        return last_node_item[1:]
    return None


def generate_block(node, resolver, macro_definitions, file_info) -> list:
    return _code_gen(node[1], resolver, macro_definitions)


def generate_scope(node, resolver, macro_definitions, file_info) -> list:
    name = node[1]
    resolver.append_named_scope(name)
    resolver.use_next_scope()
    code = [ScopeNode(resolver)]

    code += _code_gen(node[2], resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope(exports=False)
    return code


def generate_map(node, resolver, macro_definitions, file_info) -> list:
    attributes = node[1]
    # def map(self, identifier, bank_range, address_range, mask, writeable=False, mirror_bank_range=None):
    resolver.bus.map(
        attributes['identifier'],
        attributes['bank_range'],
        attributes['addr_range'],
        attributes['mask'],
        writeable=attributes.get('writable', False),
        mirror_bank_range=attributes.get('mirror_bank_range'))
    return []


def generate_opcode(node, resolver, macro_definitions, file_info) -> list:
    code = []
    opcode = node[2]
    size = None
    if isinstance(opcode, list) or isinstance(opcode, tuple):
        size = opcode[1]
        opcode = opcode[0]
    mode = node[1]
    if mode == AddressingMode.none:
        code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info))
    elif mode in (
            AddressingMode.direct_indexed, AddressingMode.indirect_indexed,
            AddressingMode.indirect_indexed_long, AddressingMode.dp_or_sr_indirect_indexed,
            AddressingMode.stack_indexed_indirect_indexed):
        code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                               value_node=ExpressionNode(node[3], resolver),
                               index=node[4], file_info=file_info))
    else:
        code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                               value_node=ExpressionNode(node[3], resolver),
                               file_info=file_info))

    return code


def generate_include_ips(node, resolver, macro_definitions, file_info) -> list:
    return [IncludeIpsNode(node[1], resolver, node[2])]


def generate_incbin(node, resolver, macro_definitions, file_info) -> list:
    return [BinaryNode(node[1], resolver)]


def generate_dl(node, resolver, macro_definitions, file_info) -> list:
    code = []
    for expr in node[1]:
        code.append(LongNode(ExpressionNode(expr, resolver)))
    return code


def generate_dw(node, resolver, macro_definitions, file_info) -> list:
    code = []
    for expr in node[1]:
        code.append(WordNode(ExpressionNode(expr, resolver)))
    return code


def generate_db(node, resolver, macro_definitions, file_info) -> list:
    code = []
    for expr in node[1]:
        code.append(ByteNode(ExpressionNode(expr, resolver)))
    return code


def generate_symbol(node, resolver, macro_definitions, file_info) -> list:
    return [SymbolNode(node[1], node[2], resolver)]


def generate_label(node, resolver, macro_definitions, file_info) -> list:
    return [LabelNode(node[1], resolver)]


def generate_text(node, resolver, macro_definitions, file_info) -> list:
    return [TextNode(node[1], resolver)]


def generate_ascii(node, resolver, macro_definitions, file_info) -> list:
    return [AsciiNode(node[1], resolver)]


def generate_table(node, resolver, macro_definitions, file_info) ->  list:
    return [TableNode(node[1], resolver)]


def generate_at_eq(node, resolver, macro_definitions, file_info) -> list:
    return [RelocationAddressNode(ExpressionNode(node[1], resolver), resolver)]


def generate_star_eq(node, resolver, macro_definitions, file_info) -> list:
    return [CodePositionNode(ExpressionNode(node[1], resolver), resolver)]


def generate_for(node, resolver, macro_definitions, file_info) -> list:
    code = []
    symbol, from_raw_val, to_raw_val, code_block = node[1:]
    from_val = eval_expr(from_raw_val, resolver)
    to_val = eval_expr(to_raw_val, resolver)
    for k in range(from_val, to_val):
        resolver.append_internal_scope()
        resolver.use_next_scope()
        code.append(ScopeNode(resolver))
        code.append(SymbolNode(symbol, str(k), resolver))
        code += _code_gen(code_block[1], resolver, macro_definitions)
        code.append(PopScopeNode(resolver))
        resolver.restore_scope()
    return code


def generate_if(node, resolver, macro_definitions, file_info) -> list:
    code = []
    symbol = node[1]
    if_branch_true = node[2]
    try:
        if_branch_false = node[3]
    except IndexError:
        if_branch_false = None
    try:
        condition = resolver.current_scope.value_for(symbol)
    except (KeyError, SymbolNotDefined):
        condition = False
    if condition:
        code += _code_gen(if_branch_true[1], resolver, macro_definitions)
    elif if_branch_false:
        code += _code_gen(if_branch_false[1], resolver, macro_definitions)
    return code


def generate_code_lookup(node, resolver, macro_definitions, file_info) -> list:
    return _code_gen([resolver.current_scope.value_for(node[1])], resolver, macro_definitions)


def generate_macro_application(node, resolver, macro_definitions, file_info) -> list:
    code = []
    macro_def = macro_definitions[node[1]]
    macro_code = [macro_def[1]]
    macro_args = macro_def[0][1]
    macro_args_values = node[2][1:][0]
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    for index, arg in enumerate(macro_args):
        value = macro_args_values[index]
        try:
            if isinstance(value, tuple):
                resolver.current_scope.add_symbol(arg, value)
            else:
                resolver.current_scope.add_symbol(arg, eval_expr(value, resolver))
        except SymbolNotDefined:
            # defer the resolve to the emit part.
            code.append(SymbolNode(arg, value, resolver))
    code += _code_gen(macro_code, resolver, macro_definitions)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


def generate_macro(node, resolver, macro_definitions, file_info) -> list:
    macro_definitions[node[1]] = node[2:]
    return []


def generate_compound(node, resolver, macro_definitions, file_info) -> list:
    code = []
    resolver.append_scope()
    resolver.use_next_scope()
    code.append(ScopeNode(resolver))
    code += generate_block(node, resolver, macro_definitions, file_info)
    code.append(PopScopeNode(resolver))
    resolver.restore_scope()
    return code


generators = {
    'block': generate_block,
    'scope': generate_scope,
    'map': generate_map,
    'compound': generate_compound,
    'macro': generate_macro,
    'macro_apply': generate_macro_application,
    'code_lookup': generate_code_lookup,
    'if': generate_if,
    'for': generate_for,
    'at_eq': generate_at_eq,
    'star_eq': generate_star_eq,
    'table': generate_table,
    'text': generate_text,
    'ascii': generate_ascii,
    'db': generate_db,
    'dw': generate_dw,
    'dl': generate_dl,
    'pointer': generate_dl,
    'symbol': generate_symbol,
    'assign': generate_symbol,
    'label': generate_label,
    'opcode': generate_opcode,
    'incbin': generate_incbin,
    'include_ips': generate_include_ips
}


def _code_gen(ast_nodes, resolver, macro_definitions):
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        generator = generators.get(node[0])
        if generator:
            code += generator(node, resolver, macro_definitions, file_info)
        else:
            raise RuntimeError('Left over node', node)

    return code
