"""Renders departures as an 800x480 1-bit BMP that the current firmware can show as-is.

The layout is portrait (480 wide x 800 tall), rotated 90 degrees counter-clockwise into the
panel's native landscape frame, matching UCOP's image. drawBmpFromUrl() in the firmware writes
the BMP straight to the panel, so the orientation has to match.
"""

from __future__ import annotations

import datetime as dt
import io
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .departures import SRC_ORIGIN, SRC_SCHEDULE, Departure

W, H = 480, 800  # portrait layout
MARGIN = 16
HEADER_H = 96
FOOTER_H = 34
BLACK, WHITE = 0, 255


def _font(size: int) -> ImageFont.FreeTypeFont:
    # Pillow's built-in scalable font: no font files needed on the Pi
    return ImageFont.load_default(size=size)


def _clock(epoch: int, tz: ZoneInfo) -> str:
    t = dt.datetime.fromtimestamp(epoch, tz)
    return t.strftime("%I:%M %p").lstrip("0").lower()


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "...", font=font) > max_w:
        text = text[:-1]
    return text.rstrip() + "..."


def time_text(d: Departure, now: int, tz: ZoneInfo) -> str:
    mins = (d.est - now) // 60
    if mins <= 0:
        return "Due"
    if mins < 60:
        return f"{mins} min"
    return _clock(d.est, tz)


def status_text(d: Departure, tz: ZoneInfo) -> str:
    if d.src == SRC_SCHEDULE:
        return "scheduled" + (" (no live data)" if d.stale_vehicle else "")
    delay_min = round(d.delay_s / 60)
    prefix = "~" if d.src == SRC_ORIGIN else ""
    if delay_min == 0:
        rel = "on time"
    elif delay_min > 0:
        rel = f"{delay_min} min late"
    else:
        rel = f"{-delay_min} min early"
    return f"{prefix}{_clock(d.est, tz)}, {rel}"


def render_board(
    stop_code: str, stop_name: str, departures: list[Departure], now: int, tz: ZoneInfo, rows: int = 8
) -> Image.Image:
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    draw.text((MARGIN, 14), f"STOP {stop_code}", font=_font(18), fill=BLACK)
    draw.text((MARGIN, 38), _fit(draw, stop_name, _font(38), W - 2 * MARGIN), font=_font(38), fill=BLACK)
    draw.line((MARGIN, HEADER_H - 4, W - MARGIN, HEADER_H - 4), fill=BLACK, width=3)

    row_h = (H - HEADER_H - FOOTER_H) // rows
    route_font, head_font, time_font, small_font = _font(26), _font(28), _font(34), _font(17)
    for i, d in enumerate(departures[:rows]):
        y = HEADER_H + i * row_h
        # route badge
        badge_w = max(52, int(draw.textlength(d.route, font=route_font)) + 16)
        draw.rectangle((MARGIN, y + 8, MARGIN + badge_w, y + 42), fill=BLACK)
        draw.text((MARGIN + badge_w / 2, y + 25), d.route, font=route_font, fill=WHITE, anchor="mm")
        # time, right aligned
        tt = time_text(d, now, tz)
        draw.text((W - MARGIN, y + 6), tt, font=time_font, fill=BLACK, anchor="ra")
        tt_w = draw.textlength(tt, font=time_font)
        # headsign between badge and time
        hx = MARGIN + badge_w + 12
        draw.text((hx, y + 10), _fit(draw, d.headsign, head_font, int(W - MARGIN - tt_w - 12 - hx)),
                  font=head_font, fill=BLACK)
        draw.text((W - MARGIN, y + row_h - 10), status_text(d, tz), font=small_font, fill=BLACK, anchor="rd")
        if i < rows - 1:
            draw.line((MARGIN, y + row_h - 2, W - MARGIN, y + row_h - 2), fill=BLACK, width=1)

    if not departures:
        draw.text((W / 2, H / 2), "No departures in the next few hours", font=_font(24), fill=BLACK, anchor="mm")

    fy = H - FOOTER_H + 8
    draw.line((MARGIN, fy - 6, W - MARGIN, fy - 6), fill=BLACK, width=1)
    draw.text((MARGIN, fy), f"as of {_clock(now, tz)}", font=small_font, fill=BLACK)
    draw.text((W - MARGIN, fy), "~ estimated from first stop", font=small_font, fill=BLACK, anchor="ra")

    # Hard threshold (no dithering) so text edges stay crisp on the 1-bit panel
    return img.point(lambda p: 255 if p > 128 else 0).convert("1", dither=Image.Dither.NONE)


def to_panel_bmp(portrait: Image.Image) -> bytes:
    """Rotate the portrait layout into the 800x480 frame and encode as an uncompressed 1-bit BMP."""
    native = portrait.rotate(90, expand=True)
    assert native.size == (800, 480), native.size
    buf = io.BytesIO()
    native.save(buf, format="BMP")
    return buf.getvalue()


def panel_bmp_to_portrait_png(bmp: bytes) -> bytes:
    """For viewing in a browser: turn a panel BMP (ours or UCOP's) upright."""
    img = Image.open(io.BytesIO(bmp)).rotate(-90, expand=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
