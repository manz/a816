"""Coverage for `a816 explain`: rule docs are populated and round-trip through the linter."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from a816.fluff import fluff_main
from a816.fluff_lint import Rule, lint_text


@pytest.mark.parametrize("code", sorted(Rule.registry))
def test_rule_has_rationale(code: str) -> None:
    rule = Rule.registry[code]
    assert rule.rationale.strip(), f"{code} has empty rationale"


@pytest.mark.parametrize("code", [c for c in sorted(Rule.registry) if c != "E501"])
def test_rule_has_examples(code: str) -> None:
    """Every rule (except E501, which is a width threshold) must ship a bad / good pair."""
    rule = Rule.registry[code]
    assert rule.bad.strip(), f"{code} has empty `bad` example"
    assert rule.good.strip(), f"{code} has empty `good` example"


@pytest.mark.parametrize("code", [c for c in sorted(Rule.registry) if c != "E501"])
def test_bad_example_triggers_its_rule(code: str) -> None:
    """The `bad` snippet must produce at least one diagnostic with this rule's code."""
    rule = Rule.registry[code]
    diags = lint_text(rule.bad, Path("inline.s"))
    assert any(d.code == code for d in diags), (
        f"{code} bad example does not trigger {code}: {[(d.code, d.message) for d in diags]}"
    )


@pytest.mark.parametrize("code", [c for c in sorted(Rule.registry) if c != "E501"])
def test_good_example_does_not_trigger_its_rule(code: str) -> None:
    """The `good` snippet must not produce any diagnostic with this rule's code."""
    rule = Rule.registry[code]
    diags = lint_text(rule.good, Path("inline.s"))
    assert all(d.code != code for d in diags), (
        f"{code} good example still triggers {code}: {[(d.code, d.message) for d in diags]}"
    )


class CLIExplainTestCase(TestCase):
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            rc = fluff_main(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_known_code_prints_rationale(self) -> None:
        rc, stdout, _ = self._run(["explain", "DOC003"])
        self.assertEqual(rc, 0)
        self.assertIn("DOC003", stdout)
        self.assertIn("Bad:", stdout)
        self.assertIn("Good:", stdout)

    def test_lowercase_code_accepted(self) -> None:
        rc, stdout, _ = self._run(["explain", "doc004"])
        self.assertEqual(rc, 0)
        self.assertIn("DOC004", stdout)

    def test_unknown_code_exits_two(self) -> None:
        rc, _, stderr = self._run(["explain", "DOC999"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown rule", stderr)
