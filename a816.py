import argparse
from matchers import ImmediateMatcher, DirectMatcher, DirectIndexedMatcher, IndirectMatcher, IndirectIndexedMatcher
from symbols import SymbolResolver


class Program(object):
    def __init__(self):
        self.resolver = SymbolResolver()

        self.matchers = [
            ImmediateMatcher(self.resolver),
            DirectMatcher(self.resolver),
            DirectIndexedMatcher(self.resolver),
            IndirectMatcher(self.resolver),
            IndirectIndexedMatcher(self.resolver)
        ]

    def parse(self, program):
        parsed_list = []

        for line in program:
            line = line.strip()
            for matcher in self.matchers:
                node = matcher.parse(line)
                if node:
                    parsed_list.append(node)
                    continue
        return parsed_list


def main():
    print('Welcome to a816')
    parser = argparse.ArgumentParser(description='a816 Arguments parser', epilog='')

    input_program = ['rep #0x20',
                     'lda.w #0x2000',
                     'lda.b #$00',
                     'lda.w #label',
                     'lda #0x02',
                     'lda [0x12], X',
                     'lda.l [0x000001]',
                     'lda $00,x',
                     'jmp label']

    program = Program()
    nodes = program.parse(input_program)

    for node in nodes:
        print(node)

if __name__ == '__main__':
    main()




        # parser.add_argument('--verbose', action='store_true', help='Displays all log levels.')
        # parser.add_argument('--branch', dest='branch', help='The branch to checkout for the project')
