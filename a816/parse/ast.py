import pprint
from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import *


def code_gen(ast_nodes, resolver):
    macro_definitions = {}
    file_info_stack = []

    return _code_gen(ast_nodes, resolver, macro_definitions, file_info_stack)


def _get_file_info(node):
    last_node_item = node[-1]
    if isinstance(last_node_item, tuple):
        if last_node_item[0] == 'fileinfo':
            return last_node_item[1:]
    return None


def __code_gen(ast_nodes, resolver, macro_definitions, file_info_stack):
    # file_info_stack = _file_info_stack[:]
    code = []
    for node in ast_nodes:
        file_info = _get_file_info(node)
        if file_info:
            file_info_stack.append(file_info)

        if node[0] == 'block':
            code += _code_gen(node[1:], resolver, macro_definitions, file_info_stack)
        elif node[0] == 'named_scope':
            name = node[1]
            resolver.append_named_scope(name)
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            code += _code_gen(node[2:], resolver, macro_definitions, file_info_stack)
            code.append(PopScopeNode(resolver))
            resolver.restore_scope(exports=False)

        elif node[0] == 'compound':
            resolver.append_scope()
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            code += _code_gen(node[1:], resolver, macro_definitions, file_info_stack)
            code.append(PopScopeNode(resolver))
            resolver.restore_scope()
        elif node[0] == 'macro':
            macro_definitions[node[1]] = node[2:]
        elif node[0] == 'macro_apply':
            macro_def = macro_definitions[node[1]]

            macro_code = macro_def[1][1:]
            macro_args = macro_def[0][1]

            macro_args_values = node[2][1:][0]
            resolver.append_scope()
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            for index, arg in enumerate(macro_args):
                code.append(SymbolNode(arg, macro_args_values[index], resolver))

            code += _code_gen(macro_code, resolver, macro_definitions, file_info_stack)

            code.append(PopScopeNode(resolver))
            resolver.restore_scope()
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
                code += _code_gen(if_branch_true, resolver, macro_definitions, file_info_stack)
            elif if_branch_false:
                code += _code_gen(if_branch_false, resolver, macro_definitions, file_info_stack)
        elif node[0] == 'for':
            symbol, from_raw_val, to_raw_val, code_block, file_info = node[1:]
            from_val = eval_expr(from_raw_val, resolver)
            to_val = eval_expr(to_raw_val, resolver)
            for k in range(from_val, to_val):
                resolver.append_internal_scope()
                resolver.use_next_scope()
                code.append(ScopeNode(resolver))
                code.append(SymbolNode(symbol, str(k), resolver))
                code += _code_gen(code_block, resolver, macro_definitions, file_info_stack)
                code.append(PopScopeNode(resolver))
                resolver.restore_scope()
        elif node[0] == 'stareq':
            code.append(CodePositionNode(ExpressionNode(node[1], resolver), resolver))
        elif node[0] == 'pluseq':
            continue
        elif node[0] == 'tildaeq':
            continue
            # code.append(CodePositionNode(LabelReferenceNode(node[1], resolver), resolver))
        elif node[0] == 'table':
            code.append(TableNode(node[1], resolver))
        elif node[0] == 'text':
            code.append(TextNode(node[1], resolver))
        elif node[0] == 'label':
            code.append(LabelNode(node[1], resolver))
        elif node[0] == 'symbol':
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
        elif node[0] == 'pointer':
            code.append(PointerNode(ExpressionNode(node[1], resolver)))
        elif node[0] == 'opcode':
            opcode = node[2]
            size = None
            if isinstance(opcode, list) or isinstance(opcode, tuple):
                size = opcode[1]
                opcode = opcode[0]
            mode = node[1]
            if mode == AddressingMode.none:
                code.append(OpcodeNode(opcode, addressing_mode=mode, file_info=file_info_stack[-1]))
            elif mode in (
                    AddressingMode.direct_indexed, AddressingMode.indirect_indexed,
                    AddressingMode.indirect_indexed_long):
                code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                                       value_node=ExpressionNode(node[3], resolver),
                                       index=node[4], file_info=file_info_stack[-1]))
            else:
                code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                                       value_node=ExpressionNode(node[3], resolver),
                                       file_info=file_info_stack[-1]))
        else:
            pprint.pprint('ERR:' + str(node))
            pass
    return code


def display_stack(stack):
    for element in stack:
        print('%s: %s %s' % element)


def _code_gen(ast_nodes, resolver, macro_definitions, _file_info_stack):
    nodes = __code_gen(ast_nodes, resolver, macro_definitions, _file_info_stack)
    return nodes
