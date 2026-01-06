from dataclasses import dataclass
from time import gmtime, strftime
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

    def parse(self, program: str, filename: str = "") -> tuple[str | None, list[NodeProtocol]]:
        ast = self.parse_as_ast(program, filename)
        self.resolver.current_scope.add_symbol("BUILD_DATE", strftime("%Y-%m-%d %H:%M:%S", gmtime()))
        return ast.error, code_gen(ast.nodes, self.resolver)

    @staticmethod
    def parse_as_ast(program: str, filename: str = "memory.s") -> ParserResult:
        from a816.errors import SourceLocation, format_error

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
            try:
                source_line = position.get_line()
            except (IndexError, AttributeError):
                source_line = ""
            location = SourceLocation(
                filename=position.file.filename,
                line=position.line,
                column=position.column,
                source_line=source_line,
                length=1,
            )
            error = format_error(str(e), location)
        except ParserSyntaxError as e:
            if e.token.position is not None:
                pos = e.token.position
                try:
                    source_line = pos.get_line()
                except (IndexError, AttributeError):
                    source_line = ""
                location = SourceLocation(
                    filename=pos.file.filename,
                    line=pos.line,
                    column=pos.column,
                    source_line=source_line,
                    length=len(e.token.value) if e.token.value else 1,
                )
                error = format_error(str(e), location)
            else:
                error = format_error(str(e))
        return ParserResult(nodes=ast, error=error)
