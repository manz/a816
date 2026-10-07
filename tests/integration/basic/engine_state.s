"""
The engine's WRAM state, reserved once: the tilemap and palette shadows
and their dirty flags (`Engine` in `preamble.s`). Modules that touch it
import this module and write `engine_state.<field>`.
"""


.import "preamble"

.reserve engine_state as Engine in wram
