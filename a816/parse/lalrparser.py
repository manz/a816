# coding: utf-8
import pprint

from a816.parse.ast import code_gen
from a816.parse.parser import A816Parser


class LALRParser(object):
    def __init__(self, resolver):
        self.resolver = resolver
        self.parser = A816Parser()

    def parse(self, program):
        ast_nodes = self.parse_as_ast(program)
        return code_gen(ast_nodes[1:], self.resolver)

    def parse_as_ast(self, program):
        cloned_parser = self.parser.clone('')
        return cloned_parser.parse(program)