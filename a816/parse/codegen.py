from a816.cpu.cpu_65c816 import AddressingMode
from a816.cpu.mapping import Bus
from a816.parse.nodes import *


def code_gen(ast_nodes, resolver):
    macro_definitions = {}
    return _code_gen(ast_nodes, resolver, macro_definitions)


def _get_file_info(node):
    last_node_item = node[-1]
    if isinstance(last_node_item, tuple):
        if last_node_item[0] == 'fileinfo':
            return last_node_item[1:]
    return None


def _code_gen(ast_nodes, resolver, macro_definitions):
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)

        if node[0] == 'block':
            code += _code_gen(node[1], resolver, macro_definitions)
        elif node[0] == 'scope':
            name = node[1]
            resolver.append_named_scope(name)
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            code += _code_gen(node[2], resolver, macro_definitions)
            code.append(PopScopeNode(resolver))
            resolver.restore_scope(exports=False)
        elif node[0] == 'map':
            attributes = node[1]

            # def map(self, identifier, bank_range, address_range, mask, writeable=False, mirror_bank_range=None):
            resolver.bus.map(
                attributes['identifier'],
                attributes['bank_range'],
                attributes['addr_range'],
                attributes['mask'],
                writeable=attributes.get('writable', False),
                mirror_bank_range=attributes.get('mirror_bank_range'))
        elif node[0] == 'compound':
            resolver.append_scope()
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            code += _code_gen(node[1], resolver, macro_definitions)
            code.append(PopScopeNode(resolver))
            resolver.restore_scope()
        elif node[0] == 'macro':
            macro_definitions[node[1]] = node[2:]
        elif node[0] == 'macro_apply':
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
        elif node[0] == 'code_lookup':
            code += _code_gen(resolver.current_scope.value_for(node[1]), resolver, macro_definitions)
        # FIXME unused
        elif node[0] == 'if':
            symbol = node[1]
            if_branch_true = node[2]
            try:
                if_branch_false = node[3]
            except IndexError:
                if_branch_false = None

            try:
                condition = resolver.current_scope.value_for(symbol)
            except KeyError:
                condition = False

            if condition:
                code += _code_gen(if_branch_true, resolver, macro_definitions)
            elif if_branch_false:
                code += _code_gen(if_branch_false, resolver, macro_definitions)
        # FIXME: unused
        elif node[0] == 'for':
            symbol, from_raw_val, to_raw_val, code_block, file_info = node[1:]
            from_val = eval_expr(from_raw_val, resolver)
            to_val = eval_expr(to_raw_val, resolver)
            for k in range(from_val, to_val):
                resolver.append_internal_scope()
                resolver.use_next_scope()
                code.append(ScopeNode(resolver))
                code.append(SymbolNode(symbol, str(k), resolver))
                code += _code_gen(code_block, resolver, macro_definitions)
                code.append(PopScopeNode(resolver))
                resolver.restore_scope()
        elif node[0] == 'star_eq':
            code.append(CodePositionNode(ExpressionNode(node[1], resolver), resolver))
        elif node[0] == 'pluseq':
            continue
        elif node[0] == 'at_eq':
            code.append(RelocationAddressNode(ExpressionNode(node[1], resolver), resolver))
        elif node[0] == 'table':
            code.append(TableNode(node[1], resolver))
        elif node[0] == 'text':
            code.append(TextNode(node[1], resolver))
        elif node[0] == 'label':
            code.append(LabelNode(node[1], resolver))
        elif node[0] == 'symbol':
            code.append(SymbolNode(node[1], node[2], resolver))
        # FIXME: unused
        elif node[0] == 'assign':
            code.append(SymbolNode(node[1], node[2], resolver))
        elif node[0] == 'db':
            for expr in node[1]:
                code.append(ByteNode(ExpressionNode(expr, resolver)))
        elif node[0] == 'dw':
            for expr in node[1]:
                code.append(WordNode(ExpressionNode(expr, resolver)))
        elif node[0] == 'dl':
            for expr in node[1]:
                code.append(LongNode(ExpressionNode(expr, resolver)))
        elif node[0] == 'incbin':
            code.append(BinaryNode(node[1], resolver))
        elif node[0] == 'include_ips':
            code.append(IncludeIpsNode(node[1], resolver, node[2]))
        elif node[0] == 'pointer':
            for expr in node[1]:
                code.append(LongNode(ExpressionNode(expr, resolver)))
        elif node[0] == 'opcode':
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
        else:
            raise RuntimeError('Left over node')

    return code
