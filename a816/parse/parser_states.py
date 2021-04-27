import ast
import os

from a816.cpu.cpu_65c816 import AddressingMode

from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial
from a816.parse.parser import Parser
from a816.parse.errors import ParserSyntaxError
from a816.parse.tokens import TokenType
from a816.parse.parser import expect_token, accept_token, expect_tokens, accept_tokens


def parse_scope(p: Parser):
    keyword = p.next()
    expect_token(keyword, TokenType.IDENTIFIER)

    next_token = p.next()
    expect_token(next_token, TokenType.LBRACE)
    block = parse_block(p)
    return 'scope', keyword.value, (('block', block),)


def parse_text(p: Parser):
    text = p.next()

    expect_token(text, TokenType.QUOTED_STRING)

    return 'text', text.value[1:-1]


def parse_macro_definition_args(p):
    args = []

    first_arg = p.next()
    if not accept_token(first_arg, TokenType.RPAREN):
        expect_token(first_arg, TokenType.IDENTIFIER)

        args.append(first_arg.value)

        while True:
            token = p.next()
            expect_tokens(token, [TokenType.COMMA, TokenType.RPAREN, TokenType.IDENTIFIER])

            if accept_token(token, TokenType.RPAREN):
                p.backup()
                break
            elif accept_token(token, TokenType.COMMA):
                continue
            else:
                expect_token(token, TokenType.IDENTIFIER)
                args.append(token.value)
    else:
        p.backup()
    return args


def parse_expression_list_inner(p: Parser):
    expressions = []
    while True:
        if accept_token(p.current(), TokenType.RPAREN):
            break
        if accept_token(p.current(), TokenType.LBRACE):
            p.next()
            expressions.append(('block', parse_block(p)))
        else:
            expressions.append(parse_expression(p))
        if accept_tokens(p.current(), [TokenType.COMMA]):
            p.next()
        else:
            break

    return expressions


def parse_expression_list(p: Parser):
    expect_token(p.next(), TokenType.LPAREN)
    expressions = parse_expression_list_inner(p)

    expect_token(p.next(), TokenType.RPAREN)

    return 'apply_args', expressions


def parse_macro_application(p: Parser):
    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)

    return 'macro_apply', macro_identifier.value, parse_expression_list(p)


def parse_macro(p: Parser):
    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)

    expect_token(p.next(), TokenType.LPAREN)

    args = parse_macro_definition_args(p)
    expect_token(p.next(), TokenType.RPAREN)
    expect_token(p.next(), TokenType.LBRACE)
    block = parse_block(p)

    return 'macro', macro_identifier.value, ('args', args), ('block', block)


def parse_map(p: Parser):
    args = {}

    while p.current().type == TokenType.IDENTIFIER:
        identifier = p.next()

        expect_token(identifier, TokenType.IDENTIFIER)

        if identifier.value not in {'identifier', 'writable', 'bank_range', 'addr_range', 'mask', 'mirror_bank_range'}:
            raise ParserSyntaxError(f'Unknown attribute for map directive. {identifier.value}', identifier)

        expect_token(p.next(), TokenType.EQUAL)
        number1 = p.next()
        expect_token(number1, TokenType.NUMBER)
        if accept_token(p.current(), TokenType.COMMA):
            p.next()
            number2 = p.next()
            expect_token(number2, TokenType.NUMBER)

            args[identifier.value] = (ast.literal_eval(number1.value), ast.literal_eval(number2.value))
        else:
            args[identifier.value] = ast.literal_eval(number1.value)

    return 'map', args


def parse_if(p: Parser):
    condition = parse_expression(p)
    expect_token(p.next(), TokenType.LBRACE)
    body = ('compound', parse_block(p))
    else_body = None
    if p.current().value == 'else':
        p.next()
        expect_token(p.next(), TokenType.LBRACE)
        else_body = ('compound', parse_block(p))

    return 'if', condition, body, else_body


def parse_for(p: Parser):
    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.ASSIGN)
    start = parse_expression(p)
    expect_token(p.next(), TokenType.COMMA)
    end = parse_expression(p)

    expect_token(p.next(), TokenType.LBRACE)
    block = ('compound', parse_block(p))

    return 'for', variable.value, start, end, block


def parse_directive_with_quoted_string(p: Parser):
    string = p.next()
    expect_token(string, TokenType.QUOTED_STRING)

    return string.value[1:-1]


