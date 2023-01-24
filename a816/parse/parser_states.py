import ast
from typing import List, Literal, Optional, Tuple, Type, Union, cast

from a816.cpu.cpu_65c816 import AddressingMode
from a816.parse.ast.nodes import (
    AsciiAstNode,
    AssignAstNode,
    AstNode,
    BinOp,
    BlockAstNode,
    CodeLookupAstNode,
    CodePositionAstNode,
    CodeRelocationAstNode,
    CompoundAstNode,
    DataNode,
    ExpressionAstNode,
    ExprNode,
    ForAstNode,
    IfAstNode,
    IncludeBinaryAstNode,
    IncludeIpsAstNode,
    KeywordAstNode,
    LabelAstNode,
    MacroApplyAstNode,
    MacroAstNode,
    MapArgs,
    MapAstNode,
    OpcodeAstNode,
    Parenthesis,
    ScopeAstNode,
    StructAstNode,
    SymbolAffectationAstNode,
    TableAstNode,
    Term,
    TextAstNode,
    UnaryOp,
    index_map,
)
from a816.parse.errors import ParserSyntaxError
from a816.parse.parser import (
    Parser,
    StateFunc,
    accept_token,
    accept_tokens,
    expect_token,
    expect_tokens,
)
from a816.parse.scanner import Scanner, ScannerStateFunc
from a816.parse.scanner_states import lex_initial
from a816.parse.tokens import Token, TokenType


def parse_scope(p: Parser) -> ScopeAstNode:
    current = p.current()
    keyword = p.next()
    expect_token(keyword, TokenType.IDENTIFIER)

    next_token = p.next()
    expect_token(next_token, TokenType.LBRACE)
    block = parse_block(p)
    return ScopeAstNode(keyword.value, BlockAstNode(block, next_token), current)


def parse_macro_definition_args(p: Parser) -> List[str]:
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


def parse_expression_list_inner(p: Parser) -> List[Union[ExpressionAstNode, BlockAstNode]]:
    expressions: List[Union[ExpressionAstNode, BlockAstNode]] = []
    while True:
        if accept_token(p.current(), TokenType.RPAREN):
            break
        if accept_token(p.current(), TokenType.LBRACE):
            current = p.current()
            p.next()
            expressions.append(BlockAstNode(parse_block(p), current))
        else:
            expressions.append(parse_expression(p))
        if accept_tokens(p.current(), [TokenType.COMMA]):
            p.next()
        else:
            break

    return expressions


def parse_expression_list(p: Parser) -> List[Union[ExpressionAstNode, BlockAstNode]]:
    expect_token(p.next(), TokenType.LPAREN)
    expressions = parse_expression_list_inner(p)

    expect_token(p.next(), TokenType.RPAREN)

    return expressions


def parse_macro_application(p: Parser) -> MacroApplyAstNode:
    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)
    return MacroApplyAstNode(macro_identifier.value, parse_expression_list(p), macro_identifier)


def parse_macro(p: Parser) -> MacroAstNode:
    macro_identifier = p.next()
    expect_token(macro_identifier, TokenType.IDENTIFIER)

    expect_token(p.next(), TokenType.LPAREN)

    args = parse_macro_definition_args(p)
    expect_token(p.next(), TokenType.RPAREN)
    block_token = p.current()
    expect_token(p.next(), TokenType.LBRACE)
    block = parse_block(p)

    return MacroAstNode(macro_identifier.value, args, BlockAstNode(block, block_token), macro_identifier)


def parse_map(p: Parser) -> MapAstNode:
    args: MapArgs = {}
    first_identifier = p.current()
    expect_token(first_identifier, TokenType.IDENTIFIER)

    while p.current().type == TokenType.IDENTIFIER:
        identifier = p.next()

        expect_token(identifier, TokenType.IDENTIFIER)
        key = identifier.value

        if key in {"identifier", "writable", "bank_range", "addr_range", "mask", "mirror_bank_range"}:
            map_key = cast(
                Literal["identifier", "writable", "bank_range", "addr_range", "mask", "mirror_bank_range"], key
            )
            expect_token(p.next(), TokenType.EQUAL)
            number1 = p.next()
            expect_token(number1, TokenType.NUMBER)
            if accept_token(p.current(), TokenType.COMMA):
                p.next()
                number2 = p.next()
                expect_token(number2, TokenType.NUMBER)

                args[map_key] = (ast.literal_eval(number1.value), ast.literal_eval(number2.value))
            else:
                args[map_key] = ast.literal_eval(number1.value)
        else:
            raise ParserSyntaxError(f"Unknown attribute for map directive. {identifier.value}", identifier)

    return MapAstNode(args, first_identifier)


