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


def fluff_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="a816 fluff", description="Format a816 assembly sources.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    format_parser = subparsers.add_parser("format", help="Format .s/.i sources under the given path.")
    format_parser.add_argument("path", type=Path, help="File or directory to format recursively.")
    format_parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write changes; exit with non-zero status if files would be reformatted.",
    )
    format_parser.add_argument(
        "--diff",
        action="store_true",
        help="Show a unified diff for each file that would be reformatted and exit with non-zero status if any differ.",
    )

    args = parser.parse_args(argv)

    if args.command == "format":
        target_path: Path = args.path
        if not target_path.exists():
            parser.error(f"path does not exist: {target_path}")

        formatter = A816Formatter()
        sources = list(_discover_sources(target_path))

        if not sources:
            return 0

        computed: list[tuple[Path, str, str]] = []
        changed: list[tuple[Path, str, str]] = []
        for path in sources:
            original = path.read_text(encoding="utf-8")
            try:
                formatted = formatter.format_text(original, str(path))
            except FormattingError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            computed.append((path, original, formatted))
            if formatted != original:
                changed.append((path, original, formatted))

        if args.diff:
            if changed:
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
            print("All files are formatted correctly.")
            return 0

        if args.check:
            if changed:
                for path, _, _ in changed:
                    print(f"Would reformat {path}")
                print(f"Would reformat {len(changed)} file(s).")
                return 1
            print("All files are formatted correctly.")
            return 0

        formatted_count = 0
        for path, original, formatted in computed:
            if formatted != original:
                try:
                    path.write_text(formatted, encoding="utf-8")
                except OSError as exc:
                    print(f"Failed to write formatted output for {path}: {exc}", file=sys.stderr)
                    return 2
                formatted_count += 1
        print(f"Formatted {formatted_count} file(s).")
        return 0

    parser.error("Unknown command")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(fluff_main())
