from collections import namedtuple

from a816.parse.codegen import code_gen
from a816.parse.errors import ScannerException, ParserSyntaxError
from a816.parse.parser import Parser
from a816.parse.parser_states import parse_initial
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial

ParserResult = namedtuple('ParserResult', ['ast', 'error'])


class MZParser(object):
    def __init__(self, resolver):
        self.resolver = resolver

    def parse(self, program, filename=''):
        ast = self.parse_as_ast(program, filename)
        return code_gen(ast.ast, self.resolver)

    @staticmethod
    def parse_as_ast(program, filename='') -> ParserResult:
        scanner = Scanner(lex_initial)
        ast = None
        error = None

        try:
            tokens = scanner.scan(filename, program)
            parser = Parser(tokens, parse_initial)
            ast = parser.parse()
        except ScannerException as e:
            position = e.position
            position_str = str(position)
            line = position.get_line()
            error = f'{position_str} :\n{line}\n ' * position.column + '~'
        except ParserSyntaxError as e:
            error = e.token.trace()
        print(error)
        return ParserResult(ast=ast, error=error)
