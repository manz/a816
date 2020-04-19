import sys
import argparse
import logging

from a816.program import Program

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='a816 usage', epilog='')
    parser.add_argument('--verbose', action='store_true', help='Displays all log levels.')
    parser.add_argument('-o', '--output', dest='output_file', default='a.out', help='Output file')
    parser.add_argument('input_file', help='The asm file to assemble.')
    parser.add_argument('-f', dest='format', default='ips', help='Output format')
    parser.add_argument('-m', dest='mapping', default='low', help='Address Mapping')
    parser.add_argument('--copier-header', action='store_true',
                        help='Adds 0x200 address delta corresponding to copier header in ips writer.')
    parser.add_argument('--dump-symbols', action='store_true',
                        help='Dumps symbol table')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    logger = logging.getLogger('x816')

    program = Program(dump_symbols=args.dump_symbols)

    if args.format == 'ips':
        exit_code = program.assemble_as_patch(args.input_file, args.output_file, args.mapping, args.copier_header)
    else:
        exit_code = program.assemble(args.input_file, args.output_file)
    sys.exit(exit_code)
