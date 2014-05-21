#!/usr/bin/env python3.4
import argparse
import logging

from a816.program import Program


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='a816 Arguments parser', epilog='')
    parser.add_argument('--verbose', action='store_true', help='Displays all log levels.')
    parser.add_argument('-o', '--output', dest='output_file', default='a.out', help='Output file')
    parser.add_argument('input_file', help='The asm file to assemble.')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    logger = logging.getLogger('x816')

    program = Program()
    exit_code = program.assemble_as_patch(args.input_file, args.output_file)
    exit(exit_code)
