"""Boot: hand the sound driver to the IPL upload."""

.alloc sound_image at 0x018000 {
driver_image:
    .embed "sound/driver"
}

.alloc boot at 0x008000 {
upload_args:
"""Load the upload arguments (A16, X16): A size, X ARAM base, Y entry."""
    lda.w #driver.__size
    ldx.w #driver.__base
    ldy.w #driver.start
    rts
}
