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
    parse_errors: list[ParseError] | None = None

    @property
    def error(self) -> str | None:
        """Formatted text of every collected parse error, blank-line separated.

        Returns the first error alone when only one was collected so existing
        single-error callers behave unchanged.
        """
        errors = self.parse_errors or ([self.parse_error] if self.parse_error else [])
        if not errors:
            return None
        return "\n\n".join(e.format() for e in errors)

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
        parser: Parser | None = None

        try:
            tokens = scanner.scan(filename, program)
            parser = Parser(tokens, parse_initial, include_paths=include_paths)
            ast = parser.parse()
        except ScannerException as e:
            parse_error = _scanner_error_to_parse_error(e)
        except ParserSyntaxError as e:
            if verbose_errors:
                logger.debug("parser raised %s", e)
            parse_error = _parser_error_to_parse_error(e, filename)

        parse_errors = _collected_parse_errors(parser, filename)
        if parse_error is None and parse_errors:
            parse_error = parse_errors[0]
        return ParserResult(nodes=ast, parse_error=parse_error, parse_errors=parse_errors)


def _scanner_error_to_parse_error(e: ScannerException) -> ParseError:
    return _build_parse_error_from_position(
        position=e.position,
        message=str(e),
        length=1,
        code=e.code,
        hint=e.hint,
    )


def _parser_error_to_parse_error(e: ParserSyntaxError, fallback_filename: str) -> ParseError:
    length = len(e.token.value) if e.token.value else 1
    if e.token.position is not None:
        return _build_parse_error_from_position(
            position=e.token.position,
            message=str(e),
            length=length,
            code=e.code,
            hint=e.hint,
        )
    return ParseError(
        message=str(e),
        filename=fallback_filename,
        line=0,
        column=0,
        code=e.code,
        hint=e.hint,
    )


def _collected_parse_errors(parser: Parser | None, filename: str) -> list[ParseError] | None:
    if parser is None or not parser.errors:
        return None
    return [_parser_error_to_parse_error(err, filename) for err in parser.errors]


def _build_parse_error_from_position(
    position: Any,
    message: str,
    length: int,
    code: str | None,
    hint: str | None,
) -> ParseError:
    try:
        source_line = position.get_line()
    except (IndexError, AttributeError):
        source_line = ""
    context_before, context_after = _gather_context(position, before=1, after=1)
    return ParseError(
        message=message,
        filename=position.file.filename,
        line=position.line,
        column=position.column,
        source_line=source_line,
        length=length,
        code=code,
        hint=hint,
        context_before=context_before,
        context_after=context_after,
    )


def _gather_context(position: Any, *, before: int, after: int) -> tuple[list[str], list[str]]:
    """Return up to ``before`` lines above and ``after`` lines below ``position``."""
    file = getattr(position, "file", None)
    lines = getattr(file, "lines", None)
    if not lines:
        return [], []
    line_idx = position.line
    before_lines = [lines[i] for i in range(max(0, line_idx - before), line_idx)]
    after_lines = [lines[i] for i in range(line_idx + 1, min(len(lines), line_idx + 1 + after))]
    return before_lines, after_lines
