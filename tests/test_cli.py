"""Tests for the CLI module."""

import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch


class CLITestCase(TestCase):
    """Test the a816/x816 CLI entry point."""

    def _run_cli(self, args: list[str]) -> tuple[int, str, str]:
        """Run CLI with given arguments, return (exit_code, stdout, stderr)."""
        from a816.cli import cli_main

        stdout_capture = StringIO()
        stderr_capture = StringIO()

        exit_code: int = 0
        with (
            patch.object(sys, "argv", ["x816"] + args),
            patch.object(sys, "stdout", stdout_capture),
            patch.object(sys, "stderr", stderr_capture),
        ):
            try:
                cli_main()
            except SystemExit as e:
                exit_code = int(e.code) if e.code is not None else 0

        return exit_code, stdout_capture.getvalue(), stderr_capture.getvalue()

    def test_compile_only_creates_object_file(self) -> None:
        """Test that --compile-only creates .o files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            exit_code, stdout, stderr = self._run_cli(["-c", str(asm_file)])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")

            # Check that .o file was created
            obj_file = Path(tmpdir) / "test.o"
            self.assertTrue(obj_file.exists(), "Object file was not created")

    def test_link_object_file_to_ips(self) -> None:
        """Test linking an object file to IPS format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and compile an assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            # Compile to object file
            exit_code, _, stderr = self._run_cli(["-c", str(asm_file)])
            self.assertEqual(exit_code, 0, f"Compile failed: {stderr}")

            # Link object file to IPS
            obj_file = Path(tmpdir) / "test.o"
            ips_file = Path(tmpdir) / "output.ips"

            exit_code, _, stderr = self._run_cli([str(obj_file), "-o", str(ips_file), "-f", "ips"])

            self.assertEqual(exit_code, 0, f"Link failed: {stderr}")
            self.assertTrue(ips_file.exists(), "IPS file was not created")

            # Verify IPS header
            with open(ips_file, "rb") as f:
                header = f.read(5)
            self.assertEqual(header, b"PATCH", "Invalid IPS header")

    def test_link_object_file_to_sfc(self) -> None:
        """Test linking an object file to SFC format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and compile an assembly file
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            # Compile to object file
            self._run_cli(["-c", str(asm_file)])

            # Link object file to SFC
            obj_file = Path(tmpdir) / "test.o"
            sfc_file = Path(tmpdir) / "output.sfc"

            exit_code, _, stderr = self._run_cli([str(obj_file), "-o", str(sfc_file), "-f", "sfc"])

            self.assertEqual(exit_code, 0, f"Link failed: {stderr}")
            self.assertTrue(sfc_file.exists(), "SFC file was not created")

    def test_compile_and_link_asm_file_directly(self) -> None:
        """Test compiling and linking an asm file in one step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            ips_file = Path(tmpdir) / "output.ips"

            exit_code, _, stderr = self._run_cli([str(asm_file), "-o", str(ips_file)])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")
            self.assertTrue(ips_file.exists(), "IPS file was not created")

    def test_defines_symbol(self) -> None:
        """Test that -D flag defines symbols."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a table file for text encoding
            tbl_file = Path(tmpdir) / "test.tbl"
            tbl_file.write_text("41=A\n42=B\n43=C\n", encoding="utf-8")

            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                f""".table "{tbl_file}"
*= 0x8000
main:
    .text "${{MY_TEXT}}"
