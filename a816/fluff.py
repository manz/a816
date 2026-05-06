import argparse
import difflib
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from a816.exceptions import FormattingError
from a816.fluff_lint import lint_file
from a816.formatter import A816Formatter

SOURCE_SUFFIXES = {".s", ".i"}
STDIN_LABEL = "<stdin>"

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"


def _discover_sources(path: Path) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in SOURCE_SUFFIXES:
            yield path
        return

    if path.is_dir():
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and candidate.suffix.lower() in SOURCE_SUFFIXES:
                yield candidate


def _colorize_diff(diff_lines: Iterable[str]) -> str:
    colored: list[str] = []
    for line in diff_lines:
        if line.startswith("@@"):
            colored.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("+++") or line.startswith("---"):
            colored.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("+"):
            colored.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            colored.append(f"{RED}{line}{RESET}")
        else:
            colored.append(line)
    return "".join(colored)


def _build_fluff_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a816 fluff", description="Format and lint a816 assembly sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Run lint rules (DOC001 / DOC002 / E501 / N801 / N802).")
    check_parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Files or directories to lint. Directories are walked for .s / .i sources.",
    )
    format_parser = subparsers.add_parser("format", help="Format .s/.i sources under the given path.")
    format_parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help=(
            "One or more files or directories to format. Directories are walked"
            " recursively for .s / .i sources. Use `-` (alone) to read from stdin"
            " and write the formatted text to stdout."
        ),
    )
    format_parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write changes; exit non-zero if files would be reformatted.",
    )
    format_parser.add_argument(
        "--diff",
        action="store_true",
        help="Show unified diff per changed file and exit non-zero if any differ.",
    )
    return parser


def _format_sources(sources: list[Path], formatter: A816Formatter) -> tuple[list[tuple[Path, str, str]], int | None]:
    """Format every source. Returns (computed, error_code). error_code is set on failure."""
    computed: list[tuple[Path, str, str]] = []
    for path in sources:
        original = path.read_text(encoding="utf-8")
        try:
            formatted = formatter.format_text(original, str(path))
        except FormattingError as exc:
            print(str(exc), file=sys.stderr)
            return computed, 2
        computed.append((path, original, formatted))
    return computed, None


def _emit_diff(changed: list[tuple[Path, str, str]]) -> int:
    if not changed:
        print("All files are formatted correctly.")
        return 0
    for path, original, formatted in changed:
        diff_lines = difflib.unified_diff(
            original.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
        diff_text = _colorize_diff(diff_lines)
        if diff_text:
            if not diff_text.endswith("\n"):
                diff_text += "\n"
            print(diff_text, end="")
    print(f"Would reformat {len(changed)} file(s).")
    return 1


def _emit_check(changed: list[tuple[Path, str, str]]) -> int:
    if not changed:
        print("All files are formatted correctly.")
        return 0
    for path, _, _ in changed:
        print(f"Would reformat {path}")
    print(f"Would reformat {len(changed)} file(s).")
    return 1


def _write_formatted(computed: list[tuple[Path, str, str]]) -> int:
    formatted_count = 0
    for path, original, formatted in computed:
        if formatted == original:
            continue
        try:
            path.write_text(formatted, encoding="utf-8")
        except OSError as exc:
            print(f"Failed to write formatted output for {path}: {exc}", file=sys.stderr)
            return 2
        formatted_count += 1
    print(f"Formatted {formatted_count} file(s).")
    return 0


def _run_format_stdin(args: argparse.Namespace) -> int:
    """Read source from stdin, write formatted text to stdout.

    --check / --diff still work: --check prints `Would reformat <stdin>`
    and exits 1 if formatting changes the input; --diff emits a unified
    diff. Otherwise the formatted text is written to stdout.
    """
    original = sys.stdin.read()
    try:
        formatted = A816Formatter().format_text(original, STDIN_LABEL)
    except FormattingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.diff:
        if original == formatted:
            return 0
        diff_lines = difflib.unified_diff(
            original.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile=STDIN_LABEL,
            tofile=STDIN_LABEL,
        )
        sys.stdout.write(_colorize_diff(diff_lines))
        return 1
    if args.check:
        if original == formatted:
            return 0
        print(f"Would reformat {STDIN_LABEL}", file=sys.stderr)
        return 1
    sys.stdout.write(formatted)
    return 0


def _collect_sources(paths: list[Path]) -> tuple[list[Path], int]:
    """Resolve every input path to a deduplicated list of source files.

    Returns (sources, error_code). error_code is 2 if any path is missing
    (after also reporting it on stderr), 0 otherwise. Missing paths do
    not abort the walk so the user sees one report covering every input.
    """
    seen: set[Path] = set()
    sources: list[Path] = []
    error_code = 0
    for raw in paths:
        if not raw.exists():
            print(f"path not found: {raw}", file=sys.stderr)
            error_code = 2
            continue
        for candidate in _discover_sources(raw):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            sources.append(candidate)
    return sources, error_code


def _run_format(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    paths: list[Path] = list(args.paths)
    has_stdin = any(str(p) == "-" for p in paths)
    if has_stdin:
        if len(paths) != 1:
            parser.error("`-` (stdin) cannot be combined with other paths")
        return _run_format_stdin(args)

    sources, missing_err = _collect_sources(paths)
    if not sources:
        return missing_err
    computed, err = _format_sources(sources, A816Formatter())
    if err is not None:
        return err
    changed = [item for item in computed if item[1] != item[2]]
    if args.diff:
        return _emit_diff(changed) or missing_err
    if args.check:
        return _emit_check(changed) or missing_err
    return _write_formatted(computed) or missing_err


def _run_check(args: argparse.Namespace) -> int:
    sources, missing_err = _collect_sources(list(args.paths))
    if not sources:
        return missing_err
    total = 0
    for source in sources:
        for diag in lint_file(source):
            print(diag.format())
            total += 1
    if total:
        print(f"{total} lint hit(s).", file=sys.stderr)
        return missing_err or 1
    return missing_err


def fluff_main(argv: Sequence[str] | None = None) -> int:
    parser = _build_fluff_parser()
    args = parser.parse_args(argv)
    if args.command == "format":
        return _run_format(args, parser)
    if args.command == "check":
        return _run_check(args)
    parser.error("Unknown command")
    return 2  # parser.error never returns, but mypy needs this


if __name__ == "__main__":  # pragma: no cover
    sys.exit(fluff_main())
