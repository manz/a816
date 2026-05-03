import argparse
import difflib
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from a816.exceptions import FormattingError
from a816.formatter import A816Formatter

SOURCE_SUFFIXES = {".s", ".i"}

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
    parser = argparse.ArgumentParser(prog="a816 fluff", description="Format a816 assembly sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    format_parser = subparsers.add_parser("format", help="Format .s/.i sources under the given path.")
    format_parser.add_argument(
        "path",
        type=Path,
        help="File or directory to format recursively. Use `-` to read from stdin and write the formatted text to stdout.",
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
        formatted = A816Formatter().format_text(original, "<stdin>")
    except FormattingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.diff:
        if original == formatted:
            return 0
        diff_lines = difflib.unified_diff(
            original.splitlines(keepends=True),
            formatted.splitlines(keepends=True),
            fromfile="<stdin>",
            tofile="<stdin>",
        )
        sys.stdout.write(_colorize_diff(diff_lines))
        return 1
    if args.check:
        if original == formatted:
            return 0
        print("Would reformat <stdin>", file=sys.stderr)
        return 1
    sys.stdout.write(formatted)
    return 0


def _run_format(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    target_path: Path = args.path
    if str(target_path) == "-":
        return _run_format_stdin(args)
    if not target_path.exists():
        parser.error(f"path does not exist: {target_path}")
    sources = list(_discover_sources(target_path))
    if not sources:
        return 0
    computed, err = _format_sources(sources, A816Formatter())
    if err is not None:
        return err
    changed = [item for item in computed if item[1] != item[2]]
    if args.diff:
        return _emit_diff(changed)
    if args.check:
        return _emit_check(changed)
    return _write_formatted(computed)


def fluff_main(argv: Sequence[str] | None = None) -> int:
    parser = _build_fluff_parser()
    args = parser.parse_args(argv)
    if args.command == "format":
        return _run_format(args, parser)
    parser.error("Unknown command")
    return 2  # parser.error never returns, but mypy needs this


if __name__ == "__main__":  # pragma: no cover
    sys.exit(fluff_main())
