#!/usr/bin/env python3
"""Example project 1 — An honest ruler (HC-SR04 ultrasonic).

A single ultrasonic reading is never exact, and it is wrong for three different
reasons at once. This example project takes them apart one by one and ends with a number you
can check against a tape measure.

The sensor listens for an echo and reports the time until the returning sound
crossed a threshold. We turn that into a distance ourselves: time times the
speed of sound, halved, because the sound travelled there and back.

Run:
    python examples/01_sr04_ruler/sr04_ruler.py                  live reading in the terminal
    python examples/01_sr04_ruler/sr04_ruler.py --plot           live window with a time plot
    python examples/01_sr04_ruler/sr04_ruler.py --truth 1.550    live reading against a tape
    python examples/01_sr04_ruler/sr04_ruler.py --study          how much averaging you need
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections import deque

try:
    from depz_sensor_sdk import DepzError, NoDepzDeviceError, open_device
except ImportError:  # the SDK lives in the project venv, not in system Python
    raise SystemExit(
        "depz_sensor_sdk is missing — you are probably running system Python.\n"
        "Activate the project environment first (see README):\n"
        "    source .venv/bin/activate        Linux / macOS\n"
        "    .venv\\Scripts\\activate           Windows\n"
        "then run the example again."
    )

# Transducer frequency. This is where the resolution step comes from, so the
# number lives up here: it explains the shape of the --study histogram.
PIEZO_HZ = 40_000

# Ask the board how often to measure. Set it explicitly rather than trusting
# whatever the previous example project left behind: example project 2 raises the rate and, if it is
# killed instead of stopped, the setting stays on the board.
SAMPLE_PERIOD_US = 20_000

# Averaging windows for the study table. Past 200 the curve has flattened and
# more averaging buys nothing, so there is no point going further.
STUDY_WINDOWS = (1, 2, 5, 10, 20, 50, 100, 200)

# Plot window (--plot). Ten seconds of history, same as the DEPZ viewer shows.
PLOT_W, PLOT_H = 1100, 560
PLOT_SECONDS = 10.0
PLOT_PAD_L, PLOT_PAD_R = 78, 20
PLOT_PAD_T, PLOT_PAD_B = 38, 150

# BGR, because OpenCV works in that order.
COL_BG = (250, 249, 246)
COL_GRID = (226, 224, 220)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_RAW = (198, 190, 180)   # single readings — the comb
COL_ANSWER = (120, 140, 20)  # the answer: averaged, outliers dropped
COL_DROP = (60, 60, 220)     # readings the rejection threw away


def speed_of_sound(air_temp_c: float) -> float:
    """Speed of sound in m/s. It gains about 0.6 m/s per degree, so at four
    metres a hot room and a cold one differ by six centimetres."""
    return 331.3 + 0.606 * air_temp_c


def step_mm(air_temp_c: float) -> float:
    """The resolution step in mm: how far sound travels during one wave period,
    halved because the sound makes a round trip."""
    return speed_of_sound(air_temp_c) * 1000.0 / PIEZO_HZ / 2.0


def echo_to_m(echo_us: int, air_temp_c: float) -> float:
    return echo_us * 1e-6 * speed_of_sound(air_temp_c) / 2.0


def robust_mean(samples: list[float], step_m: float) -> tuple[float, int]:
    """Answer taken from the densest cluster. Returns (answer, discarded).

    The sensor replies about the nearest object anywhere in its ~50° cone, so a
    sample set holds echoes from several things at once: a narrow dense peak
    from the target, and a smeared tail from a sofa or a desk grazed edge-on.

    A median does not survive that. Once stray echoes outnumber the target, the
    median slides into the tail — measured on the bench, it missed by 0.35 m.
    The target, however, always forms the DENSEST cluster: it alone reflects
    consistently while everything else is spread out. So we find the most
    crowded window a few steps wide and average only that.
    """
    if len(samples) < 3:
        return statistics.fmean(samples), 0

    ordered = sorted(samples)
    # Window width: the target jitters by a couple of steps, while stray echoes
    # spread far wider. The window must cover the former and miss the latter.
    width = step_m * 6

    best_count, best_lo = 0, 0
    right = 0
    for left, value in enumerate(ordered):
        if right < left:
            right = left
        while right < len(ordered) and ordered[right] - value <= width:
            right += 1
        if right - left > best_count:
            best_count, best_lo = right - left, left

    core = ordered[best_lo:best_lo + best_count]
    return statistics.fmean(core), len(samples) - len(core)


class Window:
    """Sliding window of recent readings, plus a count of missing echoes."""

    def __init__(self, size: int):
        self.samples: deque[float] = deque(maxlen=size)
        self.total = 0
        self.lost = 0

    def add(self, metres: float | None) -> None:
        self.total += 1
        if metres is None:
            self.lost += 1
        else:
            self.samples.append(metres)

    @property
    def ready(self) -> bool:
        return len(self.samples) > 0

    def stats(self, step_m: float) -> dict:
        s = list(self.samples)
        answer, dropped = robust_mean(s, step_m)
        return {
            "answer": answer,
            "dropped": dropped,
            "last": s[-1],
            "mean": statistics.fmean(s),
            "median": statistics.median(s),
            "lo": min(s),
            "hi": max(s),
            "sd": statistics.pstdev(s) if len(s) > 1 else 0.0,
            "n": len(s),
        }


def compose_plot(history, args, step_m: float, rate: float):
    """Render one frame of the plot window.

    Kept separate from the loop so a screenshot for the README can be produced
    headless — same pixels the example project draws, no window popping up.

    `history` is a list of (timestamp, reading or None, answer, was_dropped).
    """
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2  # imported here: the terminal modes must run without OpenCV
    import numpy as np

    frame = np.full((PLOT_H, PLOT_W, 3), COL_BG, dtype=np.uint8)
    x0, x1 = PLOT_PAD_L, PLOT_W - PLOT_PAD_R
    y0, y1 = PLOT_PAD_T, PLOT_H - PLOT_PAD_B

    seen = [h for h in history if h[1] is not None]
    now = history[-1][0] if history else 0.0

    if not seen:
        cv2.putText(frame, "waiting for an echo", (x0 + 20, (y0 + y1) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COL_DIM, 1, cv2.LINE_AA)
        return frame

    # Vertical range: the data plus a tenth of margin, never narrower than four
    # resolution steps — otherwise a still sensor fills the screen with noise
    # and it looks like something dramatic is happening.
    lo = min(h[1] for h in seen)
    hi = max(h[1] for h in seen)
    span = max(hi - lo, step_m * 4)
    mid = (hi + lo) / 2
    lo, hi = mid - span * 0.6, mid + span * 0.6

    def px(t: float) -> int:
        return int(x1 - (now - t) / PLOT_SECONDS * (x1 - x0))

    def py(metres: float) -> int:
        return int(y1 - (metres - lo) / (hi - lo) * (y1 - y0))

    # Grid: five horizontal lines with labels in metres.
    for i in range(5):
        value = lo + (hi - lo) * i / 4
        y = py(value)
        cv2.line(frame, (x0, y), (x1, y), COL_GRID, 1, cv2.LINE_AA)
        cv2.putText(frame, f"{value:.3f}", (8, y + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(frame, "metres", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                COL_DIM, 1, cv2.LINE_AA)
    for sec in range(0, int(PLOT_SECONDS) + 1, 2):
        x = px(now - sec)
        cv2.line(frame, (x, y0), (x, y1), COL_GRID, 1, cv2.LINE_AA)
        label = "now" if sec == 0 else f"-{sec}s"
        cv2.putText(frame, label, (x - 14, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, COL_DIM, 1, cv2.LINE_AA)

    # Raw readings: the comb. This is what the DEPZ viewer shows and all a bare
    # sensor gives you.
    pts = [(px(t), py(v)) for t, v, _, _ in history if v is not None]
    for a, b in zip(pts, pts[1:]):
        cv2.line(frame, a, b, COL_RAW, 1, cv2.LINE_AA)

    # The answer: averaged with outliers dropped. The whole point of the example project is
    # that this line stands still while the comb jumps.
    ans = [(px(t), py(a)) for t, _, a, _ in history if a is not None]
    for a, b in zip(ans, ans[1:]):
        cv2.line(frame, a, b, COL_ANSWER, 2, cv2.LINE_AA)

    # What the tape measure says, if we were told. A dashed line makes it
    # obvious whether the answer sits on it or beside it.
    if args.truth is not None and lo <= args.truth <= hi:
        y = py(args.truth)
        for x in range(x0, x1, 14):
            cv2.line(frame, (x, y), (min(x + 7, x1), y), COL_TEXT, 1, cv2.LINE_AA)
        cv2.putText(frame, "measured", (x0 + 8, y - 7), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, COL_TEXT, 1, cv2.LINE_AA)

    # Discarded readings, marked where they landed.
    for t, v, _, dropped in history:
        if v is not None and dropped:
            cv2.circle(frame, (px(t), py(v)), 3, COL_DROP, -1, cv2.LINE_AA)

    return frame


def draw_tiles(frame, tiles) -> None:
    """Bottom row of stat boxes, in the style of the DEPZ viewer."""
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    n = len(tiles)
    gap = 12
    top = PLOT_H - PLOT_PAD_B + 44
    width = (PLOT_W - PLOT_PAD_L - PLOT_PAD_R - gap * (n - 1)) // n
    # Six tiles do not fit at the size four do, so the value shrinks with the box.
    value_scale = 0.78 if n <= 4 else (0.66 if n == 5 else 0.56)
    for i, (title, value, note) in enumerate(tiles):
        x = PLOT_PAD_L + i * (width + gap)
        cv2.rectangle(frame, (x, top), (x + width, top + 84), COL_GRID, 1)
        cv2.putText(frame, title, (x + 12, top + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.40, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(frame, value, (x + 12, top + 54), cv2.FONT_HERSHEY_SIMPLEX,
                    value_scale, COL_TEXT, 2, cv2.LINE_AA)
        if note:
            cv2.putText(frame, note, (x + 12, top + 74), cv2.FONT_HERSHEY_SIMPLEX,
                        0.36 if n > 4 else 0.40, COL_DIM, 1, cv2.LINE_AA)


def run_plot(dev, args) -> None:
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    step = step_mm(args.temp)
    step_m = step / 1000.0
    win = Window(args.window)
    history: list[tuple[float, float | None, float | None, bool]] = []

    title = "DEPZ Example project 1 - an honest ruler"
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)

    frames = 0
    started = time.monotonic()
    dev.start()
    try:
        for m in dev.stream():
            frames += 1
            now = time.monotonic()
            reading = echo_to_m(m.echo_time_us, args.temp) if m.valid else None
            win.add(reading)
            st = win.stats(step_m) if win.ready else None
            answer = st["answer"] if st else None

            # A reading counts as dropped when it sits further from the answer
            # than the cluster width used by robust_mean.
            dropped = (reading is not None and answer is not None
                       and abs(reading - answer) > step_m * 3)
            history.append((now, reading, answer, dropped))
            while history and now - history[0][0] > PLOT_SECONDS:
                history.pop(0)

            rate = frames / (now - started) if now > started else 0.0
            frame = compose_plot(history, args, step_m, rate)

            if st:
                spread_mm = (st["hi"] - st["lo"]) * 1000.0
                # MIN / MAX covers the whole plot, so the tile matches what the
                # eye sees on screen; SPREAD is the averaging window only.
                shown = [h[1] for h in history if h[1] is not None]
                tiles = [
                    ("ANSWER", f"{st['answer']:.3f} m",
                     f"averaged over {st['n']}, outliers dropped"),
                    ("SINGLE READING", f"{st['last']:.3f} m",
                     f"jitter {st['sd'] * 1000:.1f} mm from the mean"),
                    ("MIN / MAX", f"{min(shown):.3f} / {max(shown):.3f} m",
                     f"over the plotted {PLOT_SECONDS:.0f} s"),
                    ("SPREAD", f"{spread_mm:.1f} mm",
                     f"{spread_mm / step:.1f} steps, last {st['n']}"),
                    ("RATE", f"{rate:.1f} Hz",
                     f"echo lost {win.lost} of {win.total}"),
                ]
                if args.truth is not None:
                    err_mm = (st["answer"] - args.truth) * 1000.0
                    tiles.append(("VS MEASURED", f"{err_mm:+.1f} mm",
                                  f"measured {args.truth:.3f} m"))
                draw_tiles(frame, tiles)

            cv2.imshow(title, frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        dev.stop()
        cv2.destroyAllWindows()


def run_live(dev, args) -> None:
    step = step_mm(args.temp)
    step_m = step / 1000.0
    win = Window(args.window)

    # The live view redraws its lines with ANSI cursor codes. Windows Terminal
    # understands them out of the box; the classic cmd.exe console only after
    # this no-op call, which switches it into VT mode.
    if os.name == "nt":
        os.system("")

    header = [
        "DEPZ · Example project 1 · An honest ruler",
        f"port {dev.port}   air {args.temp:.0f} °C   "
        f"speed of sound {speed_of_sound(args.temp):.0f} m/s   step ~{step:.1f} mm",
        "",
    ]
    if not args.temp_given:
        header.insert(2, "  no temperature given, assuming 20 °C — "
                         "add --temp <°C> if your room differs")
    print("\n".join(header))
    body_lines = 8
    print("\n" * body_lines, end="")

    dev.start()
    try:
        for m in dev.stream():
            win.add(echo_to_m(m.echo_time_us, args.temp) if m.valid else None)
            if not win.ready:
                continue
            st = win.stats(step_m)

            spread_mm = (st["hi"] - st["lo"]) * 1000.0
            lines = [
                f"  single reading    {st['last']:6.3f} m",
                f"  answer over {st['n']:<3d}   {st['answer']:6.3f} m   ← outliers dropped"
                + (f", {st['dropped']} discarded" if st["dropped"] else ""),
                f"  plain average     {st['mean']:6.3f} m   median {st['median']:6.3f} m",
                "",
                f"  window spread     {st['lo']:6.3f} … {st['hi']:6.3f} m"
                f"   ({spread_mm:.1f} mm ≈ {spread_mm / step:.1f} steps)",
                f"  jitter (σ)        {st['sd'] * 1000:6.1f} mm from the mean",
                f"  echo lost         {win.lost} of {win.total}",
            ]
            if args.truth is not None:
                err_mm = (st["answer"] - args.truth) * 1000.0
                lines.append(
                    f"  tape {args.truth:.3f} m      error {err_mm:+.1f} mm"
                    f"  ({abs(err_mm) / (args.truth * 1000.0) * 100:.1f} %)"
                )
            else:
                lines.append("  (to compare: --truth <metres from the tape>)")

            # Move the cursor back up instead of clearing the screen: no flicker,
            # and whatever the reader saw above stays on screen.
            sys.stdout.write(f"\033[{body_lines}A")
            for line in lines:
                sys.stdout.write("\033[2K" + line + "\n")
            sys.stdout.flush()
    finally:
        dev.stop()


def run_study(dev, args) -> None:
    step = step_mm(args.temp)
    step_m = step / 1000.0
    need = max(STUDY_WINDOWS) * args.blocks

    print("DEPZ · Example project 1 · how much averaging you need")
    print(f"port {dev.port}   air {args.temp:.0f} °C   step ~{step:.1f} mm")
    if not args.temp_given:
        print("  no temperature given, assuming 20 °C — "
              "add --temp <°C> if your room differs")
    print(f"collecting {need} readings, hold the sensor still…", flush=True)

    samples: list[float] = []
    lost = 0
    started = time.monotonic()
    dev.start()
    try:
        for m in dev.stream():
            if m.valid:
                samples.append(echo_to_m(m.echo_time_us, args.temp))
            else:
                lost += 1
            if len(samples) >= need:
                break
            if len(samples) % 50 == 0 and len(samples):
                print(f"\r  {len(samples)}/{need}", end="", flush=True)
    finally:
        dev.stop()
    elapsed = time.monotonic() - started
    print(f"\r  done: {len(samples)} readings in {elapsed:.0f} s, "
          f"echo lost {lost} times\n")

    # What the sensor actually reports. Bars start half a step wide, so the
    # jitter between neighbouring steps shows up as two humps. Widen them when
    # the spread is large, or the histogram runs off the screen.
    lo, hi = min(samples), max(samples)
    width = step_m / 2
    while (hi - lo) / width > 24:
        width *= 2
    buckets: dict[int, int] = {}
    for value in samples:
        buckets[int((value - lo) // width)] = buckets.get(int((value - lo) // width), 0) + 1
    top = max(buckets.values())
    print(f"  what the sensor reports (bar = {width * 1000:.1f} mm):")
    for idx in range(min(buckets), max(buckets) + 1):
        count = buckets.get(idx, 0)
        left = lo + idx * width
        bar = "█" * round(count / top * 42)
        print(f"    {left:6.3f} m  {count:5d}  {bar}")
    print()

    # The main table: cut the sample set into blocks of N, average each block and
    # see how far the blocks disagree. That is the honest answer to "how much
    # does the result wander if I average N readings".
    print("  how much averaging (how far the answer wanders between blocks):")
    print("    readings    time     plain average    outliers dropped")
    rate = len(samples) / elapsed if elapsed else 0.0
    rows: list[tuple[int, float, float]] = []
    for n in STUDY_WINDOWS:
        blocks = [samples[i:i + n] for i in range(0, len(samples) - n + 1, n)]
        blocks = [b for b in blocks if len(b) == n]
        if len(blocks) < 2:
            continue
        plain = [statistics.fmean(b) for b in blocks]
        clean = [robust_mean(b, step_m)[0] for b in blocks]
        secs = n / rate if rate else 0.0
        spread_plain = (max(plain) - min(plain)) * 1000.0
        spread_clean = (max(clean) - min(clean)) * 1000.0
        rows.append((n, spread_plain, spread_clean))
        print(f"    {n:5d}      {secs:5.1f} s    {spread_plain:8.2f} mm"
              f"       {spread_clean:8.2f} mm")

    # On a short block the rejection can miss: the densest cluster lands on a
    # stray reflection instead of the target. It is visible right in the table,
    # and the reader should know it is short data, not broken code.
    bad = [n for n, plain, clean in rows if clean > plain]
    if bad:
        # Look for the threshold strictly to the right of the last failure: at
        # N=1..2 there is nothing to reject, so that does not count as a win.
        good = [n for n, plain, clean in rows if clean <= plain and n > max(bad)]
        print()
        print(f"    At {', '.join(map(str, bad))} readings the rejection loses to the")
        print("    plain average: on a short block the densest cluster can settle on")
        print("    a stray reflection. It becomes reliable"
              + (f" from {min(good)} readings up." if good else " on a longer sample."))
    print()

    answer, _ = robust_mean(samples, step_m)
    core = [x for x in samples if abs(x - answer) <= step_m * 3]
    core_spread = ((max(core) - min(core)) * 1000.0) if len(core) > 1 else 0.0
    strays = len(samples) - len(core)
    nearer = sum(1 for x in samples if x < answer - step_m * 3)

    print(f"  Target answer: {answer:.3f} m — average of the densest cluster")
    print(f"  ({len(core)} readings of {len(samples)}, spread {core_spread:.1f} mm = "
          f"{core_spread / step:.1f} steps).")
    print(f"  For comparison: median {statistics.median(samples):.3f} m, "
          f"plain average {statistics.fmean(samples):.3f} m.")
    print()

    if strays:
        print(f"  Off target: {strays} readings of {len(samples)} "
              f"({strays / len(samples) * 100:.0f} %), {nearer} of them nearer than the target.")
        if nearer:
            # A stray NEARER than the target is the cone at work: the sensor
            # answers about the closest thing it can see, wherever it points.
            print("  Those are not noise. The sensor sees a ~50° cone and answers about")
            print("  the nearest thing in it: a sofa by the wall, a desk grazed edge-on,")
            print("  a door frame.")
            if strays > len(samples) * 0.4:
                print("  More than a third are strays — clear the cone or raise the sensor,")
                print("  otherwise the target drowns in other people's echoes.")
        else:
            # Strays FARTHER than the target cannot be another object — nothing
            # behind the target can answer first. It is a weak echo that slipped
            # a wave period, or a second bounce off the wall.
            print("  All of them are farther than the target, so no second object is")
            print("  involved: nothing behind the target could answer first. That is a")
            print("  weak echo slipping a wave period, or sound bouncing twice.")
        print()

    if core_spread < step * 0.6:
        print("  Strong echo: the sensor holds one step, there is little to average.")
    else:
        print("  Echo on the edge: the sensor bounces between neighbouring steps — the")
        print("  threshold crossing shifts by one wave period. Averaging places the")
        print("  answer between the steps and beats the step size, paying with time.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Example project 1: an honest ruler on HC-SR04")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--temp", type=float,
                   help="air temperature, °C (20 by default)")
    p.add_argument("--window", type=int, default=20,
                   help="how many recent readings to average in live mode")
    p.add_argument("--truth", type=float,
                   help="distance from the tape measure, in metres")
    p.add_argument("--plot", action="store_true",
                   help="live window with a time plot instead of terminal output")
    p.add_argument("--study", action="store_true",
                   help="collect a sample set and show how much averaging is needed")
    p.add_argument("--blocks", type=int, default=5,
                   help="blocks per largest window in --study")
    args = p.parse_args(argv)

    # Temperature is not decoration: on the bench it explained three quarters of
    # the gap against the tape (30 °C instead of 20 is almost 2 %). Taking the
    # default silently is fine; staying quiet about it is not.
    args.temp_given = args.temp is not None
    if args.temp is None:
        args.temp = 20.0

    try:
        dev = open_device(args.port) if args.port else open_device()
    except NoDepzDeviceError:
        print("No board found. Check: depz-sensor list", file=sys.stderr)
        return 1
    except DepzError as exc:
        print(f"Cannot open the board: {exc}", file=sys.stderr)
        return 1

    was_period = dev.get_sample_period_us()
    dev.set_sample_period_us(SAMPLE_PERIOD_US)

    try:
        if args.study:
            run_study(dev, args)
        elif args.plot:
            run_plot(dev, args)
        else:
            run_live(dev, args)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        dev.set_sample_period_us(was_period)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
