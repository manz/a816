#!/usr/bin/env python3.4

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

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    logger = logging.getLogger('x816')

    program = Program()
    if args.format == 'ips':
        exit_code = program.assemble_as_patch(args.input_file, args.output_file, args.mapping)
    else:
        exit_code = program.assemble(args.input_file, args.output_file)
    sys.exit(exit_code)
