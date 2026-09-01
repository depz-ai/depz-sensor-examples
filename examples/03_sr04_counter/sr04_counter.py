#!/usr/bin/env python3
"""Example project 3 — Counting people through a doorway (HC-SR04 ultrasonic).

The sensor watches a fixed background — a wall, a door frame, the far side of a
corridor — and counts how many times something came between. No image, no
machine learning, one number per reading.

Two things make it work, and both come from example project 1:

* a stray echo is always NEARER than the target, and a single stray looks
  exactly like someone walking past. So a crossing has to last several readings
  in a row to be counted;
* the threshold needs two levels, not one. With a single level a person
  standing right on it would be counted over and over as the reading wavers.

Run:
    python examples/03_sr04_counter/sr04_counter.py                terminal
    python examples/03_sr04_counter/sr04_counter.py --plot         window with a time plot
    python examples/03_sr04_counter/sr04_counter.py --min-near 60  to count shorter crossings
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import deque

try:
    from depz_sensor_sdk import DepzError, NoDepzDeviceError, open_device
except ImportError:  # the SDK lives in the project venv, not in system Python
    raise SystemExit(
        "depz_sensor_sdk is missing — you are probably running system Python.\n"
        "Use the project environment:\n"
        "    .venv/bin/python examples/03_sr04_counter/sr04_counter.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

# 50 readings a second. A walking person spends about half a second inside the
# cone, so a crossing lands some 25 readings — plenty to be sure of.
SAMPLE_PERIOD_US = 20_000

# How long to watch the empty scene before counting starts.
BACKGROUND_S = 2.0

# Two thresholds instead of one. Something must come closer than ENTER × the
# background to open a crossing, and go back past EXIT × the background to close
# it. The gap between them is the hysteresis: without it a person standing on a
# single threshold would be counted again and again as the reading wavers.
ENTER_FRACTION = 0.75
EXIT_FRACTION = 0.85

# A person is a poor ultrasonic target: clothing absorbs sound, arms and legs
# move, and while someone walks through the beam the echo keeps dropping out for
# a frame or two. Measured on the bench: gaps INSIDE one crossing run 40-160 ms,
# while gaps BETWEEN crossings are 1.3-7.4 s. Nothing in between — so a pause
# only ends a crossing once it lasts this long.
RELEASE_S = 0.30

# And a crossing must add up to at least this much time near the sensor. Example project 1
# measured 6-9 % stray echoes on a clean bench and 70 % with a sofa in the cone,
# every one of them nearer than the background — which looks exactly like a
# person. A stray lasts one reading; a person, hundreds of milliseconds.
MIN_NEAR_MS = 100

PLOT_W, PLOT_H = 1100, 560
PLOT_SECONDS = 20.0
PLOT_PAD_L, PLOT_PAD_R = 78, 20
PLOT_PAD_T, PLOT_PAD_B = 38, 150

COL_BG = (250, 249, 246)
COL_GRID = (226, 224, 220)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_RAW = (198, 190, 180)
COL_LINE = (120, 140, 20)
COL_EVENT = (60, 60, 220)
COL_BAND = (238, 240, 228)


def speed_of_sound(air_temp_c: float) -> float:
    """Speed of sound in m/s: 331 at zero, gaining 0.6 per degree."""
    return 331.3 + 0.606 * air_temp_c


def echo_to_m(echo_us: int, air_temp_c: float) -> float:
    return echo_us * 1e-6 * speed_of_sound(air_temp_c) / 2.0


class Counter:
    """Threshold crossing with hysteresis, a release delay and a minimum duration.

    Three guards, each against a different failure seen on the bench:

    * two thresholds instead of one, so someone standing on the edge is not
      counted over and over as the reading wavers;
    * a release delay, so the flicker inside one crossing does not split it into
      a dozen;
    * a minimum total time near the sensor, so a single stray echo — which is
      always nearer, and therefore looks just like a person — is not counted.
    """

    def __init__(self, background: float, enter: float, exit_: float,
                 release_s: float = RELEASE_S, min_near_ms: float = MIN_NEAR_MS):
        self.background = background
        self.enter = enter
        self.exit = exit_
        self.release_s = release_s
        self.min_near_ms = min_near_ms
        self.count = 0
        self.inside = False
        self.near_ms = 0.0
        self.started_at = 0.0
        self.last_near = 0.0
        self.durations: list[float] = []
        self.closest: list[float] = []  # nearest reading inside each crossing
        self.nearest_now = None
        self.rejected = 0  # blips too short to be a person

    def update(self, metres: float | None, now: float, period_s: float) -> str | None:
        """Feed one reading. Returns "counted" when a crossing completes,
        "rejected" when a blip was thrown away, else None."""
        near = metres is not None and metres < self.enter
        far = metres is None or metres > self.exit

        if near:
            if not self.inside:
                self.inside = True
                self.started_at = now
                self.near_ms = 0.0
                self.nearest_now = metres
            self.last_near = now
            self.near_ms += period_s * 1000.0
            # How close the person actually came. If this sits right against the
            # threshold, the crossing was caught by luck: on the bench, people
            # walking at 1.20 m against a 1.201 m threshold were missed entirely,
            # while those passing at 0.6-1.1 m were caught every time.
            if metres is not None and metres < self.nearest_now:
                self.nearest_now = metres
            return None

        if self.inside and far and now - self.last_near >= self.release_s:
            # The gap held long enough to be a real departure, not a flicker.
            self.inside = False
            if self.near_ms >= self.min_near_ms:
                self.count += 1
                self.durations.append(self.last_near - self.started_at)
                self.closest.append(self.nearest_now)
                return "counted"
            self.rejected += 1
            return "rejected"
        return None


def measure_background(dev, args) -> tuple[float, float, int]:
    """Watch the empty scene. Returns (background, stray line, intruder count).

    Two numbers, not one. The background itself comes from the densest cluster —
    a plain mean would be dragged in by stray echoes, and example project 1 showed those are
    always nearer, so the background would come out short and every crossing
    would be missed.

    The second number is what actually limits the sensor: the NEAREST reading
    the empty scene ever produced. A door frame grazed at an angle throws echoes
    that land well in front of the real background — measured on the bench, a
    background of 1.60 m produced strays down to 1.30 m with nobody there. Any
    threshold above that line counts the door frame as a person, so the
    detection zone ends below it, whatever the fractions say.
    """
    # A countdown, not an instant start: the background must be measured with
    # nobody in the cone, and whoever launches the example project is usually still standing
    # in it. One stray reading here shrinks the detection zone to nothing.
    for left in range(3, 0, -1):
        print(f"\rStep out of the cone — measuring the background in {left}…",
              end="", flush=True)
        time.sleep(1.0)
    print(f"\rMeasuring the background for {BACKGROUND_S:.0f} s — keep the view clear…   ",
          flush=True)
    samples: list[float] = []
    deadline = time.monotonic() + BACKGROUND_S
    dev.start()
    try:
        for m in dev.stream():
            if m.valid:
                samples.append(echo_to_m(m.echo_time_us, args.temp))
            if time.monotonic() >= deadline:
                break
    finally:
        dev.stop()

    if not samples:
        raise SystemExit("No echo at all — nothing to use as a background.")

    ordered = sorted(samples)
    width = 0.05  # 5 cm: wide enough for the jitter, narrow enough to be one object
    best_count, best_lo, right = 0, 0, 0
    for left, value in enumerate(ordered):
        if right < left:
            right = left
        while right < len(ordered) and ordered[right] - value <= width:
            right += 1
        if right - left > best_count:
            best_count, best_lo = right - left, left
    core = ordered[best_lo:best_lo + best_count]
    background = statistics.fmean(core)

    # The stray line must not be decided by one unlucky reading: someone still
    # walking away during the background measurement would drag it to their own
    # distance and shrink the zone to nothing (seen on the bench: a single
    # 0.512 m reading against a 1.612 m background killed the detection).
    # So take a low percentile, and ignore anything so near it must be an
    # object rather than a stray — those get reported instead.
    floor = background * 0.6
    strays = [v for v in ordered if v >= floor]
    intruders = len(ordered) - len(strays)
    if not strays:
        strays = ordered
    nearest_stray = strays[max(0, int(len(strays) * 0.02))]
    return background, nearest_stray, intruders


def compose_plot(history, counter, events, rate: float):
    """Render one frame of the plot window. Separate from the loop so a README
    screenshot can be taken headless with the same pixels."""
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2
    import numpy as np

    frame = np.full((PLOT_H, PLOT_W, 3), COL_BG, dtype=np.uint8)
    x0, x1 = PLOT_PAD_L, PLOT_W - PLOT_PAD_R
    y0, y1 = PLOT_PAD_T, PLOT_H - PLOT_PAD_B
    now = history[-1][0] if history else 0.0

    lo = 0.0
    hi = counter.background * 1.25

    def px(t: float) -> int:
        return int(x1 - (now - t) / PLOT_SECONDS * (x1 - x0))

    def py(metres: float) -> int:
        return int(y1 - (metres - lo) / (hi - lo) * (y1 - y0))

    # The band between the two thresholds — the hysteresis made visible.
    cv2.rectangle(frame, (x0, py(counter.exit)), (x1, py(counter.enter)),
                  COL_BAND, -1)

    for i in range(5):
        value = lo + (hi - lo) * i / 4
        y = py(value)
        cv2.line(frame, (x0, y), (x1, y), COL_GRID, 1, cv2.LINE_AA)
        cv2.putText(frame, f"{value:.2f}", (8, y + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(frame, "metres", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                COL_DIM, 1, cv2.LINE_AA)
    for sec in range(0, int(PLOT_SECONDS) + 1, 5):
        x = px(now - sec)
        cv2.line(frame, (x, y0), (x, y1), COL_GRID, 1, cv2.LINE_AA)
        cv2.putText(frame, "now" if sec == 0 else f"-{sec}s", (x - 14, y1 + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, COL_DIM, 1, cv2.LINE_AA)

    # Background line, and the two thresholds bounding the band.
    for value, label in ((counter.background, "background"),
                         (counter.enter, "enter"), (counter.exit, "leave")):
        y = py(value)
        for x in range(x0, x1, 14):
            cv2.line(frame, (x, y), (min(x + 7, x1), y), COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(frame, label, (x0 + 8, y - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.40, COL_DIM, 1, cv2.LINE_AA)

    pts = [(px(t), py(v)) for t, v in history if v is not None]
    for a, b in zip(pts, pts[1:]):
        cv2.line(frame, a, b, COL_RAW, 1, cv2.LINE_AA)
    for a, b in zip(pts, pts[1:]):
        if abs(a[0] - b[0]) < 30:
            cv2.line(frame, a, b, COL_LINE, 2, cv2.LINE_AA)

    # The count, large enough to read from across the room — this is the whole
    # point of the example project, and the tile at the bottom is too small to watch while
    # walking through the beam.
    label = str(counter.count)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 3.2, 6)
    box_w, box_h = max(tw + 40, 130), th + 54
    # Bottom left: the freshest readings arrive on the right, and the counter
    # must not cover the very thing you are watching while you walk.
    bx0, by0 = x0 + 12, y1 - box_h - 12
    # Opaque panel behind it: the readings sweep across the whole plot, and a
    # bare number lands on top of the trace as often as not.
    cv2.rectangle(frame, (bx0, by0), (bx0 + box_w, by0 + box_h), COL_BG, -1)
    cv2.rectangle(frame, (bx0, by0), (bx0 + box_w, by0 + box_h), COL_GRID, 1)
    cv2.putText(frame, label, (bx0 + (box_w - tw) // 2, by0 + th + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 3.2, COL_LINE, 6, cv2.LINE_AA)
    state = "OCCUPIED" if counter.inside else "crossings"
    (sw, _), _ = cv2.getTextSize(state, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(frame, state, (bx0 + (box_w - sw) // 2, by0 + box_h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                COL_EVENT if counter.inside else COL_DIM, 1, cv2.LINE_AA)

    # A mark for every counted crossing.
    for t in events:
        if now - t <= PLOT_SECONDS:
            x = px(t)
            cv2.line(frame, (x, y0), (x, y1), COL_EVENT, 1, cv2.LINE_AA)
            cv2.circle(frame, (x, y0 + 8), 4, COL_EVENT, -1, cv2.LINE_AA)

    return frame


def draw_tiles(frame, tiles) -> None:
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    n = len(tiles)
    gap = 12
    top = PLOT_H - PLOT_PAD_B + 44
    width = (PLOT_W - PLOT_PAD_L - PLOT_PAD_R - gap * (n - 1)) // n
    value_scale = 0.78 if n <= 4 else 0.66
    for i, (title, value, note) in enumerate(tiles):
        x = PLOT_PAD_L + i * (width + gap)
        cv2.rectangle(frame, (x, top), (x + width, top + 84), COL_GRID, 1)
        cv2.putText(frame, title, (x + 12, top + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.40, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(frame, value, (x + 12, top + 54), cv2.FONT_HERSHEY_SIMPLEX,
                    value_scale, COL_TEXT, 2, cv2.LINE_AA)
        if note:
            cv2.putText(frame, note, (x + 12, top + 74), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, COL_DIM, 1, cv2.LINE_AA)


def tiles_for(counter, value, rate, win_lost, win_total):
    last = f"{counter.durations[-1] * 1000:.0f} ms" if counter.durations else "—"
    avg = (f"{statistics.fmean(counter.durations) * 1000:.0f} ms"
           if counter.durations else "—")
    return [
        ("COUNT", str(counter.count), "crossings since start"),
        ("NOW", "occupied" if counter.inside else "clear",
         f"{value:.3f} m" if value is not None else "no echo"),
        ("LAST CROSSING", last, f"average {avg}"),
        ("BACKGROUND", f"{counter.background:.3f} m",
         f"enter {counter.enter:.3f} / leave {counter.exit:.3f}"),
        ("CLOSEST PASS", f"{min(counter.closest):.3f} m" if counter.closest else "—",
         f"margin {(counter.enter - min(counter.closest)) * 1000:.0f} mm"
         if counter.closest else "how close they came"),
        ("REJECTED", str(counter.rejected),
         f"shorter than {counter.min_near_ms:.0f} ms"),
        ("RATE", f"{rate:.1f} Hz", f"echo lost {win_lost} of {win_total}"),
    ]


def run(dev, args) -> None:
    background, nearest_stray, intruders = measure_background(dev, args)
    if intruders:
        print(f"Warning: {intruders} readings during the background measurement "
              f"came from something")
        print("much closer than the background — someone was still in view. "
              "Restart with the view clear.")

    # The threshold is the smaller of two limits: a fraction of the background,
    # and a margin below the nearest stray the empty scene produced. Whichever
    # is closer wins — crossing the stray line means counting the door frame.
    by_fraction = background * args.enter
    by_noise = nearest_stray - args.margin
    enter = min(by_fraction, by_noise)
    counter = Counter(background, enter, min(enter + (background - enter) * 0.4,
                                             background * args.exit),
                      args.release, args.min_near)

    print(f"Background {background:.3f} m, nearest stray echo {nearest_stray:.3f} m")
    print(f"Detection zone: closer than {counter.enter:.3f} m "
          f"(leave past {counter.exit:.3f} m)"
          + ("  — limited by stray echoes, not by the setting"
             if by_noise < by_fraction else ""))
    print(f"A crossing needs {args.min_near:.0f} ms near "
          f"and {args.release * 1000:.0f} ms away to end.")
    if counter.enter < background * 0.5:
        print("The zone is less than half the distance to the background: whoever")
        print("passes beyond it will not be seen at all.")
    print()

    cv2 = None
    if args.plot:
        import cv2 as _cv2
        cv2 = _cv2
        cv2.namedWindow("DEPZ Example project 3 - counting crossings", cv2.WINDOW_AUTOSIZE)
    else:
        print("\n" * 4, end="")

    history: deque[tuple[float, float | None]] = deque()
    events: list[float] = []
    frames = lost = 0
    started = time.monotonic()

    dev.start()
    try:
        for m in dev.stream():
            frames += 1
            now = time.monotonic()
            value = echo_to_m(m.echo_time_us, args.temp) if m.valid else None
            if value is None:
                lost += 1

            what = counter.update(value, now, SAMPLE_PERIOD_US / 1e6)
            if what == "counted":
                events.append(now)

            history.append((now, value))
            while history and now - history[0][0] > PLOT_SECONDS:
                history.popleft()

            rate = frames / (now - started) if now > started else 0.0

            if cv2 is not None:
                frame = compose_plot(history, counter, events, rate)
                draw_tiles(frame, tiles_for(counter, value, rate, lost, frames))
                cv2.imshow("DEPZ Example project 3 - counting crossings", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
            else:
                shown = f"{value:.3f} m" if value is not None else "  —  "
                last = (f"{counter.durations[-1] * 1000:.0f} ms"
                        if counter.durations else "—")
                lines = [
                    f"  count {counter.count:<4d}  {'OCCUPIED' if counter.inside else 'clear   '}"
                    f"   now {shown}",
                    f"  last crossing {last:>8s}   rejected as too short: {counter.rejected}",
                    f"  {rate:4.1f} Hz, echo lost {lost} of {frames}",
                ]
                sys.stdout.write("\033[4A")
                for line in lines:
                    sys.stdout.write("\033[2K" + line + "\n")
                sys.stdout.write("\033[2K\n")
                sys.stdout.flush()
    finally:
        dev.stop()
        if cv2 is not None:
            cv2.destroyAllWindows()

    print(f"\nCounted {counter.count} crossings, rejected {counter.rejected} blips.")
    if counter.durations:
        print(f"Crossing duration: {min(counter.durations) * 1000:.0f} … "
              f"{max(counter.durations) * 1000:.0f} ms, "
              f"average {statistics.fmean(counter.durations) * 1000:.0f} ms.")
    if counter.closest:
        worst = max(counter.closest)
        margin = (counter.enter - worst) * 1000
        print(f"Closest approach per crossing: {min(counter.closest):.3f} … "
              f"{worst:.3f} m, margin to the threshold {margin:.0f} mm.")
    # The blips matter more than the margin. A person walking at the edge of the
    # cone shows up as one or two readings and nothing more — measured on the
    # bench: passes at 0.53-0.61 m were caught every time, passes at 1.20 m
    # against a 1.20 m threshold left a single reading and were lost.
    if counter.rejected >= max(1, counter.count):
        print()
        print(f"Warning: {counter.rejected} blips against {counter.count} counted "
              f"crossings. A blip is someone")
        print("crossing at the edge of the cone, where the echo no longer comes "
              "back — those are")
        print("missed entirely. Aim the sensor so the background is far away and "
              "people pass close.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Example project 3: counting crossings on HC-SR04")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--temp", type=float, default=20.0, help="air temperature, °C")
    p.add_argument("--enter", type=float, default=ENTER_FRACTION,
                   help="enter threshold as a fraction of the background")
    p.add_argument("--exit", type=float, default=EXIT_FRACTION,
                   help="leave threshold as a fraction of the background")
    p.add_argument("--margin", type=float, default=0.10,
                   help="metres to stay below the nearest stray echo")
    p.add_argument("--release", type=float, default=RELEASE_S,
                   help="seconds of clear view that end a crossing")
    p.add_argument("--min-near", type=float, default=MIN_NEAR_MS,
                   help="milliseconds near the sensor needed to count a crossing")
    p.add_argument("--plot", action="store_true", help="window with a time plot")
    args = p.parse_args(argv)

    if args.enter >= args.exit:
        print("--enter must be smaller than --exit", file=sys.stderr)
        return 2

    try:
        dev = open_device(args.port) if args.port else open_device()
    except NoDepzDeviceError:
        print("No board found. Check: .venv/bin/depz-sensor list", file=sys.stderr)
        return 1
    except DepzError as exc:
        print(f"Cannot open the board: {exc}", file=sys.stderr)
        return 1

    was_period = dev.get_sample_period_us()
    dev.set_sample_period_us(SAMPLE_PERIOD_US)
    try:
        run(dev, args)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        dev.set_sample_period_us(was_period)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
