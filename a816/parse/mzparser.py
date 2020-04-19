from a816.parse.codegen import code_gen
from a816.parse.errors import ScannerException, ParserSyntaxError
from a816.parse.parser import Parser
from a816.parse.parser_states import parse_initial
from a816.parse.scanner import Scanner
from a816.parse.scanner_states import lex_initial


class MZParser(object):
    def __init__(self, resolver):
        self.resolver = resolver

    def parse(self, program, filename=''):
        ast = self.parse_as_ast(program, filename)
        return code_gen(ast, self.resolver)

    @staticmethod
    def parse_as_ast(program, filename=''):
        scanner = Scanner(lex_initial)

        try:
            tokens = scanner.scan(filename, program)
            parser = Parser(tokens, parse_initial)
            return parser.parse()
        except ScannerException as e:
            position = e.position
            position_str = str(position)
            line = position.get_line()
            print(f'{position_str} :\n{line}')
            print(' ' * position.column + '~')
        except ParserSyntaxError as e:
            print(str(e))
            e.token.display()
