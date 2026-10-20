"""`a816 ci`: the steps behind the shared GitHub actions (`manz/a816/actions/*`).

Each action is a thin `action.yml` that maps its inputs onto one
`a816 ci <step>` call, so the logic lives here, tested like the rest of a816,
and ships with the a816 the project locks.
"""

from a816.ci.cli import ci_main

__all__ = ["ci_main"]
