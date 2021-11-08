import unittest


class MacroTests(unittest.TestCase):
    def test_recursive_macros(self) -> None:
        program = """
        .macro recursive(length) {
        .if length > 0 {
            .db length
            recursive(length - 1)
        } else {
            .db length
        }
        """