def parse_include_ips(p: Parser):
    string = parse_directive_with_quoted_string(p)

    expect_token(p.next(), TokenType.COMMA)
    expression = parse_expression(p)

    return 'include_ips', string, expression


def parse_keyword(p: Parser):
    keyword = p.next()

    if keyword.value == 'scope':
        return parse_scope(p)
    elif keyword.value in ('text', 'ascii'):
        return keyword.value, parse_directive_with_quoted_string(p)
    elif keyword.value == 'dw':
        expressions = parse_expression_list_inner(p)
        return 'dw', expressions
    elif keyword.value == 'dl':
        expressions = parse_expression_list_inner(p)
        return 'dl', expressions
    elif keyword.value == 'db':
        expressions = parse_expression_list_inner(p)
        return 'db', expressions
    elif keyword.value == 'pointer':
        expressions = parse_expression_list_inner(p)
        return 'pointer', expressions
    elif keyword.value == 'include':
        filename = parse_directive_with_quoted_string(p)

        with open(filename, encoding='utf-8') as fd:
            source = fd.read()

            scanner = Scanner(lex_initial)
            tokens = scanner.scan(filename, source)

            parser = Parser(tokens, parse_initial)
            sub_ast = parser.parse()

        return 'block', sub_ast
    elif keyword.value == 'include_ips':
        return parse_include_ips(p)
    elif keyword.value == 'incbin':
        return 'incbin', parse_directive_with_quoted_string(p)
    elif keyword.value == 'table':
        return 'table', parse_directive_with_quoted_string(p)
    elif keyword.value == 'macro':
        return parse_macro(p)
    elif keyword.value == 'map':
        return parse_map(p)
    elif keyword.value == 'if':
        return parse_if(p)
    elif keyword.value == 'for':
        return parse_for(p)
    else:
        raise ParserSyntaxError(f'Unexpected token {keyword}', keyword)


def parse_label(p: Parser):
    p.backup()
    current_token = p.next()

    return 'label', current_token.value


def parse_block(p: Parser):
    decl = []
    while p.current().type != TokenType.EOF:
        if p.current().type == TokenType.RBRACE:
            break
        statement = parse_decl(p)
        if statement is not None:
            decl.append(statement)

    expect_token(p.next(), TokenType.RBRACE)
    return decl


def parse_code_position_keyword(p: Parser):
    value = p.next()
    return value.value


def parse_code_relocation_keyword(p: Parser):
    code_position = parse_expression(p)
    return 'at_eq', code_position


def parse_expression(p: Parser):
    nodes = __parse_expression(p)

    nodes = [token.value for token in nodes]

    return ' '.join(nodes)


def __parse_expression(p: Parser):
    nodes = []
    current_token = p.next()
    if accept_token(current_token, TokenType.LPAREN):
        nodes.append(current_token)
        nodes += __parse_expression(p)
        expect_token(p.current(), TokenType.RPAREN)
        nodes.append(p.next())
    elif accept_tokens(current_token, [TokenType.NUMBER, TokenType.IDENTIFIER]):
        nodes.append(current_token)
    elif accept_token(current_token, TokenType.OPERATOR) and current_token.value == '-':
        nodes.append(current_token)
        nodes += __parse_expression(p)

    if nodes:
        operator = p.current()

        if accept_tokens(operator, [TokenType.OPERATOR, TokenType.RIGHT_SHIFT, TokenType.LEFT_SHIFT]):
            p.next()
            nodes.append(operator)
            return nodes + __parse_expression(p)
        else:
            return nodes
    return nodes


def _parse_expression(p: Parser):
    current_node = None
    current_token = p.next()

    if accept_token(current_token, TokenType.LPAREN):
        current_node = parse_expression(p)
        # consumes the closing parenthesis
        expect_token(p.next(), TokenType.RPAREN)

    elif accept_tokens(current_token, [TokenType.NUMBER, TokenType.IDENTIFIER]):
        current_node = current_token.value

    if current_node:
        operator = p.current()

        if accept_tokens(p.current(), [TokenType.OPERATOR, TokenType.RIGHT_SHIFT, TokenType.LEFT_SHIFT]):
            p.next()
            return operator.value, current_node, parse_expression(p)
        else:
            return current_node


def parse_symbol_affectation(p):
    symbol = p.next()
    expect_tokens(p.next(), [TokenType.EQUAL, TokenType.ASSIGN])

    if p.current() == TokenType.EQUAL:
        node_type = 'symbol'
    else:
        node_type = 'assign'

    expression = parse_expression(p)

    return node_type, symbol.value, expression