def parse_if(p: Parser) -> IfAstNode:
    current = p.current()
    condition = parse_expression(p)
    expect_token(p.next(), TokenType.LBRACE)
    body = CompoundAstNode(parse_block(p), p.current())
    else_body = None
    if p.current().value == "else":
        p.next()
        expect_token(p.next(), TokenType.LBRACE)
        else_body = CompoundAstNode(parse_block(p), p.current())

    return IfAstNode(condition, body, else_body, current)


def parse_for(p: Parser) -> ForAstNode:
    current = p.current()
    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.ASSIGN)
    start = parse_expression(p)
    expect_token(p.next(), TokenType.COMMA)
    end = parse_expression(p)

    expect_token(p.next(), TokenType.LBRACE)
    block = CompoundAstNode(parse_block(p), p.current())

    return ForAstNode(variable.value, start, end, block, current)


def parse_struct(p: Parser) -> StructAstNode:
    current = p.current()

    variable = p.next()
    expect_token(variable, TokenType.IDENTIFIER)

    expect_token(p.next(), TokenType.LBRACE)
    fields = {}
    while p.current().type != TokenType.EOF:
        if p.current().type == TokenType.COMMENT:
            p.next()
            continue
        if p.current().type == TokenType.RBRACE:
            break

        expect_token(p.current(), TokenType.TYPE)
        field_type = p.next()
        expect_token(p.current(), TokenType.IDENTIFIER)
        field_identifier = p.current()

        fields[field_identifier.value] = field_type.value

        p.next()

    expect_token(p.next(), TokenType.RBRACE)

    return StructAstNode(variable.value, fields, current)


def parse_directive_with_quoted_string(p: Parser) -> str:
    string = p.next()
    expect_token(string, TokenType.QUOTED_STRING)

    return string.value[1:-1]


def parse_include_ips(p: Parser) -> IncludeIpsAstNode:
    current = p.current()
    string = parse_directive_with_quoted_string(p)

    expect_token(p.next(), TokenType.COMMA)
    expression = parse_expression(p)

    return IncludeIpsAstNode(string, expression, current)


def parse_keyword(p: Parser) -> KeywordAstNode:
    keyword = p.next()

    if keyword.value == "scope":
        return parse_scope(p)
    elif keyword.value == "ascii":
        return AsciiAstNode(parse_directive_with_quoted_string(p), keyword)
    elif keyword.value == "text":
        return TextAstNode(parse_directive_with_quoted_string(p), keyword)
    elif keyword.value == "dw":
        expressions = parse_expression_list_inner(p)
        return DataNode("dw", expressions, keyword)
    elif keyword.value == "dl":
        expressions = parse_expression_list_inner(p)
        return DataNode("dl", expressions, keyword)
    elif keyword.value == "db":
        expressions = parse_expression_list_inner(p)
        return DataNode("db", expressions, keyword)
    elif keyword.value == "pointer":
        expressions = parse_expression_list_inner(p)
        return DataNode("pointer", expressions, keyword)
    elif keyword.value == "include":
        filename = parse_directive_with_quoted_string(p)

        with open(filename, encoding="utf-8") as fd:
            source = fd.read()

            scanner = Scanner(cast(ScannerStateFunc, lex_initial))
            tokens = scanner.scan(filename, source)

            parser = Parser(tokens, cast(StateFunc, parse_initial))
            sub_ast = parser.parse()

        return BlockAstNode(sub_ast, keyword)
    elif keyword.value == "include_ips":
        return parse_include_ips(p)
    elif keyword.value == "incbin":
        return IncludeBinaryAstNode(parse_directive_with_quoted_string(p), p.current())
    elif keyword.value == "table":
        return TableAstNode(parse_directive_with_quoted_string(p), p.current())
    elif keyword.value == "macro":
        return parse_macro(p)
    elif keyword.value == "map":
        return parse_map(p)
    elif keyword.value == "if":
        return parse_if(p)
    elif keyword.value == "for":
        return parse_for(p)
    elif keyword.value == "struct":
        return parse_struct(p)
    else:
        raise ParserSyntaxError(f"Unexpected token {keyword}", keyword)


def parse_label(p: Parser) -> LabelAstNode:
    p.backup()
    current_token = p.next()

    return LabelAstNode(current_token.value, current_token)


def parse_block(p: Parser) -> List[AstNode]:
    decl = []
    while p.current().type != TokenType.EOF:
        if p.current().type == TokenType.RBRACE:
            break
        statement = parse_decl(p)
        if statement is not None:
            decl.append(statement)

    expect_token(p.next(), TokenType.RBRACE)
    return decl


def parse_code_position_keyword(p: Parser) -> CodePositionAstNode:
    current = p.current()
    code_position = parse_expression(p)
    return CodePositionAstNode(code_position, current)


