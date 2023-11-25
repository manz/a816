from dataclasses import dataclass
from typing import Any

from a816.parse.ast.nodes import AstNode
from a816.parse.codegen import code_gen
from a816.parse.errors import ParserSyntaxError, ScannerException
from a816.parse.nodes import NodeProtocol
from a816.parse.parser import Parser
from a816.parse.parser_states import parse_initial
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial
from a816.symbols import Resolver


@dataclass
class ParserResult:
    nodes: list[AstNode]
    error: str | None

    @property
    def ast(self) -> list[tuple[Any, ...]]:
        return [node.to_representation() for node in self.nodes]


class MZParser:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def parse(self, program: str, filename: str = "") -> list[NodeProtocol]:
        ast = self.parse_as_ast(program, filename)
        return code_gen(ast.nodes, self.resolver)

    @staticmethod
    def parse_as_ast(program: str, filename: str = "memory.s") -> ParserResult:
        scanner = Scanner(lex_initial)
        ast: list[AstNode] = []
        error: str | None

        try:
            tokens = scanner.scan(filename, program)
            parser = Parser(tokens, parse_initial)
            ast = parser.parse()
            error = None
        except ScannerException as e:
            position = e.position
            position_str = str(position)
            line = position.get_line()
            error = f"{position_str} : {e}\n{line}\n" + (" " * position.column + "^")
        except ParserSyntaxError as e:
            error = e.token.trace()
        return ParserResult(nodes=ast, error=error)
