# coding: utf-8
import pprint

from a816.parse.ast import code_gen
from a816.parse.parser import A816Parser


class LALRParser(object):
    def __init__(self, resolver):
        self.resolver = resolver

    def parse(self, program):
        parser = A816Parser()
        ast_nodes = parser.parse(program)
        print('-'*40)
        pprint.pprint(ast_nodes)
        return code_gen(ast_nodes[1:], self.resolver)
