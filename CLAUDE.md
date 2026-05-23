# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

a816 is a 65c816 assembler for Super Famicom/SNES development, designed for ROM hacking and patching. It provides both command-line tools and Python APIs for assembling 65c816 assembly code into various output formats (IPS patches, SFC files, object files).

## Build and Development Commands

### Testing
```bash
# Run all tests with coverage, linting, and type checking
make tests
# Or use hatch directly
hatch run tests:all

# Run only tests
hatch run tests:tests

# Run specific test
pytest tests/test_parse.py

# Coverage report
hatch run tests:coverage
```

### Code Quality
```bash
# Format code and run checks
hatch run tests:format

# Check code style and linting only
hatch run tests:check

# Type checking
hatch run tests:type
```

### Building
```bash
# Build wheel
make wheels

# Build standalone binary with Nuitka
make binary-nuitka

# Clean build artifacts
make clean
```

### Development Environment
```bash
# Create/recreate development environment
make env
```

## Core Architecture

### Main Components

1. **Program (a816/program.py)**: Central orchestrator that manages the entire assembly process including parsing, symbol resolution, and code emission

2. **MZParser (a816/parse/mzparser.py)**: High-level parser interface that coordinates scanning, parsing, and code generation

3. **Scanner/Parser Pipeline**: 
   - Scanner converts source text to tokens using state machines
   - Parser converts tokens to AST using recursive descent parsing
   - Code generator transforms AST to executable nodes

4. **Symbol Resolution (a816/symbols.py)**: 
   - Manages scopes, labels, and symbol tables
   - Handles 65c816 address mapping (low/high ROM)
   - Resolves forward references through multiple passes

5. **CPU Emulation (a816/cpu/)**: 
   - Models 65c816 instruction set and addressing modes
   - Handles address mapping between logical and physical addresses
   - Supports different ROM types (low_rom, low_rom_2, high_rom)

6. **Writers (a816/writers.py)**: Output format handlers
   - IPSWriter: Creates IPS patch files
   - SFCWriter: Creates SFC ROM files

7. **Linker (a816/linker.py)**: Links multiple object files together, resolving symbols and applying relocations

### Processing Flow

1. **Scanning**: Source text → Tokens (using state machine in scanner_states.py)
2. **Parsing**: Tokens → AST (using recursive descent in parser_states.py) 
3. **Code Generation**: AST → Executable Nodes (in codegen.py)
4. **Symbol Resolution**: Multi-pass resolution of labels and symbols
5. **Emission**: Final code generation with chosen Writer

### Key Concepts

- **Scopes**: Hierarchical symbol namespaces supporting nested and named scopes
- **Address Mapping**: Logical addresses mapped to physical ROM addresses based on cartridge type
- **Multi-pass Assembly**: Forward references resolved through multiple passes
- **Macro Support**: Parameterized code expansion
- **Memory Management**: Position-dependent code with relocation support

## CLI Tools

- `a816`/`x816`: Main assembler CLI with separate compilation support
- `a816-lsp-server`: Language Server Protocol implementation

### Separate Compilation Workflow

The assembler now supports separate compilation and linking:

```bash
# Compile individual files to object files
a816 --compile-only file1.s file2.s  # Creates file1.o, file2.o

# Link object files to create final output
a816 file1.o file2.o -o output.ips   # Creates IPS patch
a816 file1.o file2.o -f sfc -o output.sfc  # Creates SFC file

# Mixed compilation and linking in one step
a816 file1.s file2.o -o output.ips   # Compiles file1.s, links with file2.o
```

## Testing Strategy

Tests are organized by component:
- Parser tests: `test_parse.py`, `test_ast.py`
- Code generation: `test_code_gen.py`
- Symbol resolution: `test_resolver.py`  
- Linker: `test_linker.py`
- Object files: `test_object_file.py`
- Separate compilation: `test_separate_compilation.py`

The project uses pytest with coverage reporting and includes sample assembly files in `tests/samples/`.

## External Symbol Support

Cross-file symbol references are now fully supported using the `.extern` directive:

```ca65
; In file1.s - declare external symbols
.extern external_func

main:
    lda #0x42
    jsr.w external_func  ; Use .w to specify word addressing
    rts

; In file2.s - define the external symbol  
external_func:
    sta 0x2000
    rts
```

**Workflow:**
1. Declare external symbols with `.extern symbol_name`
2. Compile each file separately: `a816 --compile-only file1.s file2.s`
3. Link together: `a816 file1.o file2.o -o output.ips`

The linker will verify all external symbols are resolved and report errors for missing symbols.

## Implementation Notes

**Key Features:**
- ✅ External symbol declarations with `.extern` directive
- ✅ Cross-file symbol resolution during linking  
- ✅ Linker validation of symbol dependencies
- ✅ Support for both separate compilation and direct linking workflows