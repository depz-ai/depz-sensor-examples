#!/usr/bin/env python3
"""Lab 4 — The first depth map (VL53L8CH time-of-flight matrix).

The ultrasonic sensor answered with one number. This one answers with 64 at a
time: an 8x8 grid of little laser rangefinders looking through a shared lens,
each covering its own narrow slice of the scene.

Two things bite everyone on the first run, and this lab is about both.

Cells with nothing to reflect off do not report "nothing" — they report a
number, and the number is garbage: a zero, a negative, or a plausible-looking
two metres. The frame carries a status byte per cell saying whether the reading
means anything, and that byte is not optional reading.

And the grid is angular, not flat. The cells fan out from the lens, so a wall
that really is flat does not have to arrive as 64 equal numbers. Whether it does
is a fact about the sensor, and --flat measures it against a tape.

Run:
    python labs/04_tof_depth_map/lab4_tof.py                 live map in a window
    python labs/04_tof_depth_map/lab4_tof.py --raw           same, validity filter off
    python labs/04_tof_depth_map/lab4_tof.py --truth 1.000   centre against a tape
    python labs/04_tof_depth_map/lab4_tof.py --flat          point at a flat wall
    python labs/04_tof_depth_map/lab4_tof.py --terminal      text map, for ssh
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

try:
    import numpy as np
    from depz_sensor_sdk import (
        DepzError,
        NoDepzDeviceError,
        Vl53l8Cx,
        open_device,
    )
except ImportError:  # the SDK lives in the project venv, not in system Python
    raise SystemExit(
        "depz_sensor_sdk is missing — you are probably running system Python.\n"
        "Use the project environment:\n"
        "    .venv/bin/python labs/04_tof_depth_map/lab4_tof.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

SIDE = 8            # 8x8 zones — the full resolution of this sensor
ZONES = SIDE * SIDE
RANGING_HZ = 15     # the maximum at 8x8; 4x4 can go to 60

# The lens sees a square 45° by 45°, split evenly between the zones. Everything
# geometric in this lab comes out of these two numbers.
FOV_DEG = 45.0
ZONE_DEG = FOV_DEG / SIDE

# Status codes a cell can carry. ST's driver documents fourteen of them; these
# are the ones that actually turn up on the bench, and the rest fall through to
# "status N" rather than pretending to be understood.
STATUS_TEXT = {
    4: "consistency failed",
    5: "valid",
    6: "first frame",       # the wrap-around check has no previous frame yet
    9: "valid, wide pulse",  # two surfaces merged into one blob, still usable
    10: "valid, new target",
    12: "blurred by glass",
    255: "no target",
}

# The same thing in the width of a tile. A cell with nothing to say still has to
# say why, and "consisten" cut off mid-word says nothing at all.
STATUS_TILE = {
    4: "unstable",
    6: "first",
    10: "new",
    12: "glass",
    255: "empty",
}

# The 64 zones do not arrive laid out the way a person looking at the scene
# would lay them out. Zone 0 sits at the sensor die's own corner, and on this
# board that corner lands on the right of the field, with the index running
# top to bottom — so the raw grid is the scene turned a quarter turn. One
# clockwise rotation puts it back: row 0 the top of the field, column 0 the
# left, as seen from behind the sensor looking where it looks.
#
# Measured on the bench, not read off a datasheet: a wall a metre away with a
# doorway off to the right put its empty cells in raw row 0, and the corner of
# the wall leaning closest ended up in raw cell (7, 0).
GRID_QUARTER_TURNS = -1

# ST treats 5 and 9 as the readings you may use. 10 is a valid range too, but it
# is the first sighting of a target the previous frame did not have — for a live
# map that is exactly the flicker we would rather not paint, so it stays out.
VALID_STATUS = (5, 9)

# Colour scale. Fixed, not auto-fitted to each frame: on a map, colour has to
# mean the same thing from frame to frame, otherwise a hand moving closer
# repaints the whole wall behind it and it is impossible to read.
SCALE_NEAR_M = 0.10
SCALE_FAR_M = 2.50

# Ramp stops, near to far, as RGB. Warm is close, cool is far.
RAMP = (
    (214, 69, 65),
    (232, 154, 58),
    (206, 205, 80),
    (86, 170, 130),
    (60, 110, 190),
)

# The window holds two views of the same frame, side by side, because neither
# one alone is honest. The tiles are the data: 64 numbers, holes left as holes.
# The picture is the room: smoothed, hole-filled, colour stretched over whatever
# is in front of the sensor right now — easy to read, and partly invented.
PLOT_W, PLOT_H = 1470, 856
TILE_PAD_L, TILE_PAD_T = 40, 96
TILE_SIZE = 82
BAR_W = 26

# The smoothed picture.
PIC_X, PIC_SIZE = 830, 600

# BGR, because OpenCV works in that order.
COL_BG = (250, 249, 246)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_VOID = (232, 230, 226)   # a cell with no usable reading


def zone_angle_deg(row: int, col: int) -> float:
    """Angle between the sensor axis and the centre of this zone's cone.

    The zones are laid out as a grid of equal angles, so a zone's offset from
    the middle of the grid is an offset in degrees. Two offsets — sideways and
    up-down — combine the way the sides of a right triangle do.
    """
    ax = (col - (SIDE - 1) / 2) * ZONE_DEG
    ay = (row - (SIDE - 1) / 2) * ZONE_DEG
    return math.degrees(math.atan(math.hypot(math.tan(math.radians(ax)),
                                             math.tan(math.radians(ay)))))


def ramp_at(t: float) -> tuple[int, int, int]:
    """A colour from the ramp, `t` running 0 (nearest) to 1 (farthest)."""
    t = min(max(t, 0.0), 1.0) * (len(RAMP) - 1)
    i = min(int(t), len(RAMP) - 2)
    f = t - i
    a, b = RAMP[i], RAMP[i + 1]
    return tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))


def ramp_rgb(metres: float) -> tuple[int, int, int]:
    """Distance to a colour on the fixed near-to-far scale."""
    return ramp_at((metres - SCALE_NEAR_M) / (SCALE_FAR_M - SCALE_NEAR_M))


def read_map(frame, keep_invalid: bool) -> tuple[np.ndarray, np.ndarray]:
    """One frame as (distance in metres, status), both 8x8.

    Cells without a usable reading come back as NaN — a value that cannot be
    mistaken for a distance and refuses to take part in an average, which is
    exactly the behaviour we want out of a hole in the map.
    """
    dist = np.rot90(frame.grid("distance_mm").astype(float) / 1000.0,
                    GRID_QUARTER_TURNS)
    status = np.rot90(frame.grid("target_status"), GRID_QUARTER_TURNS)
    if not keep_invalid:
        dist = np.where(np.isin(status, VALID_STATUS), dist, np.nan)
    return dist, status


def summarise(dist: np.ndarray, status: np.ndarray) -> dict:
    """The numbers under the map: centre, spread, how much of the frame is real."""
    good = dist[np.isfinite(dist)]
    centre = dist[3:5, 3:5]
    centre_good = centre[np.isfinite(centre)]
    return {
        "valid": int(np.isin(status, VALID_STATUS).sum()),
        "shown": int(good.size),
        "centre": float(centre_good.mean()) if centre_good.size else float("nan"),
        "lo": float(good.min()) if good.size else float("nan"),
        "hi": float(good.max()) if good.size else float("nan"),
    }


# ── terminal ─────────────────────────────────────────────────────────────────

RESET = "\033[0m"


def paint(text: str, rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"\033[38;2;{r};{g};{b}m{text}{RESET}"


def map_lines(dist: np.ndarray, status: np.ndarray) -> list[str]:
    """The 8x8 grid as coloured text, one row per line."""
    out = ["      " + "".join(f"{c:^7d}" for c in range(SIDE))]
    for r in range(SIDE):
        cells = []
        for c in range(SIDE):
            value = dist[r, c]
            if not math.isfinite(value):
                cells.append(f"\033[90m{'·':^7}{RESET}")
            else:
                cells.append(paint(f"{value:^7.3f}", ramp_rgb(value)))
        out.append(f"  {r}   " + "".join(cells))
    return out


def run_live(dev, args) -> None:
    print("DEPZ · Lab 4 · The first depth map")
    print(f"port {dev.port}   {SIDE}×{SIDE} zones   {RANGING_HZ} Hz   "
          f"field of view {FOV_DEG:.0f}°, {ZONE_DEG:.2f}° per zone")
    if args.raw:
        print("  --raw: showing every cell, including the ones the sensor "
              "marks as meaningless")
    print()

    body = SIDE + 8
    print("\n" * body, end="")

    started = time.monotonic()
    frames = 0
    dev.start_ranging()
    try:
        for frame in dev.frames():
            frames += 1
            dist, status = read_map(frame, keep_invalid=args.raw)
            st = summarise(dist, status)
            elapsed = time.monotonic() - started

            lines = map_lines(dist, status)
            lines += [
                "",
                f"  centre 2×2      {st['centre']:6.3f} m"
                if math.isfinite(st["centre"]) else
                "  centre 2×2         —    no usable reading in the middle",
                f"  usable zones    {st['valid']:2d} of {ZONES}"
                + (f"   ({ZONES - st['valid']} painted anyway by --raw)"
                   if args.raw else ""),
                f"  range shown     {st['lo']:6.3f} … {st['hi']:6.3f} m"
                if math.isfinite(st["lo"]) else "  range shown        —",
                f"  silicon         {frame.silicon_temp_degc:2d} °C"
                f"   {frames / elapsed:4.1f} fps   frame {frames}",
            ]
            if args.truth is not None and math.isfinite(st["centre"]):
                err_mm = (st["centre"] - args.truth) * 1000.0
                lines.append(f"  tape {args.truth:.3f} m     error {err_mm:+.0f} mm")
            else:
                lines.append("  (to compare: --truth <metres from the tape>)")

            # Move the cursor back up instead of clearing the screen: no flicker,
            # and whatever the reader saw above stays on screen.
            sys.stdout.write(f"\033[{body}A")
            for line in lines:
                sys.stdout.write("\033[2K" + line + "\n")
            sys.stdout.flush()
    finally:
        dev.stop_ranging()


# ── window ───────────────────────────────────────────────────────────────────

def fill_holes(grid: np.ndarray) -> np.ndarray:
    """Guess a distance for every empty cell from the cells around it.

    Only for the smoothed picture, never for a number the lab reports. An
    interpolator handed a NaN spreads it, so the holes have to go before
    smoothing — but what replaces them is invented, and that is precisely why
    the picture sits next to the tiles rather than instead of them.
    """
    import warnings

    out = grid.astype(float).copy()
    for _ in range(SIDE):
        holes = ~np.isfinite(out)
        if not holes.any():
            return out
        padded = np.pad(out, 1, constant_values=np.nan)
        neighbours = np.stack([padded[r:r + SIDE, c:c + SIDE]
                               for r in range(3) for c in range(3)])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean = np.nanmean(neighbours, axis=0)
        out = np.where(holes & np.isfinite(mean), mean, out)
    # Every cell empty — nothing in front of the sensor at all.
    return np.where(np.isfinite(out), out, SCALE_FAR_M)


def draw_picture(dist: np.ndarray, size: int):
    """The smoothed view: 64 cells blown up into a picture of the room.

    Two things separate it from the tiles. Holes are filled in, and the colour
    scale is stretched over what this frame actually contains instead of the
    lab's fixed one — a room a metre away spans 20 cm of depth, and on a scale
    that runs to 2.5 m all of it is the same yellow. Stretched, the same 20 cm
    uses the whole ramp and shapes appear.

    What does not change is the resolution. This is 64 measurements smoothed,
    not 64 measurements sharpened: the picture can show where things are, never
    what they are.
    """
    import cv2

    filled = fill_holes(dist)
    lo, hi = float(filled.min()), float(filled.max())
    if hi - lo < 0.05:          # a flat wall: do not amplify pure noise
        mid = (hi + lo) / 2
        lo, hi = mid - 0.025, mid + 0.025

    norm = np.clip((filled - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
    # Linear, not cubic. Cubic looks smoother, but it overshoots at every edge:
    # measured on a frame with a hand in it, 17 % of the pixels came out nearer
    # than the nearest reading or farther than the farthest one. A picture that
    # invents distances is the one thing this window must not do.
    big = cv2.resize(norm, (size, size), interpolation=cv2.INTER_LINEAR)

    # Colour after smoothing, not before: interpolating between two colours
    # invents shades that are not on the ramp, interpolating between two
    # distances does not.
    lut = np.array([ramp_at(i / 255.0)[::-1] for i in range(256)], np.uint8)
    panel = lut[np.clip(big * 255, 0, 255).astype(np.uint8)]
    return panel, lo, hi


def draw_tiles(dist: np.ndarray, status: np.ndarray, subtitle: str,
               picture: bool = False):
    """Render one frame of the map window.

    Kept separate from the loop so a screenshot for the README can be produced
    headless — same pixels the lab draws, no window popping up.
    """
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    img = np.full((PLOT_H, PLOT_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX

    cv2.putText(img, "DEPZ - Lab 4 - The first depth map", (TILE_PAD_L, 40),
                font, 0.72, COL_TEXT, 1, cv2.LINE_AA)
    cv2.putText(img, subtitle, (TILE_PAD_L, 68), font, 0.46, COL_DIM, 1, cv2.LINE_AA)

    for r in range(SIDE):
        for c in range(SIDE):
            x = TILE_PAD_L + c * TILE_SIZE
            y = TILE_PAD_T + r * TILE_SIZE
            value = dist[r, c]
            if math.isfinite(value):
                rgb = ramp_rgb(value)
                colour = (rgb[2], rgb[1], rgb[0])
                label = f"{value:.3f}"
                # Dark tiles need light text and light tiles need dark text,
                # so pick by how bright the tile actually is.
                brightness = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
                ink = (250, 250, 250) if brightness < 150 else (30, 30, 30)
            else:
                colour, ink = COL_VOID, COL_DIM
                code = int(status[r, c])
                label = STATUS_TILE.get(code, f"st {code}")
            cv2.rectangle(img, (x, y), (x + TILE_SIZE - 4, y + TILE_SIZE - 4),
                          colour, -1)
            size = cv2.getTextSize(label, font, 0.44, 1)[0]
            cv2.putText(img, label,
                        (x + (TILE_SIZE - 4 - size[0]) // 2,
                         y + (TILE_SIZE - 4 + size[1]) // 2),
                        font, 0.44, ink, 1, cv2.LINE_AA)

    # Colour bar, near at the top to match the map's own top-is-far-away feel.
    bar_x = TILE_PAD_L + SIDE * TILE_SIZE + 24
    bar_y0, bar_y1 = TILE_PAD_T, TILE_PAD_T + SIDE * TILE_SIZE - 4
    for y in range(bar_y0, bar_y1):
        t = (y - bar_y0) / (bar_y1 - bar_y0)
        rgb = ramp_rgb(SCALE_NEAR_M + t * (SCALE_FAR_M - SCALE_NEAR_M))
        img[y, bar_x:bar_x + BAR_W] = (rgb[2], rgb[1], rgb[0])
    for i in range(6):
        t = i / 5
        y = int(bar_y0 + t * (bar_y1 - bar_y0))
        metres = SCALE_NEAR_M + t * (SCALE_FAR_M - SCALE_NEAR_M)
        cv2.putText(img, f"{metres:.2f}", (bar_x + BAR_W + 6, y + 5),
                    font, 0.40, COL_DIM, 1, cv2.LINE_AA)

    if picture:
        cap_y = TILE_PAD_T + SIDE * TILE_SIZE + 30
        cv2.putText(img, "MEASURED - 64 cells, holes left as holes",
                    (TILE_PAD_L, cap_y), font, 0.50, COL_TEXT, 1, cv2.LINE_AA)
        cv2.putText(img, "fixed colour scale: a colour means the same distance "
                    "in every frame",
                    (TILE_PAD_L, cap_y + 24), font, 0.44, COL_DIM, 1, cv2.LINE_AA)

        panel, lo, hi = draw_picture(dist, PIC_SIZE)
        y0 = TILE_PAD_T
        img[y0:y0 + PIC_SIZE, PIC_X:PIC_X + PIC_SIZE] = panel

        # Crosshair, and the one number it stands for. The value is the mean of
        # the middle 2x2: an 8x8 grid has no single central cell, and those four
        # are the same four --truth compares against a tape.
        cx = cy = PIC_SIZE // 2
        gx, gy = PIC_X + cx, y0 + cy
        for thick, colour in ((3, (30, 30, 30)), (1, (255, 255, 255))):
            cv2.line(img, (gx - 26, gy), (gx - 8, gy), colour, thick, cv2.LINE_AA)
            cv2.line(img, (gx + 8, gy), (gx + 26, gy), colour, thick, cv2.LINE_AA)
            cv2.line(img, (gx, gy - 26), (gx, gy - 8), colour, thick, cv2.LINE_AA)
            cv2.line(img, (gx, gy + 8), (gx, gy + 26), colour, thick, cv2.LINE_AA)
            cv2.circle(img, (gx, gy), 5, colour, thick, cv2.LINE_AA)

        centre = dist[3:5, 3:5]
        centre = centre[np.isfinite(centre)]
        reading = f"{centre.mean():.3f} m" if centre.size else "-- no target --"
        cv2.putText(img, reading, (PIC_X, y0 + PIC_SIZE + 52),
                    font, 1.5, COL_TEXT, 2, cv2.LINE_AA)
        cv2.putText(img, "at the crosshair (middle 2x2)",
                    (PIC_X + 250, y0 + PIC_SIZE + 52),
                    font, 0.44, COL_DIM, 1, cv2.LINE_AA)

        # This panel gets its own scale strip: its colours mean something else
        # from one frame to the next, and side by side with a fixed scale that
        # has to be said out loud rather than assumed.
        sx, sy = PIC_X, y0 + PIC_SIZE + 74
        for i in range(PIC_SIZE):
            rgb = ramp_at(i / (PIC_SIZE - 1))
            img[sy:sy + 14, sx + i] = (rgb[2], rgb[1], rgb[0])
        cv2.putText(img, f"{lo:.3f} m", (sx, sy + 34),
                    font, 0.44, COL_DIM, 1, cv2.LINE_AA)
        size = cv2.getTextSize(f"{hi:.3f} m", font, 0.44, 1)[0]
        cv2.putText(img, f"{hi:.3f} m", (sx + PIC_SIZE - size[0], sy + 34),
                    font, 0.44, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(img, "stretched over this frame", (sx + PIC_SIZE // 2 - 90,
                                                       sy + 34),
                    font, 0.44, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(img, "SMOOTHED - shapes, not detail: holes filled in, "
                    "colour stretched",
                    (sx, sy + 62), font, 0.44, COL_DIM, 1, cv2.LINE_AA)

    return img


def run_plot(dev, args) -> None:
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    title = "DEPZ Lab 4 - depth map"
    started = time.monotonic()
    frames = 0
    dev.start_ranging()
    try:
        for frame in dev.frames():
            frames += 1
            dist, status = read_map(frame, keep_invalid=args.raw)
            st = summarise(dist, status)
            elapsed = time.monotonic() - started
            subtitle = (
                f"centre {st['centre']:.3f} m   "
                f"usable {st['valid']}/{ZONES}   "
                f"{frames / elapsed:.1f} fps   {frame.silicon_temp_degc} C"
                + ("   [--raw: filter off]" if args.raw else "")
            )
            try:
                cv2.imshow(title,
                           draw_tiles(dist, status, subtitle, picture=True))
            except cv2.error as exc:
                # No display to draw on — over ssh, or in a container. Say so in
                # one line and point at the mode that does work there, instead
                # of dropping an OpenCV stack trace on someone.
                raise SystemExit(
                    f"Cannot open a window ({exc.err.strip() or exc}).\n"
                    "Add --terminal for the text map, which needs no display."
                ) from None
            if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
    finally:
        dev.stop_ranging()
        cv2.destroyAllWindows()


# ── the flat wall ────────────────────────────────────────────────────────────

def run_flat(dev, args) -> None:
    """Point the sensor square at a flat wall and see what the grid makes of it.

    A wall is the one target whose true shape we already know, which makes it
    the only honest way to ask a geometric question: does a cell 20° off the
    axis report the distance along its own slanted line of sight, or the
    distance to the wall measured along the sensor's axis? The two differ by
    1/cos of the angle — at the corners of this grid, thirteen percent, far too
    much to shrug off.
    """
    print("DEPZ · Lab 4 · a flat wall through 64 eyes")
    print(f"port {dev.port}   {SIDE}×{SIDE} zones   "
          f"{FOV_DEG:.0f}° field, {ZONE_DEG:.2f}° per zone")
    print()
    print("Stand the sensor square in front of a flat, matte wall about a metre")
    print("away — a closed door works. Nothing else in the way, nothing shiny.")
    print(f"Collecting {args.samples} frames, hold still…", flush=True)

    stack: list[np.ndarray] = []
    warmup = 0
    dev.start_ranging()
    try:
        for frame in dev.frames():
            dist, _ = read_map(frame, keep_invalid=False)
            # The sensor's opening frame is stamped "first frame" in all 64
            # cells: it checks every reading against the previous one and has no
            # previous one yet. Nothing is broken, but the frame carries no
            # range at all, and counted as a sample it would make every zone
            # look like it had dropped one.
            if not stack and not np.isfinite(dist).any():
                warmup += 1
                continue
            stack.append(dist)
            if len(stack) >= args.samples:
                break
    finally:
        dev.stop_ranging()

    cube = np.dstack(stack)
    # A cell that never once reported a usable range averages over nothing, and
    # numpy is right to complain about it. It is a legitimate outcome here — an
    # open doorway in the corner of the field — so the map keeps its NaN and the
    # warning is suppressed rather than answered.
    with np.errstate(invalid="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            median = np.nanmedian(cube, axis=2)
            noise = np.nanstd(cube, axis=2)
    seen = np.sum(np.isfinite(cube), axis=2)

    print()
    print("Median distance per zone, metres:")
    for r in range(SIDE):
        row = "  ".join("  ·  " if not math.isfinite(median[r, c])
                        else f"{median[r, c]:.3f}" for c in range(SIDE))
        print(f"  {row}")

    centre = np.nanmean(median[3:5, 3:5])
    print()
    print(f"  centre 2×2        {centre:.3f} m")
    if args.truth is not None:
        print(f"  tape              {args.truth:.3f} m"
              f"     error {(centre - args.truth) * 1000:+.0f} mm")
    print(f"  noise per zone    {np.nanmedian(noise) * 1000:.1f} mm typical, "
          f"{np.nanmax(noise) * 1000:.1f} mm worst")
    print(f"  zones with a reading in all {len(stack)} frames   "
          f"{int((seen == len(stack)).sum())} of {ZONES}"
          + (f"   ({warmup} warm-up frame dropped)" if warmup else ""))

    # Group the zones by how far off-axis they are and compare each ring with
    # what the slanted line of sight would cost. If the numbers are distances
    # along the ray, the ratio tracks 1/cos; if the sensor has already projected
    # them onto its axis, the ratio stays at one and the wall reads flat.
    # A ring is the square of zones a given number of steps out from the middle.
    # Averaging around the ring is what makes this test survive a sensor that is
    # not perfectly square to the wall: a tilt raises one side of the ring by as
    # much as it lowers the other, so it cancels, while the slant we are looking
    # for lifts the whole ring at once.
    print()
    print("  ring    off-axis    measured / centre    1/cos(angle)")
    rings: dict[int, list[tuple[float, float]]] = {}
    for r in range(SIDE):
        for c in range(SIDE):
            if math.isfinite(median[r, c]):
                key = int(max(abs(r - 3.5), abs(c - 3.5)) - 0.5)
                rings.setdefault(key, []).append(
                    (median[r, c], zone_angle_deg(r, c)))
    for key in sorted(rings):
        got = statistics.fmean(v for v, _ in rings[key]) / centre
        want = statistics.fmean(1.0 / math.cos(math.radians(a))
                                for _, a in rings[key])
        angle = statistics.fmean(a for _, a in rings[key])
        print(f"  {key + 1:^6d}  {angle:6.1f}°       {got:8.3f}         {want:8.3f}")

    outer = rings[max(rings)]
    got = statistics.fmean(v for v, _ in outer) / centre
    want = statistics.fmean(1.0 / math.cos(math.radians(a)) for _, a in outer)
    print()
    if abs(got - want) < abs(got - 1.0):
        print("  The outer ring is farther by about what the slant costs: the")
        print("  numbers are distances along each zone's own line of sight.")
        print(f"  Divide by 1/cos — up to {(want - 1) * 100:.0f} % at the corners "
              "— to get the")
        print("  distance to the wall itself.")
    else:
        print("  The outer ring is level with the centre, not farther by the")
        print(f"  {(want - 1) * 100:.0f} % the slant would cost: the sensor "
              "already projects")
        print("  each reading onto its own axis. A flat wall arrives flat.")


# ── entry point ──────────────────────────────────────────────────────────────

def has_display() -> bool:
    """Is there a screen to put a window on?

    Checked before opening one rather than after: when Qt cannot find a display
    it aborts the whole process, so an exception around `imshow` never arrives
    to be caught.
    """
    import os
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Lab 4: the first depth map on a VL53L8CH ToF matrix")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--terminal", action="store_true",
                   help="text map in the console instead of the window (for ssh)")
    p.add_argument("--raw", action="store_true",
                   help="paint every cell, including the ones marked meaningless")
    p.add_argument("--truth", type=float,
                   help="distance from the tape measure, in metres")
    p.add_argument("--flat", action="store_true",
                   help="point at a flat wall and check the grid's geometry")
    p.add_argument("--samples", type=int, default=60,
                   help="frames to collect in --flat (60 ≈ four seconds)")
    args = p.parse_args(argv)

    try:
        dev = open_device(args.port) if args.port else open_device()
    except NoDepzDeviceError:
        print("No board found. Check: .venv/bin/depz-sensor list", file=sys.stderr)
        return 1
    except DepzError as exc:
        print(f"Cannot open the board: {exc}", file=sys.stderr)
        return 1

    if not isinstance(dev, Vl53l8Cx):
        print(f"That board is a {type(dev).__name__}, not the ToF matrix. "
              "Plug in the VL53L8CH, or point --port at it.", file=sys.stderr)
        dev.close()
        return 1

    try:
        # The sensor boots empty: its ~84 KB firmware lives on the host and is
        # pushed over USB on every start. That is the second of silence here.
        print("uploading sensor firmware…", end=" ", flush=True)
        dev.init()
        dev.set_resolution(ZONES)
        dev.set_ranging_frequency_hz(RANGING_HZ)
        print("ready\n")

        if args.flat:
            run_flat(dev, args)
        elif args.terminal or not has_display():
            # The window is the default here, unlike the ultrasonic labs: 64
            # numbers refreshed fifteen times a second are a picture, and a
            # picture read as a table of digits is not read at all. Over ssh
            # there is no window to open, so the text map stands in.
            if not args.terminal:
                print("no display — falling back to the text map "
                      "(this is what --terminal does)\n")
            run_live(dev, args)
        else:
            run_plot(dev, args)
    except KeyboardInterrupt:
        print("\nstopped")
    except DepzError as exc:
        print(f"\nThe board stopped talking: {exc}", file=sys.stderr)
        return 1
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
