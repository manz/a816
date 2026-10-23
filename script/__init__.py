"""Deprecated: the script package moved to `katsuji.script` (katsuji 0.4).

This package forwards to it so existing imports keep working, and goes away
in a816 1.3. `script.Table` is `katsuji.script.Table`, so tables built
either way are accepted by `.table`.
"""

import warnings

from katsuji.script import Table

__all__ = ["Table"]

warnings.warn(
    "`script` moved to `katsuji.script`: import it from there (a816 1.3 drops `script`)",
    DeprecationWarning,
    stacklevel=2,
)
