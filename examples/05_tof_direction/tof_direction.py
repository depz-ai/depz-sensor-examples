#!/usr/bin/env python3
"""Example project 5 (two-beam variant) — In and out through a doorway (VL53L8CH).

Example project 3 ended with a promise: to tell in from out you need two sensors side by
side, because whichever one fires first tells you the direction. This example project keeps
that promise without a second board. The 8x8 matrix is cut down the middle
into two "beams" — the three left columns and the three right columns, with
the two middle columns left as a gap so the beams do not touch — and each half
is nothing more than a presence detector like the one in example project 3.

A person walking through covers the left beam, then both, then only the right
one, then nothing. Read the order of those states and the direction falls out. That is the
whole example project: two booleans and a short memory.

Run:
    python examples/05_tof_direction/tof_direction.py               live window
    python examples/05_tof_direction/tof_direction.py --swap        if in and out are backwards
    python examples/05_tof_direction/tof_direction.py --vertical    if people cross top-to-bottom
    python examples/05_tof_direction/tof_direction.py --terminal    text output, for ssh
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings

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
        "    .venv/bin/python examples/05_tof_direction/tof_direction.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

SIDE = 8
ZONES = SIDE * SIDE
RANGING_HZ = 15

# Example project 4 measured this: the raw grid arrives a quarter turn out, and one
# clockwise rotation puts column 0 on the left of the field. The two beams are
# defined by column, so without this rotation "left" would mean "top".
GRID_QUARTER_TURNS = -1

# Only these two statuses carry a range worth using (example project 4).
VALID_STATUS = (5, 9)

# The two beams, as column slices: the left half and the right half, nothing
# left over. The first version kept columns 3-4 as a gap between the beams,
# and the bench threw it out: a person walks the gap in about 0.4 s, both
# beams are dark for that long, and the example project closed every crossing in the
# middle — one walk-through became two half-events. There is no gap now; the
# order of the beams is read from timestamps, so they may overlap freely.
LEFT_COLS = slice(0, 4)
RIGHT_COLS = slice(4, 8)
ALL = slice(0, SIDE)

# The board may be mounted so that people cross the field top-to-bottom
# instead of left-to-right. --vertical cuts the matrix into a top beam and a
# bottom beam instead; the names "left"/"right" become "top"/"bottom".
BEAM_PARTS = {
    "horizontal": (("left", (ALL, LEFT_COLS)), ("right", (ALL, RIGHT_COLS))),
    "vertical": (("top", (LEFT_COLS, ALL)), ("bottom", (RIGHT_COLS, ALL))),
}

# How long to watch the empty doorway before counting starts.
BACKGROUND_S = 3.0

# A cell counts as covered when it reads this much nearer than its own
# background. Example project 4 put the noise of a still cell at 4.3 mm typical, 15 mm at
# worst; 0.15 m is ten times the worst case.
NEAR_MARGIN_M = 0.15

# A cell that saw nothing during the background phase has no background to be
# nearer than. Anything that appears there inside this range is an object.
OPEN_SPACE_M = 3.0

# A beam has 24 cells. It switches on at ENTER_CELLS covered and off only when
# it drops to EXIT_CELLS — two levels, so a shoulder on the edge of a beam does
# not make it chatter (the hysteresis trick from example project 3, counted in cells).
ENTER_CELLS = 5
EXIT_CELLS = 2

# A beam that goes dark stays "on" for this long before the example project believes it.
# Example project 3 measured flicker inside one crossing at 40-160 ms and real gaps at
# seconds — 300 ms sits in the empty space between them.
RELEASE_S = 0.30

# A crossing is over when both beams have been dark for this long. Not "as
# soon as both are dark": the green patch of a walking person jumps across
# the field in a few frames, and there are frames in the middle with too few
# cells on either side. 0.8 s is longer than any such hole and shorter than
# the gap between two people in a queue.
QUIET_S = 0.8

# A crossing shorter than this is a hand, a reflection, or a fly.
MIN_CROSSING_MS = 150

# Fallback for runners. At 15 frames a second someone running crosses the
# whole field in six or eight frames and often lights both beams in the same
# frame — then the beams have the same switch-on time and there is no "first".
# For that case the example project also remembers where across the field the covered
# cells sat in the first and the last frame of the crossing, and if that
# centre moved at least this many lines, the move is the direction.
MIN_TRAVEL_LINES = 3.0

# A cell has to be covered in this many consecutive frames before it counts.
# Two frames kept stray readings out — but it also cost a frame at every beam,
# and at a brisk walk a person is in one half of the field for only two or
# three frames of the fifteen per second. Crossings went missing. One frame
# now: the beam's own threshold of ENTER_CELLS already ignores the one to
# three stray cells an empty room produces.
PERSIST_FRAMES = 1

# A cell that answered in at least this share of the background frames gets a
# background from its answers, even though it is a poor one. Dropping it to
# "open space" is worse: then every one of its stray answers looks like an
# object where there was none.
BACKGROUND_MIN_SHARE = 0.10

# Window.
PLOT_W, PLOT_H = 1180, 700
MAP_X, MAP_Y, MAP_TILE = 40, 160, 60
LOG_X = 600

COL_BG = (250, 249, 246)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_IN = (60, 150, 40)       # BGR: green
COL_OUT = (60, 90, 210)      # BGR: red
COL_FREE = (238, 240, 235)
COL_COVERED = (150, 190, 90)
COL_BEAM_ON = (214, 238, 210)
COL_BEAM_OFF = (236, 236, 233)


def read_grid(frame) -> np.ndarray:
    """One frame as an 8x8 of metres, unusable cells NaN, column 0 on the left."""
    dist = np.rot90(frame.grid("distance_mm").astype(float) / 1000.0,
                    GRID_QUARTER_TURNS)
    status = np.rot90(frame.grid("target_status"), GRID_QUARTER_TURNS)
    return np.where(np.isin(status, VALID_STATUS), dist, np.nan)


def covered(grid: np.ndarray, background: np.ndarray) -> np.ndarray:
    """Which cells have something in front of their usual view, this frame."""
    seen = np.isfinite(grid)
    nearer = seen & np.isfinite(background) & (grid < background - NEAR_MARGIN_M)
    appeared = seen & ~np.isfinite(background) & (grid < OPEN_SPACE_M)
    return nearer | appeared


class Persistence:
    """Keeps only the cells that have been covered PERSIST_FRAMES frames in a
    row. One stray reading cannot get through; a person, who covers the same
    cells frame after frame, is delayed by one frame and nothing more."""

    def __init__(self):
        self.streak = np.zeros((SIDE, SIDE), int)

    def update(self, now_covered: np.ndarray) -> np.ndarray:
        self.streak = np.where(now_covered, self.streak + 1, 0)
        return self.streak >= PERSIST_FRAMES


class Beam:
    """One half of the matrix acting as a presence detector in the style of example project 3.

    Three things separate it from a bare cell count: it needs ENTER_CELLS to
    switch on but only EXIT_CELLS to stay on, and once the cells go it waits
    RELEASE_S before switching off. All three exist because a person is not a
    solid block — the count flickers at the edges, and each flicker would
    otherwise be a state change.
    """

    def __init__(self, part: tuple[slice, slice]):
        self.part = part          # (rows, cols) of the matrix this beam watches
        self.on = False
        self.cells = 0
        self.last_busy = 0.0
        self.on_since = 0.0       # when it last switched on
        self.off_at = 0.0         # when it last switched off

    def update(self, mask: np.ndarray, now: float) -> None:
        self.cells = int(mask[self.part].sum())
        if not self.on:
            if self.cells >= ENTER_CELLS:
                self.on = True
                self.on_since = now
                self.last_busy = now
            return
        if self.cells > EXIT_CELLS:
            self.last_busy = now
        elif now - self.last_busy >= RELEASE_S:
            self.on = False
            self.off_at = now


class Counter:
    """Turns the order in which the two beams fire into in or out.

    Only two questions are asked when the doorway has been quiet for QUIET_S:
    which beam switched on first, and which switched off last. A person walking
    through lights the first beam, then both, then only the second — so the
    beam that saw them first is not the one that saw them last:

        first on = A, last off = B  -> walked A to B  -> "in" or "out"

    Anything else — one beam only, both at once, too short — is not a crossing
    and is dropped without a word. A counter on a doorway has two numbers to
    report, and a third column of "maybes" does not help anyone reading it.

    Timestamps rather than states, because a fast walker is out of the first
    beam before its release delay has let go of it — the beams then look like
    "both on" for the whole crossing and a state machine sees no order.
    """

    def __init__(self, swap: bool = False, layout: str = "horizontal"):
        self.swap = swap
        (self.name_a, part_a), (self.name_b, part_b) = BEAM_PARTS[layout]
        self.a = Beam(part_a)
        self.b = Beam(part_b)
        self.in_count = 0
        self.out_count = 0
        self.ignored = 0
        self.why_ignored: str | None = None   # shown so a missed walk is not a mystery
        self.busy = False
        self.started_at = 0.0
        self.quiet_since = 0.0
        self.seen_a = False
        self.seen_b = False
        self.axis = 1 if layout == "horizontal" else 0   # columns or rows
        self.first_centre = float("nan")
        self.last_centre = float("nan")
        self.log: list[tuple[float, str]] = []

    def _centre(self, mask: np.ndarray) -> float:
        """Mean column (or row, with --vertical) of the covered cells."""
        lines = np.nonzero(mask)[self.axis]
        return float(lines.mean()) if lines.size else float("nan")

    @property
    def beams(self):
        return ((self.name_a, self.a), (self.name_b, self.b))

    @property
    def first(self) -> str | None:
        if not self.busy:
            return None
        if self.seen_a and self.seen_b:
            return self.name_a if self.a.on_since <= self.b.on_since else self.name_b
        return self.name_a if self.seen_a else self.name_b

    def update(self, mask: np.ndarray, now: float) -> str | None:
        """Feed one frame. Returns "in" or "out" when a crossing ends, else None."""
        self.a.update(mask, now)
        self.b.update(mask, now)
        a, b = self.a.on, self.b.on

        if not self.busy:
            if a or b:
                self.busy = True
                self.started_at = now
                self.quiet_since = now
                self.seen_a, self.seen_b = a, b
                self.first_centre = self.last_centre = self._centre(mask)
            return None

        self.seen_a |= a
        self.seen_b |= b
        if a or b:
            self.quiet_since = now
            centre = self._centre(mask)
            if np.isfinite(centre):
                self.last_centre = centre
        elif now - self.quiet_since >= QUIET_S:
            return self._close(now)
        return None

    def _close(self, now: float) -> str | None:
        self.busy = False
        held_ms = (self.quiet_since - self.started_at) * 1000.0
        if held_ms < MIN_CROSSING_MS:
            return self._ignore(f"too short, {held_ms:.0f} ms")

        # The beams' answer, if they have one: a clear first and a clear last.
        a_to_b = None
        if self.seen_a and self.seen_b and self.a.on_since != self.b.on_since \
                and self.a.off_at != self.b.off_at:
            first_is_a = self.a.on_since < self.b.on_since
            last_is_a = self.a.off_at > self.b.off_at
            if first_is_a != last_is_a:
                a_to_b = first_is_a

        # Otherwise, the runner's fallback: where the patch was at the start
        # against where it was at the end.
        travel = self.last_centre - self.first_centre
        if a_to_b is None and abs(travel) >= MIN_TRAVEL_LINES:
            a_to_b = travel > 0

        if a_to_b is None:
            if not (self.seen_a and self.seen_b):
                only = self.name_a if self.seen_a else self.name_b
                return self._ignore(f"only the {only} beam fired, patch moved "
                                    f"{travel:+.1f} lines, {held_ms / 1000:.1f} s")
            return self._ignore(f"no clear direction, patch moved {travel:+.1f} "
                                f"lines, {held_ms / 1000:.1f} s")
        self.why_ignored = None

        if self.swap:
            a_to_b = not a_to_b
        event = "in" if a_to_b else "out"
        if a_to_b:
            self.in_count += 1
        else:
            self.out_count += 1
        self.log.append((now, event))
        del self.log[:-8]
        return event

    def _ignore(self, why: str) -> None:
        self.ignored += 1
        self.why_ignored = why
        return None


def measure_background(dev, seconds: float) -> tuple[np.ndarray, int]:
    """Watch the empty doorway and remember what each cell normally sees.

    Per cell, not one number: one column may look at a frame two metres away,
    the next down an empty corridor with nothing to reflect off. Cells of the
    second kind get NaN and are handled by `covered` on their own terms.
    """
    stack: list[np.ndarray] = []
    dev.start_ranging()
    try:
        deadline = time.monotonic() + seconds
        for frame in dev.frames():
            stack.append(read_grid(frame))
            if time.monotonic() >= deadline:
                break
    finally:
        dev.stop_ranging()

    cube = np.dstack(stack)
    seen = np.sum(np.isfinite(cube), axis=2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(cube, axis=2)
    enough = max(3, int(len(stack) * BACKGROUND_MIN_SHARE))
    background = np.where(seen >= enough, median, np.nan)
    return background, len(stack)


# ── window ───────────────────────────────────────────────────────────────────

def draw_window(mask: np.ndarray, counter: Counter, rate: float,
                now: float):
    """Render one frame. Separate from the loop so a README screenshot can be
    produced headless with the same pixels."""
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    img = np.full((PLOT_H, PLOT_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX

    cv2.putText(img, "DEPZ - Example project 5 - In and out, two-beam variant", (40, 40),
                font, 0.72, COL_TEXT, 1, cv2.LINE_AA)
    (name_a, _), (name_b, _) = counter.beams
    cv2.putText(img, f"{name_a} beam = one half of the field, {name_b} beam = "
                "the other  (--vertical to rotate, --swap to flip)",
                (40, 68), font, 0.46, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, f"{rate:4.1f} fps", (PLOT_W - 130, 40), font, 0.5,
                COL_DIM, 1, cv2.LINE_AA)

    # Beam bands behind the tiles, lit when the beam is on.
    for name, beam in counter.beams:
        rows, cols = beam.part
        x0, x1 = cols.start * MAP_TILE, cols.stop * MAP_TILE
        y0, y1 = rows.start * MAP_TILE, rows.stop * MAP_TILE
        col = COL_BEAM_ON if beam.on else COL_BEAM_OFF
        cv2.rectangle(img, (MAP_X + x0 - 6, MAP_Y + y0 - 6),
                      (MAP_X + x1 + 2, MAP_Y + y1 + 2), col, -1)
        label = f"{name.upper()}  {beam.cells:2d} cells  {'ON' if beam.on else 'off'}"
        cv2.putText(img, label, (MAP_X + x0, MAP_Y + y0 - 10),
                    font, 0.44, COL_TEXT, 1, cv2.LINE_AA)

    for r in range(SIDE):
        for c in range(SIDE):
            x = MAP_X + c * MAP_TILE
            y = MAP_Y + r * MAP_TILE
            col = COL_COVERED if mask[r, c] else COL_FREE
            cv2.rectangle(img, (x, y), (x + MAP_TILE - 4, y + MAP_TILE - 4), col, -1)

    # Counters.
    y = MAP_Y
    for label, value, col in (("IN", counter.in_count, COL_IN),
                              ("OUT", counter.out_count, COL_OUT)):
        cv2.putText(img, label, (LOG_X, y + 40), font, 1.0, col, 2, cv2.LINE_AA)
        cv2.putText(img, f"{value:3d}", (LOG_X + 220, y + 40), font, 1.5, col,
                    3, cv2.LINE_AA)
        y += 80

    # Current crossing.
    y += 20
    if counter.busy:
        text = f"crossing... first seen by the {counter.first} beam"
    else:
        text = "doorway clear"
    cv2.putText(img, text, (LOG_X, y), font, 0.5, COL_TEXT, 1, cv2.LINE_AA)
    if counter.why_ignored:
        y += 26
        cv2.putText(img, f"last ignored: {counter.why_ignored}", (LOG_X, y),
                    font, 0.44, COL_DIM, 1, cv2.LINE_AA)

    # Last few events.
    y += 40
    cv2.putText(img, "recent", (LOG_X, y), font, 0.44, COL_DIM, 1, cv2.LINE_AA)
    for when, event in reversed(counter.log):
        y += 26
        col = COL_IN if event == "in" else COL_OUT
        cv2.putText(img, f"{now - when:5.1f} s ago   {event}", (LOG_X, y),
                    font, 0.5, col, 1, cv2.LINE_AA)

    cv2.putText(img, "q or Esc to quit", (40, PLOT_H - 20), font, 0.44,
                COL_DIM, 1, cv2.LINE_AA)
    return img


def window_open(cv2, title: str) -> bool:
    """True while the window is still on screen.

    OpenCV 4 answers 0 for a window the user has closed; OpenCV 5 raises
    instead ("NULL guiReceiver"), which would end the run with a traceback
    rather than a quiet exit. Both mean the same thing here.
    """
    try:
        return cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


def run_window(dev, background: np.ndarray, args) -> None:
    import cv2

    counter = Counter(swap=args.swap, layout=args.layout)
    persist = Persistence()
    title = "DEPZ Example project 5 - two-beam counter"
    started = time.monotonic()
    frames = 0
    dev.start_ranging()
    try:
        for frame in dev.frames():
            frames += 1
            now = time.monotonic()
            mask = persist.update(covered(read_grid(frame), background))
            event = counter.update(mask, now)
            if event:
                report(counter, event)
            rate = frames / max(now - started, 1e-6)
            cv2.imshow(title, draw_window(mask, counter, rate, now))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if not window_open(cv2, title):
                break
    finally:
        dev.stop_ranging()
        cv2.destroyAllWindows()


def run_terminal(dev, background: np.ndarray, args) -> None:
    counter = Counter(swap=args.swap, layout=args.layout)
    persist = Persistence()
    print("\nwatching the doorway — walk through it (Ctrl-C to stop)\n")
    shown = None
    dev.start_ranging()
    try:
        for frame in dev.frames():
            now = time.monotonic()
            mask = persist.update(covered(read_grid(frame), background))
            event = counter.update(mask, now)
            (na, a), (nb, b) = counter.beams
            line = (f"  {na} {a.cells:2d} {'ON ' if a.on else 'off'}   "
                    f"{nb} {b.cells:2d} {'ON ' if b.on else 'off'}   "
                    f"in {counter.in_count}  out {counter.out_count}")
            sys.stdout.write("\033[2K\r" + line)
            sys.stdout.flush()
            if event:
                sys.stdout.write("\n")
                report(counter, event)
            elif counter.why_ignored and counter.why_ignored != shown:
                sys.stdout.write("\n")
                print(f"  {time.strftime('%H:%M:%S')}  ignored: {counter.why_ignored}")
            shown = counter.why_ignored
    finally:
        dev.stop_ranging()


def report(counter: Counter, event: str) -> None:
    print(f"  {time.strftime('%H:%M:%S')}  {event:4s}   "
          f"in {counter.in_count}   out {counter.out_count}")


def has_display() -> bool:
    import os
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Example project 5 (two-beam variant): in and out through a doorway")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--swap", action="store_true",
                   help="call right-to-left 'in' instead of left-to-right")
    p.add_argument("--vertical", dest="layout", action="store_const",
                   const="vertical", default="horizontal",
                   help="people cross the field top-to-bottom, not left-to-right")
    p.add_argument("--terminal", action="store_true",
                   help="text output instead of the window (for ssh)")
    p.add_argument("--background", type=float, default=BACKGROUND_S,
                   help="seconds spent learning the empty doorway")
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
        print("uploading sensor firmware…", end=" ", flush=True)
        dev.init()
        dev.set_resolution(ZONES)
        dev.set_ranging_frequency_hz(RANGING_HZ)
        print("ready")

        print(f"\nStand clear of the doorway — learning the empty scene for "
              f"{args.background:.0f} s…", flush=True)
        background, frames = measure_background(dev, args.background)
        known = int(np.isfinite(background).sum())
        print(f"  {frames} frames, {known} of {ZONES} cells have a background "
              f"({ZONES - known} look at open space)")
        if known:
            print(f"  background {np.nanmin(background):.3f} … "
                  f"{np.nanmax(background):.3f} m")

        if args.terminal or not has_display():
            if not args.terminal:
                print("no display — falling back to text output "
                      "(this is what --terminal does)")
            run_terminal(dev, background, args)
        else:
            run_window(dev, background, args)
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
