#!/usr/bin/env python3
"""Lab 8 — A pedometer (BNO086 IMU).

A step is a collision. Your foot hits the floor, the floor hits back, and for a
tenth of a second everything you are carrying is thrown upwards. The sensor
feels that as a spike in acceleration, and counting steps is counting spikes.

Which sounds like one line of code, and is not, because two things get in the
way and both are in this lab.

The first is that the sensor also feels everything else — a hand swinging, a
board set down on a desk, a door closing. A number has to be picked below which
a spike is not a step, and that number cannot be guessed; it comes off the
bench. On this board, resting flat, linear acceleration wanders by 0.03 m/s².
Carried in a hand while walking it peaks around 2. In a trouser pocket, walking
the same corridor, it peaks between 5 and 23. Where a sensor is worn changes
the signal by more than an order of magnitude, and every threshold below is
about a board carried on the body.

The second is that one step makes more than one spike. The heel lands, the foot
rolls, the other leg swings through; a good walk produces a cluster of peaks a
few hundredths of a second apart, and counting all of them counts far too many.
So after each accepted step the counter stops listening for a while — long
enough to let a footfall finish, short enough not to miss the next one. That
waiting time is the second number, and it decides the fastest walk the lab can
follow: 0.25 s means at most 240 steps a minute.

The chip has its own pedometer, and the lab shows both counts side by side.
That is not decoration. Watch when the chip starts counting: on the bench walk
it reported nothing for the first three seconds and then jumped straight to 2,
because it waits to be convinced that a rhythm is a walk. Our count starts on
the first footfall. Neither is wrong — they answer slightly different questions.

Run:
    python labs/08_imu_pedometer/lab8_imu.py                  live count in a window
    python labs/08_imu_pedometer/lab8_imu.py --check 20       walk 20, see who was right
    python labs/08_imu_pedometer/lab8_imu.py --threshold 3    accept weaker spikes
    python labs/08_imu_pedometer/lab8_imu.py --terminal       text output, for ssh
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import deque

try:
    import numpy as np
    from depz_sensor_sdk import (
        Bno086,
        DepzError,
        NoDepzDeviceError,
        SensorId,
        open_device,
    )
except ImportError:  # the SDK lives in the project venv, not in system Python
    raise SystemExit(
        "depz_sensor_sdk is missing — you are probably running system Python.\n"
        "Use the project environment:\n"
        "    .venv/bin/python labs/08_imu_pedometer/lab8_imu.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

# Linear acceleration is the accelerometer with gravity taken out by the chip,
# which is exactly what a pedometer wants: standing still reads zero instead of
# 9.8, so a threshold means "this much movement" and not "this much movement
# plus whichever way up the board is".
REPORT_HZ = 100
CHIP_HZ = 10        # the chip's own step counter, for comparison

# Both numbers were measured on the bench, walking a corridor with the board in
# a trouser pocket, and both are arguments so you can find your own.
# Chosen across three bench walks at 44, 64 and 120 steps per minute, and
# chosen for consistency rather than accuracy: it overcounts by 10-15% at every
# one of those speeds. Nothing the sweep tried was accurate at all three. The
# settings that were exact on the slow walks counted 42 for 30 on the brisk
# one, because a faster footfall rings more and a fixed deaf time cannot know
# that. A steady 13% too many is a bias you can see and allow for; being right
# twice and double-counting the third time is not.
THRESHOLD = 3.0     # m/s², below this a spike is not a footfall
REFRACTORY = 0.40   # s, deaf time after a step — 150 steps/min is the ceiling

# What --check tries alongside your settings. One walk is expensive — it costs
# a person getting up — so it should answer the whole question, not one point
# of it.
SWEEP_THRESHOLDS = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)
SWEEP_REFRACTORY = (0.20, 0.30, 0.40)

# A footfall is a spike about a tenth of a second wide, so a three-sample mean
# at 100 Hz blunts single-sample noise without touching the shape of the step.
SMOOTH = 3

# Trace length in the window: enough to show a walking rhythm, not so much that
# the peaks turn into a hedge.
TRACE_S = 6.0

# Still for this long and the lab calls the walk finished — used by --check to
# know when to print, and to work out cadence over the walk rather than over
# the time somebody spent standing about afterwards.
QUIET_S = 3.0

# ── window geometry, BGR colours ─────────────────────────────────────────────
WIN_W, WIN_H = 980, 620
PLOT_X, PLOT_Y, PLOT_W, PLOT_H = 60, 292, 860, 244

COL_BG = (250, 249, 246)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_TRACE = (104, 148, 90)
COL_STEP = (65, 69, 214)
COL_THRESH = (70, 130, 205)
COL_GRID = (226, 223, 217)


class StepCounter:
    """Peaks above a threshold, no two closer together than the deaf time.

    Deliberately the simplest thing that can work: no filtering beyond a
    three-sample mean, no frequency analysis, nothing adaptive. Everything it
    gets wrong, it gets wrong visibly — which is the point of a lab.
    """

    def __init__(self, threshold: float, refractory: float) -> None:
        self.threshold = threshold
        self.refractory = refractory
        self.steps = 0
        self.step_times: list[float] = []
        self._window: deque[float] = deque(maxlen=SMOOTH)
        self._prev = 0.0          # smoothed value one sample back
        self._rising = False      # was the last change upwards?
        self._last_step = -9.0

    def feed(self, magnitude: float, t: float) -> tuple[float, bool]:
        """One sample in; the smoothed value and whether it completed a step."""
        self._window.append(magnitude)
        value = sum(self._window) / len(self._window)

        # A peak is where a rise turns into a fall — checked on the smoothed
        # value one sample late, which is the price of not needing the future.
        peaked = self._rising and value < self._prev
        self._rising = value > self._prev
        took_step = (peaked and self._prev > self.threshold
                     and t - self._last_step >= self.refractory)
        if took_step:
            self.steps += 1
            self.step_times.append(t)
            self._last_step = t
        self._prev = value
        return value, took_step

    def cadence(self) -> float:
        """Steps per minute over the steps taken, 0 until there are two."""
        if len(self.step_times) < 2:
            return 0.0
        span = self.step_times[-1] - self.step_times[0]
        return 0.0 if span <= 0 else 60.0 * (len(self.step_times) - 1) / span


def draw_window(counter: StepCounter, trace, chip_steps: int, now: float,
                walking: bool):
    """Render one frame: the two counts, and the signal they came from."""
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    img = np.full((WIN_H, WIN_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX

    cv2.putText(img, "DEPZ - Lab 8 - Pedometer", (40, 44), font, 0.72,
                COL_TEXT, 1, cv2.LINE_AA)
    cv2.putText(img, "a step is a collision: count the spikes, ignore the echoes",
                (40, 72), font, 0.46, COL_DIM, 1, cv2.LINE_AA)

    cv2.putText(img, "STEPS", (60, 130), font, 0.52, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, f"{counter.steps}", (60, 220), font, 2.8, COL_TEXT, 3, cv2.LINE_AA)
    cv2.putText(img, "counted here, from the acceleration", (60, 250), font, 0.42,
                COL_DIM, 1, cv2.LINE_AA)

    cv2.putText(img, "THE CHIP SAYS", (380, 130), font, 0.52, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, f"{chip_steps}", (380, 220), font, 2.8, COL_DIM, 3, cv2.LINE_AA)
    cv2.putText(img, "its own pedometer, running in parallel", (380, 250), font,
                0.42, COL_DIM, 1, cv2.LINE_AA)

    cv2.putText(img, f"{counter.cadence():.0f}", (700, 220), font, 2.0,
                COL_TEXT if walking else COL_DIM, 2, cv2.LINE_AA)
    cv2.putText(img, "STEPS PER MINUTE", (700, 130), font, 0.52, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, "walking" if walking else "still", (700, 250), font, 0.42,
                COL_TRACE if walking else COL_DIM, 1, cv2.LINE_AA)

    # The trace, with the threshold drawn across it. Seeing which spikes the
    # line lets through is the whole explanation of the threshold.
    top = max(12.0, counter.threshold * 2)
    def y_of(v):
        return int(PLOT_Y + PLOT_H - min(v, top) / top * PLOT_H)

    cv2.rectangle(img, (PLOT_X, PLOT_Y), (PLOT_X + PLOT_W, PLOT_Y + PLOT_H),
                  COL_GRID, 1, cv2.LINE_AA)
    for v in range(0, int(top) + 1, 5):
        y = y_of(v)
        cv2.line(img, (PLOT_X, y), (PLOT_X + PLOT_W, y), COL_GRID, 1, cv2.LINE_AA)
        cv2.putText(img, f"{v}", (PLOT_X - 28, y + 4), font, 0.40, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, "m/s2", (PLOT_X - 44, PLOT_Y - 8), font, 0.40, COL_DIM, 1,
                cv2.LINE_AA)

    ty = y_of(counter.threshold)
    cv2.line(img, (PLOT_X, ty), (PLOT_X + PLOT_W, ty), COL_THRESH, 1, cv2.LINE_AA)
    cv2.putText(img, f"threshold {counter.threshold:.1f}", (PLOT_X + PLOT_W - 150,
                ty - 8), font, 0.42, COL_THRESH, 1, cv2.LINE_AA)

    if len(trace) > 1:
        t0 = now - TRACE_S
        pts = [(int(PLOT_X + (t - t0) / TRACE_S * PLOT_W), y_of(v))
               for t, v in trace if t >= t0]
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts, dtype=np.int32)], False,
                          COL_TRACE, 1, cv2.LINE_AA)
        for st in counter.step_times[-40:]:
            if st >= t0:
                x = int(PLOT_X + (st - t0) / TRACE_S * PLOT_W)
                cv2.line(img, (x, PLOT_Y + PLOT_H - 14), (x, PLOT_Y + PLOT_H),
                         COL_STEP, 2, cv2.LINE_AA)

    cv2.putText(img, f"deaf for {counter.refractory * 1000:.0f} ms after each step "
                     f"— at most {60 / counter.refractory:.0f} steps a minute",
                (PLOT_X, PLOT_Y + PLOT_H + 32), font, 0.44, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, "R reset both counters    Q quit", (40, WIN_H - 20), font,
                0.44, COL_DIM, 1, cv2.LINE_AA)
    return img


def walk_stream(dev):
    """(time, |linear acceleration|, chip step count) as they arrive."""
    chip = 0
    t0 = None
    for rep in dev.reports(sensors=(SensorId.LINEAR_ACCELERATION,
                                    SensorId.STEP_COUNTER)):
        if t0 is None:
            t0 = time.monotonic()
        if rep.sensor_id == SensorId.STEP_COUNTER:
            chip = rep.steps
            continue
        mag = math.sqrt(rep.x ** 2 + rep.y ** 2 + rep.z ** 2)
        yield time.monotonic() - t0, mag, chip


def run_window(dev, args) -> None:
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    title = "DEPZ Lab 8 - pedometer"
    counter = StepCounter(args.threshold, args.refractory)
    chip_at_reset = 0
    trace: deque = deque(maxlen=int(TRACE_S * REPORT_HZ) + 50)
    last_draw = 0.0
    for now, mag, chip in walk_stream(dev):
        value, _ = counter.feed(mag, now)
        trace.append((now, value))
        if now - last_draw < 1 / 30:
            continue
        last_draw = now
        walking = bool(counter.step_times) and now - counter.step_times[-1] < QUIET_S
        try:
            cv2.imshow(title, draw_window(counter, trace, chip - chip_at_reset,
                                          now, walking))
        except cv2.error as exc:
            raise SystemExit(
                f"Cannot open a window ({exc.err.strip() or exc}).\n"
                "Add --terminal for the text readout, which needs no display."
            ) from None
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("r"):
            # The chip's counter cannot be zeroed, so remember where it was and
            # subtract — the same trick as a trip meter on a car.
            chip_at_reset = chip
            counter.steps = 0
            counter.step_times.clear()
    cv2.destroyAllWindows()


def run_terminal(dev, args) -> None:
    counter = StepCounter(args.threshold, args.refractory)
    last = 0.0
    print("Walk with the board on you. Ctrl-C to stop.\n")
    for now, mag, chip in walk_stream(dev):
        value, took = counter.feed(mag, now)
        if now - last < 0.1 and not took:
            continue
        last = now
        walking = bool(counter.step_times) and now - counter.step_times[-1] < QUIET_S
        print(f"  steps {counter.steps:4d}   chip {chip:4d}   "
              f"{counter.cadence():5.0f}/min   |a| {value:6.2f}   "
              f"{'walking' if walking else 'still  '}   ", end="\r", flush=True)


def run_check(dev, args) -> None:
    """Walk a known number of steps and see what each counter made of it.

    The lab starts counting on the first footfall and stops when the walking
    does, so there is nothing to press and no countdown to race.
    """
    counter = StepCounter(args.threshold, args.refractory)
    # Every combination runs on the same walk, so the table at the end is a
    # comparison and not a collection of separate outings.
    sweep = {(t, r): StepCounter(t, r)
             for t in SWEEP_THRESHOLDS for r in SWEEP_REFRACTORY}
    print(f"Walk {args.check} steps with the board in a pocket or against your "
          f"thigh,\nthen stand still. Counting starts on the first footfall.\n")
    chip_start = None
    peak = 0.0
    last_print = 0.0
    for now, mag, chip in walk_stream(dev):
        if chip_start is None:
            chip_start = chip
        value, _ = counter.feed(mag, now)
        for c in sweep.values():
            c.feed(mag, now)
        peak = max(peak, value)
        if now - last_print > 0.6:
            last_print = now
            state = "waiting for the first step"
            if counter.step_times:
                quiet = now - counter.step_times[-1]
                state = (f"walking, {quiet:.1f}s since the last step" if quiet < QUIET_S
                         else "stopped")
            print(f"    steps {counter.steps:3d}   chip {chip - chip_start:3d}   "
                  f"peak {peak:5.1f} m/s2   {state}        ", end="\r", flush=True)
        if counter.step_times and now - counter.step_times[-1] > QUIET_S:
            break

    chip_steps = chip - chip_start
    span = counter.step_times[-1] - counter.step_times[0]
    print(f"\n\n  you walked        {args.check}")
    print(f"  this lab counted  {counter.steps:3d}   "
          f"{counter.steps - args.check:+d}")
    print(f"  the chip counted  {chip_steps:3d}   {chip_steps - args.check:+d}")
    print(f"\n  over {span:.1f} s, {counter.cadence():.0f} steps per minute, "
          f"peak {peak:.1f} m/s2")
    print(f"  threshold {counter.threshold:.1f} m/s2, "
          f"deaf time {counter.refractory * 1000:.0f} ms")
    print("\n  what every setting would have counted on this same walk:\n")
    print("            " + "".join(f"  {r * 1000:.0f} ms" for r in SWEEP_REFRACTORY))
    for thr in SWEEP_THRESHOLDS:
        cells = ""
        for refr in SWEEP_REFRACTORY:
            got = sweep[(thr, refr)].steps
            mark = "*" if got == args.check else " "
            cells += f"  {got:4d}{mark}"
        print(f"  {thr:4.1f} m/s2 {cells}")
    print("\n  * hit your count exactly. Higher threshold rejects weak spikes, "
          "lower catches\n    soft steps; longer deaf time refuses double "
          "counts, shorter allows a faster walk.")
    print("  One walk is one sample — a setting that wins here can lose on the "
          "next walk.")


def has_display() -> bool:
    import os
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lab 8: a pedometer on a BNO086 IMU")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--terminal", action="store_true",
                   help="text readout instead of the window (for ssh)")
    p.add_argument("--check", type=int, metavar="STEPS",
                   help="walk this many steps, then compare the counts")
    p.add_argument("--threshold", type=float, default=THRESHOLD,
                   help=f"spike height that counts as a step, m/s2 "
                        f"(default {THRESHOLD})")
    p.add_argument("--refractory", type=float, default=REFRACTORY,
                   help=f"deaf time after each step, seconds "
                        f"(default {REFRACTORY})")
    args = p.parse_args(argv)

    if args.refractory <= 0 or args.threshold <= 0:
        print("--threshold and --refractory both have to be positive",
              file=sys.stderr)
        return 1

    try:
        dev = open_device(args.port) if args.port else open_device()
    except NoDepzDeviceError:
        print("No board found. Check: .venv/bin/depz-sensor list", file=sys.stderr)
        return 1
    except DepzError as exc:
        print(f"Cannot open the board: {exc}", file=sys.stderr)
        return 1

    if not isinstance(dev, Bno086):
        print(f"That board is a {type(dev).__name__}, not the IMU. "
              "Plug in the BNO086, or point --port at it.", file=sys.stderr)
        dev.close()
        return 1

    try:
        dev.enable(SensorId.LINEAR_ACCELERATION, REPORT_HZ)
        dev.enable(SensorId.STEP_COUNTER, CHIP_HZ)

        if args.check:
            run_check(dev, args)
        elif args.terminal or not has_display():
            if not args.terminal:
                print("no display — falling back to the text readout "
                      "(this is what --terminal does)\n")
            run_terminal(dev, args)
        else:
            run_window(dev, args)
    except KeyboardInterrupt:
        print("\nstopped")
    except DepzError as exc:
        print(f"\nThe board stopped talking: {exc}", file=sys.stderr)
        return 1
    finally:
        for sensor in (SensorId.LINEAR_ACCELERATION, SensorId.STEP_COUNTER):
            dev.disable(sensor)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