def parse_code_relocation_keyword(p: Parser) -> CodeRelocationAstNode:
    current = p.current()
    code_position = parse_expression(p)
    return CodeRelocationAstNode(code_position, current)


def parse_expression(p: Parser) -> ExpressionAstNode:
    nodes = _parse_expression(p)

    return ExpressionAstNode(nodes)


def parse_expression_ep(p: Parser) -> List[AstNode]:
    return [parse_expression(p)]


def _parse_expression(p: Parser) -> List[ExprNode]:
    tokens: List[ExprNode] = []
    current_token = p.next()
    if accept_token(current_token, TokenType.LPAREN):
        tokens.append(Parenthesis(current_token))
        tokens += _parse_expression(p)
        expect_token(p.current(), TokenType.RPAREN)
        tokens.append(Parenthesis(p.next()))
    elif accept_tokens(current_token, [TokenType.NUMBER, TokenType.BOOLEAN, TokenType.IDENTIFIER]):
        tokens.append(Term(current_token))
    elif accept_token(current_token, TokenType.OPERATOR) and current_token.value == "-":
        tokens.append(UnaryOp(current_token))
        tokens += _parse_expression(p)
    else:
        raise ParserSyntaxError("Invalid expression", token=current_token)

    if tokens:
        operator = p.current()

        if accept_token(operator, TokenType.OPERATOR):
            p.next()
            tokens.append(BinOp(operator))
            return tokens + _parse_expression(p)
        else:
            return tokens
    return tokens


def parse_symbol_affectation(p: Parser) -> Union[SymbolAffectationAstNode, AssignAstNode]:
    current = p.current()
    symbol = p.next()
    expect_tokens(p.next(), [TokenType.EQUAL, TokenType.ASSIGN])
    node_type: Union[Type[SymbolAffectationAstNode], Type[AssignAstNode]]
    if p.current().type == TokenType.EQUAL:
        node_type = SymbolAffectationAstNode
    else:
        node_type = AssignAstNode

    expression = parse_expression(p)

    return node_type(symbol.value, expression, current)


def parse_opcode(p: Parser) -> OpcodeAstNode:
    opcode: Token = p.next()
    size: Optional[str] = None
    index: Optional[str] = None

    if accept_token(opcode, TokenType.OPCODE_NAKED):
        addressing_mode = AddressingMode.none
    else:
        addressing_mode = AddressingMode.direct

    if accept_token(p.current(), TokenType.OPCODE_SIZE):
        size = p.current().value
        p.next()

    addressing_mode, inner_index, operand = parse_operand_and_addressing(addressing_mode, opcode, p)

    if accept_token(p.current(), TokenType.ADDRESSING_MODE_INDEX):
        index = p.next().value.lower()
        addressing_mode = index_map[addressing_mode]

    opcode_value: Union[Tuple[str, str], str]

    if size is not None:
        opcode_value = (opcode.value, size.lower())
    else:
        opcode_value = opcode.value

    return OpcodeAstNode(
        addressing_mode=addressing_mode,
        opcode_value=opcode_value,
        operand=operand,
        index=index or inner_index,
        file_info=opcode,
    )


def parse_operand_and_addressing(
    addressing_mode: AddressingMode, opcode: Token, p: Parser
) -> Tuple[AddressingMode, Optional[str], Optional[ExpressionAstNode]]:
    inner_index = None
    operand = None
    if accept_token(p.current(), TokenType.SHARP):
        addressing_mode = AddressingMode.immediate
        p.next()
        if accept_token(p.current(), TokenType.EOF):
            raise ParserSyntaxError(f"Unexpected end of input.", p.current(), None)
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

            if accept_token(p.peek(), TokenType.OPERATOR):
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


def parse_code_lookup(p: Parser) -> CodeLookupAstNode:
    current = p.current()
    identifier = p.next()
    expect_token(identifier, TokenType.IDENTIFIER)
    expect_token(p.next(), TokenType.DOUBLE_RBRACE)

    return CodeLookupAstNode(identifier.value, current)


def parse_decl(
    p: Parser,
) -> Optional[AstNode]:
    current_token = p.next()
    if accept_token(current_token, TokenType.COMMENT):
        return None
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
        return CompoundAstNode(parse_block(p), current_token)
    elif accept_token(current_token, TokenType.STAR_EQ):
        return parse_code_position_keyword(p)
    elif accept_token(current_token, TokenType.AT_EQ):
        return parse_code_relocation_keyword(p)
    else:
        raise ParserSyntaxError(f"Unexpected Keyword {current_token}", current_token, None)


def parse_initial(p: Parser) -> List[AstNode]:
    statements: List[AstNode] = []
    while p.current().type != TokenType.EOF:

        statement = parse_decl(p)
        if statement:
            statements.append(statement)

    return statements
