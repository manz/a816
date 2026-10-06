"""Top-level patch."""

.import "vwf"

.alloc boot at 0x008000 {
main:
"""Entry: run the VWF init, then loop."""
    jsl vwf.init
    bra main
}
