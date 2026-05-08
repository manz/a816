import logging
from dataclasses import dataclass
from pathlib import Path
from time import gmtime, strftime
from typing import Any

from a816.parse.ast.nodes import AstNode
from a816.parse.codegen import code_gen
from a816.parse.errors import ParseError, ParserSyntaxError, ScannerException
from a816.parse.parser import Parser
from a816.parse.parser_states import parse_initial
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial
from a816.protocols import NodeProtocol
from a816.symbols import Resolver

logger = logging.getLogger("a816.parser")


@dataclass
class ParserResult:
    nodes: list[AstNode]
    parse_error: ParseError | None = None

    @property
    def error(self) -> str | None:
        """Formatted error string for display."""
        return self.parse_error.format() if self.parse_error else None

    @property
    def ast(self) -> list[tuple[Any, ...]]:
        return [node.to_representation() for node in self.nodes]


class MZParser:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def parse(self, program: str, filename: str = "") -> tuple[str | None, list[NodeProtocol]]:
        include_paths = self.resolver.context.include_paths
        ast = self.parse_as_ast(program, filename, include_paths=include_paths, verbose_errors=True)
        self.resolver.current_scope.add_symbol("BUILD_DATE", strftime("%Y-%m-%d %H:%M:%S", gmtime()))
        return ast.error, code_gen(ast.nodes, self.resolver)

    @staticmethod
    def parse_as_ast(
        program: str,
        filename: str = "memory.s",
        include_paths: list[Path] | None = None,
        verbose_errors: bool = False,
    ) -> ParserResult:
        scanner = Scanner(lex_initial)
        ast: list[AstNode] = []
        parse_error: ParseError | None = None

        try:
            tokens = scanner.scan(filename, program)
            parser = Parser(tokens, parse_initial, include_paths=include_paths)
            ast = parser.parse()
        except ScannerException as e:
            position = e.position
            try:
                source_line = position.get_line()
            except (IndexError, AttributeError):
                source_line = ""
            parse_error = ParseError(
                message=str(e),
                filename=position.file.filename,
                line=position.line,
                column=position.column,
                source_line=source_line,
                length=1,
            )
        except ParserSyntaxError as e:
            if verbose_errors:
                logger.exception(e)
                e.token.display()
            if e.token.position is not None:
                pos = e.token.position
                try:
                    source_line = pos.get_line()
                except (IndexError, AttributeError):
                    source_line = ""
                parse_error = ParseError(
                    message=str(e),
                    filename=pos.file.filename,
                    line=pos.line,
                    column=pos.column,
                    source_line=source_line,
                    length=len(e.token.value) if e.token.value else 1,
                )
            else:
                parse_error = ParseError(
                    message=str(e),
                    filename=filename,
                    line=0,
                    column=0,
                )
        return ParserResult(nodes=ast, parse_error=parse_error)