index_map = {
    AddressingMode.indirect: AddressingMode.indirect_indexed,
    AddressingMode.indirect_long: AddressingMode.indirect_indexed_long,
    AddressingMode.direct: AddressingMode.direct_indexed,
    AddressingMode.dp_or_sr_indirect_indexed: AddressingMode.stack_indexed_indirect_indexed
}


def parse_opcode(p):
    opcode = p.next()
    size = None
    operand = None
    index = None

    if accept_token(opcode, TokenType.OPCODE_NAKED):
        addressing_mode = AddressingMode.none
    else:
        addressing_mode = AddressingMode.direct

    if accept_token(p.current(), TokenType.OPCODE_SIZE):
        size = p.current().value
        p.next()

    addressing_mode, inner_index, operand = parse_operand_and_addressing(
        addressing_mode, opcode, operand, p)

    if accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
        index = p.next().value.lower()
        addressing_mode = index_map[addressing_mode]

    if size is not None:
        opcode_value = (opcode.value, size.lower())
    else:
        opcode_value = opcode.value

    return 'opcode', addressing_mode, opcode_value, operand, index or inner_index


def parse_operand_and_addressing(addressing_mode, opcode, operand, p):
    inner_index = None

    if accept_token(p.current(), TokenType.SHARP):
        addressing_mode = AddressingMode.immediate
        p.next()
        if accept_token(p.current(), TokenType.EOF):
            raise ParserSyntaxError(f'Unexpected end of input.', p.current(), None)
        operand = parse_expression(p)
    elif accept_token(p.current(), TokenType.LPAREN):
        saved_position = p.pos
        try:
            p.next()
            operand = parse_expression(p)
            if accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
                addressing_mode = AddressingMode.dp_or_sr_indirect_indexed
                inner_index = p.current().value
                p.next()
            else:
                addressing_mode = AddressingMode.indirect

            expect_token(p.current(), TokenType.RPAREN)

            if accept_tokens(p.peek(), [TokenType.OPERATOR, TokenType.RIGHT_SHIFT, TokenType.LEFT_SHIFT]):
                raise SyntaxError()
            p.next()
        except SyntaxError:
            p.pos = saved_position
            operand = parse_expression(p)
            addressing_mode = AddressingMode.direct
    elif accept_token(p.current(), TokenType.LBRAKET):
        p.next()
        operand = parse_expression(p)
        expect_token(p.next(), TokenType.RBRAKET)
        addressing_mode = AddressingMode.indirect_long
    elif accept_token(opcode, TokenType.OPCODE):
        operand = parse_expression(p)
    return addressing_mode, inner_index, operand


def parse_code_lookup(p: Parser):
    identifier = p.next()
    expect_token(identifier, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.DOUBLE_RBRACE)

    return 'code_lookup', identifier.value


def parse_decl(p: Parser):
    current_token = p.next()
    if accept_token(current_token, TokenType.COMMENT):
        return
    elif accept_token(current_token, TokenType.DOUBLE_LBRACE):
        return parse_code_lookup(p)
    elif accept_tokens(current_token, [TokenType.OPCODE, TokenType.OPCODE_NAKED]):
        p.backup()
        return parse_opcode(p)
    elif accept_token(current_token, TokenType.KEYWORD):
        p.backup()
        return parse_keyword(p)
    elif accept_token(current_token, TokenType.IDENTIFIER):
        p.backup()
        if accept_token(p.peek(), TokenType.LPAREN):
            return parse_macro_application(p)
        else:
            # might be another thing but we check for equal inside parse_symbol_affectation
            return parse_symbol_affectation(p)
    elif accept_token(current_token, TokenType.LABEL):
        return parse_label(p)
    elif accept_token(current_token, TokenType.LBRACE):
        return 'compound', parse_block(p)
    elif accept_token(current_token, TokenType.STAR_EQ):
        return 'star_eq', parse_code_position_keyword(p)
    elif accept_token(current_token, TokenType.AT_EQ):
        return parse_code_relocation_keyword(p)
    else:
        raise ParserSyntaxError(f'Unexpected Keyword {current_token}', current_token, None)


def parse_initial(p: 'Parser'):
    statements = []
    while p.current().type != TokenType.EOF:

        statement = parse_decl(p)
        if statement:
            statements.append(statement)

    return statements