""",
                encoding="utf-8",
            )

            ips_file = Path(tmpdir) / "output.ips"

            exit_code, _, stderr = self._run_cli([str(asm_file), "-o", str(ips_file), "-D", "MY_TEXT=ABC"])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")

    def test_invalid_file_type_error(self) -> None:
        """Test error handling for invalid file types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "test.txt"
            bad_file.write_text("not an assembly file", encoding="utf-8")

            exit_code, _, _ = self._run_cli([str(bad_file)])

            self.assertNotEqual(exit_code, 0, "Should fail for invalid file type")

    def test_compile_only_invalid_file_type_error(self) -> None:
        """Test error handling for compile-only with invalid file types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "test.txt"
            bad_file.write_text("not an assembly file", encoding="utf-8")

            exit_code, _, _ = self._run_cli(["-c", str(bad_file)])

            self.assertNotEqual(exit_code, 0, "Should fail for invalid file type in compile-only mode")

    def test_link_multiple_object_files(self) -> None:
        """Test linking multiple object files together."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create first assembly file
            asm1 = Path(tmpdir) / "file1.s"
            asm1.write_text(
                """*= 0x8000
main:
    lda #0x01
    rts
""",
                encoding="utf-8",
            )

            # Create second assembly file
            asm2 = Path(tmpdir) / "file2.s"
            asm2.write_text(
                """*= 0x9000
helper:
    lda #0x02
    rts
""",
                encoding="utf-8",
            )

            # Compile both
            self._run_cli(["-c", str(asm1), str(asm2)])

            obj1 = Path(tmpdir) / "file1.o"
            obj2 = Path(tmpdir) / "file2.o"

            self.assertTrue(obj1.exists(), "First object file was not created")
            self.assertTrue(obj2.exists(), "Second object file was not created")

            # Link them together
            ips_file = Path(tmpdir) / "output.ips"

            exit_code, _, stderr = self._run_cli([str(obj1), str(obj2), "-o", str(ips_file)])

            self.assertEqual(exit_code, 0, f"Link failed: {stderr}")
            self.assertTrue(ips_file.exists(), "IPS file was not created")

    def test_copier_header_flag(self) -> None:
        """Test that --copier-header flag is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            ips_file = Path(tmpdir) / "output.ips"

            exit_code, _, stderr = self._run_cli([str(asm_file), "-o", str(ips_file), "--copier-header"])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")

    def test_unknown_format_error(self) -> None:
        """Test error handling for unknown output format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            exit_code, _, _ = self._run_cli([str(asm_file), "-f", "xyz"])

            self.assertNotEqual(exit_code, 0, "Should fail for unknown format")

    def test_syntax_error_formatted_output(self) -> None:
        """Test that syntax errors produce formatted output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda invalid syntax here
""",
                encoding="utf-8",
            )

            exit_code, _, _ = self._run_cli([str(asm_file)])

            self.assertNotEqual(exit_code, 0, "Should fail for syntax error")

    def test_verbose_flag(self) -> None:
        """Test that --verbose flag is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
main:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            exit_code, _, stderr = self._run_cli(["--verbose", str(asm_file)])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")

    def test_dump_symbols_flag(self) -> None:
        """Test that --dump-symbols flag is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_file = Path(tmpdir) / "test.s"
            asm_file.write_text(
                """*= 0x8000
my_label:
    lda #0x42
    rts
""",
                encoding="utf-8",
            )

            exit_code, _, stderr = self._run_cli(["--dump-symbols", str(asm_file)])

            self.assertEqual(exit_code, 0, f"CLI failed: {stderr}")

    def test_mixed_asm_and_object_files(self) -> None:
        """Test linking a mix of .s and .o files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and compile first file to .o
            asm1 = Path(tmpdir) / "file1.s"
            asm1.write_text(
                """*= 0x8000
func1:
    lda #0x01
    rts
""",
                encoding="utf-8",
            )
            self._run_cli(["-c", str(asm1)])
            obj1 = Path(tmpdir) / "file1.o"

            # Create second file (don't compile)
            asm2 = Path(tmpdir) / "file2.s"
            asm2.write_text(
                """*= 0x9000
func2:
    lda #0x02
    rts
""",
                encoding="utf-8",
            )

            # Link .o and .s together
            ips_file = Path(tmpdir) / "output.ips"
            exit_code, _, stderr = self._run_cli([str(obj1), str(asm2), "-o", str(ips_file)])

            self.assertEqual(exit_code, 0, f"Link failed: {stderr}")
            self.assertTrue(ips_file.exists(), "IPS file was not created")
