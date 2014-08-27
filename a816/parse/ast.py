import pprint
from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.nodes import ScopeNode, PopScopeNode, SymbolNode, CodePositionNode, TableNode, TextNode, LabelNode, \
    ByteNode, LabelReferenceNode, WordNode, BinaryNode, PointerNode, OpcodeNode


def code_gen(ast_nodes, resolver):
    macro_defs = {}
    return _code_gen(ast_nodes, resolver, macro_defs)


def _code_gen(ast_nodes, resolver, macro_defs):
    code = []
    for node in ast_nodes:
        if node[0] == 'block':
            code += _code_gen(node[1:], resolver, macro_defs)
        elif node[0] == 'compound':
            resolver.append_scope()
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            code += _code_gen(node[1:], resolver, macro_defs)
            code.append(PopScopeNode(resolver))
            resolver.restore_scope()
        elif node[0] == 'macro':
            macro_defs[node[1]] = node[2:]
        elif node[0] == 'macro_apply':
            macro_def = macro_defs[node[1]]

            macro_code = macro_def[1][1:]
            macro_args = macro_def[0][1]

            macro_args_values = node[2][1:][0]
            resolver.append_scope()
            resolver.use_next_scope()
            code.append(ScopeNode(resolver))

            for index, arg in enumerate(macro_args):
                code.append(SymbolNode(arg, macro_args_values[index], resolver))

            code += _code_gen(macro_code, resolver, macro_defs)

            code.append(PopScopeNode(resolver))
            resolver.restore_scope()
        elif node[0] == 'stareq':
            code.append(CodePositionNode(node[1], resolver))
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
                code.append(ByteNode(LabelReferenceNode(expr, resolver)))
        elif node[0] == 'dw':
            for expr in node[1]:
                code.append(WordNode(LabelReferenceNode(expr, resolver)))
        elif node[0] == 'incbin':
            code.append(BinaryNode(node[1], resolver))
        elif node[0] == 'pointer':
            code.append(PointerNode(LabelReferenceNode(node[1], resolver)))
        elif node[0] == 'opcode':
            opcode = node[2]
            size = None
            if isinstance(opcode, list):
                size = opcode[1]
                opcode = opcode[0]
            mode = node[1]
            if mode == AddressingMode.none:
                code.append(OpcodeNode(opcode, addressing_mode=mode))
            elif mode in (AddressingMode.direct_indexed, AddressingMode.indirect_indexed, AddressingMode.indirect_indexed_long):
                code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                                       value_node=LabelReferenceNode(node[3], resolver),
                                       index=node[4]))
            else:
                code.append(OpcodeNode(opcode, addressing_mode=mode, size=size,
                                       value_node=LabelReferenceNode(node[3], resolver)))
        else:
            pprint.pprint('ERR:' + str(node[0]))
            pass
    return code