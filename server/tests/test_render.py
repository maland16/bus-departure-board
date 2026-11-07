import io
import struct

from PIL import Image

from busboard.departures import Departure
from busboard.payload import build_payload
from busboard.render import panel_bmp_to_portrait_png, render_board, to_panel_bmp

from conftest import TZ, local

NOW = local(2026, 11, 6, 18, 0)


def deps():
    return [
        Departure("20261106", f"T{i}", "0:1117", 2, "4", r, "", h, False, NOW + 300 * i, NOW + 300 * i + 60 * i,
                  i % 3, None, None, False, False, False)
        for i, (r, h) in enumerate([("24A", "Gallery Blvd & Gallery Walmart and a very long name"), ("BRZ", "Brunswick"),
                                    ("4", "Metro Pulse")] * 3, start=1)
    ]


def test_bmp_is_what_the_firmware_accepts():
    bmp = to_panel_bmp(render_board("1117", "Congress St + Center St", deps(), NOW, TZ))
    # Same header checks as downloadBmpToFramebuffer() in display-driver.cpp
    assert bmp[:2] == b"BM"
    data_offset = struct.unpack_from("<I", bmp, 10)[0]
    dib_size, width, height, _, bpp, compression = struct.unpack_from("<IiiHHI", bmp, 14)
    assert dib_size >= 40 and (width, abs(height)) == (800, 480)
    assert bpp == 1 and compression == 0
    assert len(bmp) == data_offset + 100 * 480  # 800 px / 8 = 100 bytes per row, no padding needed


def test_preview_is_upright():
    bmp = to_panel_bmp(render_board("1117", "Congress St + Center St", [], NOW, TZ))
    img = Image.open(io.BytesIO(panel_bmp_to_portrait_png(bmp)))
    assert img.size == (480, 800)


def test_payload_shape():
    p = build_payload("1117", "Congress St + Center St", deps()[:2], NOW, TZ)
    assert p["v"] == 1 and p["now"] == NOW and len(p["deps"]) == 2
    assert p["tz"] == -5 * 3600 and p["live"] is True  # EST in November
    assert set(p["deps"][0]) == {"r", "h", "t", "s", "src"}
