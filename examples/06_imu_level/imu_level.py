#!/usr/bin/env python3
"""Example project 6 — A spirit level (BNO086 IMU).

The first sensor in this series measured distance, the second measured 64
distances at once. This one measures nothing you can put a tape on: it reports
which way is down.

That is enough to build a spirit level, and a spirit level is the honest place
to start with an IMU, because it uses the one thing the chip knows without any
help. Gravity always points the same way, it is always there, and the
accelerometer feels it as an acceleration of about 9.8 m/s² pointing up — the
push of the table holding the board against the fall. Tip the board and that
vector swings in the board's own axes. The angle it swings through is the tilt.
No calibration, no magnetometer, no quaternion — those start in example project 7.

Two things bite here, and this example project is about both.

The first is that the board does not know what "level" means. It knows where
down is; whether your table is horizontal is not its business. So the example project
starts by measuring the board's own axes and lets you press Z to declare the
current pose flat — the same thing you do when you rest a real spirit level on
a surface and read what it says.

The second is that the vector is not perfect. Its length should always be
9.807 m/s², the strength of gravity, no matter how the board lies. On this
bench it came out 9.650 flat, 9.526 nose-up and 9.920 on its side. The
accelerometer has an offset on each axis, and an offset is a lie about
direction: about two degrees of it. Pressing Z cancels most of that, which is
why the zeroed reading is the one to trust — and why --raw is worth a look
first, so you can see what you are being saved from.

Run:
    python examples/06_imu_level/imu_level.py                 live bubble in a window
    python examples/06_imu_level/imu_level.py --range 5       finer scale, +/-5 degrees
    python examples/06_imu_level/imu_level.py --zero          start already zeroed
    python examples/06_imu_level/imu_level.py --check         average one pose, print it
    python examples/06_imu_level/imu_level.py --check --truth 3.8    against a known wedge
    python examples/06_imu_level/imu_level.py --terminal      text output, for ssh
"""

from __future__ import annotations

import argparse
import math
import statistics
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
        "    .venv/bin/python examples/06_imu_level/imu_level.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

# How often the chip sends the gravity vector. It is a fused, already-smoothed
# quantity, not a raw sample, so there is nothing to gain from asking faster.
REPORT_HZ = 50

# Standard gravity. Not a reading and not a constant of this board — it is what
# the length of the vector is supposed to be, and comparing against it is how
# the example project shows the accelerometer's offset.
G_STANDARD = 9.807

# The board's axes, measured on the bench rather than read off a datasheet —
# the ToF matrix in example project 4 arrived rotated a quarter turn, so nothing here is
# taken on trust. Board flat on the table, chip up, USB cable towards you:
#
#   lifting the FAR edge   grew +Y   ->  +Y points away from you
#   lifting the RIGHT edge grew +X   ->  +X points to your right
#   lying flat             gave +Z   ->  +Z points up, out of the board
#
# An ordinary right-handed set, in the orientation you would guess. Worth
# checking on your own board anyway: turn it and watch which number moves.
AXIS_RIGHT = np.array([1.0, 0.0, 0.0])
AXIS_FORWARD = np.array([0.0, 1.0, 0.0])
AXIS_UP = np.array([0.0, 0.0, 1.0])

# Samples kept for zeroing and for the noise figure — half a second of them.
# Zeroing on a single report would bake that report's noise into every angle
# measured afterwards.
BUFFER = REPORT_HZ // 2

# Below this the example project calls the surface level and paints the bubble green. It is
# a decision, not a measurement: a builder's level is graded around 0.5 mm/m,
# which is 0.03 degrees, and this sensor cannot see that. 0.20 is a little
# above the noise measured on the bench, so a still board reads LEVEL steadily
# instead of flickering.
LEVEL_DEG = 0.20

# ── window geometry, BGR colours (OpenCV works in that order) ────────────────
WIN_W, WIN_H = 940, 600
EYE_CX, EYE_CY, EYE_R = 262, 312, 186

COL_BG = (250, 249, 246)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_EYE = (236, 234, 229)
COL_RING = (206, 203, 197)
COL_BUBBLE = (65, 69, 214)      # off level: the warm end of the example project 4 ramp
COL_LEVEL = (130, 170, 86)      # within LEVEL_DEG: the calm end
COL_CROSS = (186, 183, 177)


def unit(v: np.ndarray) -> np.ndarray:
    """The direction of `v`, length thrown away."""
    return v / np.linalg.norm(v)


class Level:
    """The whole example project: a gravity vector in, two angles and a bubble out.

    Everything is measured against a reference direction — the one the level
    calls "up". Unzeroed that is the board's own +Z, so the reading is the tilt
    of the board itself; after Z it is whatever pose you declared flat, and the
    reading is the tilt away from that surface.
    """

    def __init__(self) -> None:
        self.reference: np.ndarray | None = None
        self.recent: deque[np.ndarray] = deque(maxlen=BUFFER)

    # ── the reference pose ───────────────────────────────────────────────────
    def zero(self) -> bool:
        """Call the current pose flat. False if there is not enough to average."""
        if len(self.recent) < self.recent.maxlen:
            return False
        self.reference = unit(np.mean(self.recent, axis=0))
        return True

    def reset(self) -> None:
        """Back to the board's own axes."""
        self.reference = None

    @property
    def zeroed(self) -> bool:
        return self.reference is not None

    # ── the geometry ─────────────────────────────────────────────────────────
    def _basis(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Three perpendicular directions: right, forward, and up.

        Up is the reference. The other two have to lie in the surface that is
        perpendicular to it, and they are built from the board's own axes so
        that "right" on screen keeps meaning the right edge of the board.

        Taking +X and removing the part of it that points along up leaves the
        part lying in the surface — the same move as dropping a plumb line from
        a stick and keeping its shadow on the floor. That fails when the stick
        is itself vertical, i.e. when the board is standing on its side and +X
        is the up direction, so in that case +Y is used as the seed instead.
        """
        up = self.reference if self.reference is not None else AXIS_UP
        seed = AXIS_RIGHT if abs(float(np.dot(AXIS_RIGHT, up))) < 0.9 else AXIS_FORWARD
        right = unit(seed - up * float(np.dot(seed, up)))
        forward = np.cross(up, right)
        return right, forward, up

    def angles(self, gravity: np.ndarray) -> tuple[float, float, float]:
        """(pitch, roll, tilt) in degrees for one gravity vector.

        pitch — far edge up is positive; roll — right edge up is positive;
        tilt — the total, the angle between where up is now and where the
        reference says it should be. Tilt is not pitch plus roll: two tilts at
        right angles combine like the sides of a right triangle, so leaning a
        board 3 degrees forward and 4 degrees sideways leaves it 5 degrees off.

        The vector's length divides out of every one of these — an angle is a
        ratio of components. That is why the scale error visible in |g| costs
        nothing here, while the per-axis offset behind it costs about a degree.
        """
        right, forward, up = self._basis()
        u = unit(gravity)
        cr = float(np.dot(u, right))
        cf = float(np.dot(u, forward))
        cu = float(np.dot(u, up))
        return (
            math.degrees(math.atan2(cf, cu)),
            math.degrees(math.atan2(cr, cu)),
            math.degrees(math.acos(max(-1.0, min(1.0, cu)))),
        )

    def feed(self, gravity: np.ndarray) -> tuple[float, float, float]:
        """Remember the sample, return its angles."""
        self.recent.append(gravity)
        return self.angles(gravity)

    def noise_deg(self) -> float:
        """Spread of the recent samples, as an angle.

        How much the reading wanders while nothing moves — the same kind of
        number as the 4.3 mm cell noise example project 4 measured, and the reason
        LEVEL_DEG is not set tighter.
        """
        if len(self.recent) < 4:
            return 0.0
        mean = unit(np.mean(self.recent, axis=0))
        spread = [
            math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(unit(v), mean))))))
            for v in self.recent
        ]
        return max(spread)


def gravity_stream(dev):
    """Gravity vectors from the board, as numpy triples."""
    for rep in dev.reports(sensors=SensorId.GRAVITY):
        yield np.array([rep.x, rep.y, rep.z]), rep.accuracy


# ── the window ───────────────────────────────────────────────────────────────

def draw_level(pitch: float, roll: float, tilt: float, span: float,
               status: str, hint: str, noise: float, g_len: float, accuracy: int,
               advice: tuple[str, str] = ("", "")):
    """Render one frame of the level.

    Kept separate from the loop so a screenshot for the README can be produced
    headless — same pixels the example project draws, no window popping up.
    """
    # OpenCV ships a Qt build with no fonts of its own, so Qt prints a font
    # warning on every start. Nothing here depends on Qt fonts — every label in
    # the window is drawn by cv2.putText — so silence that one uncategorised
    # warning and keep the console readable. setdefault, so an explicit
    # QT_LOGGING_RULES from the environment still wins.
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    img = np.full((WIN_H, WIN_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    level = tilt < LEVEL_DEG

    cv2.putText(img, "DEPZ - Example project 6 - Spirit level", (40, 44),
                font, 0.72, COL_TEXT, 1, cv2.LINE_AA)
    cv2.putText(img, status, (40, 72), font, 0.46, COL_DIM, 1, cv2.LINE_AA)

    # The eye. Rings are the scale; the outermost one is the full span.
    cv2.circle(img, (EYE_CX, EYE_CY), EYE_R, COL_EYE, -1, cv2.LINE_AA)
    for i in (1, 2, 3, 4):
        r = int(EYE_R * i / 4)
        cv2.circle(img, (EYE_CX, EYE_CY), r, COL_RING, 1, cv2.LINE_AA)
        if i in (2, 4):
            label = f"{span * i / 4:g}"
            cv2.putText(img, label, (EYE_CX + 8, EYE_CY - r + 16),
                        font, 0.40, COL_DIM, 1, cv2.LINE_AA)
    cv2.line(img, (EYE_CX - EYE_R, EYE_CY), (EYE_CX + EYE_R, EYE_CY),
             COL_CROSS, 1, cv2.LINE_AA)
    cv2.line(img, (EYE_CX, EYE_CY - EYE_R), (EYE_CX, EYE_CY + EYE_R),
             COL_CROSS, 1, cv2.LINE_AA)
    # The tolerance circle, so "level" is something you can see, not just read.
    cv2.circle(img, (EYE_CX, EYE_CY), max(4, int(EYE_R * LEVEL_DEG / span)),
               COL_LEVEL if level else COL_RING, 1, cv2.LINE_AA)

    # The bubble sits on the high side, the way the air pocket in a glass tube
    # floats to the top. Lift the right edge and it goes right.
    scale = EYE_R / span
    bx, by = roll * scale, -pitch * scale
    reach = math.hypot(bx, by)
    if reach > EYE_R - 14:                       # off scale: park it on the rim
        bx, by = bx / reach * (EYE_R - 14), by / reach * (EYE_R - 14)
    colour = COL_LEVEL if level else COL_BUBBLE
    cv2.circle(img, (EYE_CX + int(bx), EYE_CY + int(by)), 15, colour, -1, cv2.LINE_AA)
    cv2.circle(img, (EYE_CX + int(bx), EYE_CY + int(by)), 15, COL_BG, 1, cv2.LINE_AA)

    for text, x, y in (("far edge up", EYE_CX - 46, EYE_CY - EYE_R - 14),
                       ("near edge up", EYE_CX - 50, EYE_CY + EYE_R + 30)):
        cv2.putText(img, text, (x, y), font, 0.42, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, "right", (EYE_CX + EYE_R + 10, EYE_CY - 2),
                font, 0.42, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, "edge up", (EYE_CX + EYE_R + 10, EYE_CY + 18),
                font, 0.42, COL_DIM, 1, cv2.LINE_AA)

    # The numbers.
    tx = 570
    cv2.putText(img, "TILT", (tx, 150), font, 0.50, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, f"{tilt:.2f}", (tx, 226), font, 2.4, colour, 3, cv2.LINE_AA)
    cv2.putText(img, "degrees off the reference", (tx, 254),
                font, 0.44, COL_DIM, 1, cv2.LINE_AA)
    if level:
        cv2.putText(img, "LEVEL", (tx + 200, 150), font, 0.70, COL_LEVEL, 2, cv2.LINE_AA)

    for i, (name, value, note) in enumerate((
            ("pitch", pitch, "far edge up +"),
            ("roll", roll, "right edge up +"))):
        y = 320 + i * 54
        cv2.putText(img, name, (tx, y), font, 0.52, COL_TEXT, 1, cv2.LINE_AA)
        cv2.putText(img, f"{value:+7.2f}", (tx + 80, y), font, 0.78, COL_TEXT, 1,
                    cv2.LINE_AA)
        cv2.putText(img, note, (tx + 230, y), font, 0.42, COL_DIM, 1, cv2.LINE_AA)

    for i, line in enumerate(advice):
        cv2.putText(img, line, (tx, 452 + i * 24), font, 0.44, COL_DIM, 1, cv2.LINE_AA)

    # The health line: what the sensor says about itself.
    # |g| is the sensor's own confession. It has to be 9.807 in every pose,
    # so whatever it is off by is the accelerometer's offset — the same offset
    # that tilts the vector and costs the level its accuracy.
    cv2.putText(img, f"|g| {g_len:.3f} m/s2   off by {100 * (g_len / G_STANDARD - 1):+.1f}% "
                     f"from {G_STANDARD}   accuracy {accuracy}/3   "
                     f"noise {noise * 60:.1f} arcmin",
                (40, WIN_H - 54), font, 0.44, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(img, hint, (40, WIN_H - 26), font, 0.44, COL_DIM, 1, cv2.LINE_AA)
    return img


def run_window(dev, args) -> None:
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    title = "DEPZ Example project 6 - spirit level"
    level = Level()
    pending_zero = args.zero
    last_draw = 0.0
    for gravity, accuracy in gravity_stream(dev):
        pitch, roll, tilt = level.feed(gravity)
        if pending_zero and level.zero():
            pending_zero = False
            continue

        # The chip sends 50 vectors a second; redrawing every one of them burns
        # frames on motion no eye can follow. 30 a second is already smooth.
        now = time.monotonic()
        if now - last_draw < 1 / 30:
            continue
        last_draw = now

        if level.zeroed:
            status = "zeroed — angles are measured from the pose you called flat"
            # The oldest trick in the trade, and the only way a level can be
            # checked without a second level: turn it end for end on the same
            # spot. A perfect one reads the same both ways, so whatever it
            # gains is twice its own error.
            advice = ("now turn the board 180 degrees on the same spot:",
                      "half of whatever it reads is the level's own error")
        else:
            status = "not zeroed — angles are measured from the board's own +Z"
            advice = ("press Z with the board resting on the surface",
                      "you want to call flat")
        frame = draw_level(pitch, roll, tilt, args.range, status,
                           "Z zero here    R back to the board's axes    Q quit",
                           level.noise_deg(), float(np.linalg.norm(gravity)),
                           accuracy, advice)
        try:
            cv2.imshow(title, frame)
        except cv2.error as exc:
            # No display to draw on — over ssh, or in a container. Say so in one
            # line and point at the mode that does work there, instead of
            # dropping an OpenCV stack trace on someone.
            raise SystemExit(
                f"Cannot open a window ({exc.err.strip() or exc}).\n"
                "Add --terminal for the text readout, which needs no display."
            ) from None
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("z"):
            pending_zero = True
        if key == ord("r"):
            level.reset()
    cv2.destroyAllWindows()


# ── the text readout ─────────────────────────────────────────────────────────

def run_terminal(dev, args) -> None:
    level = Level()
    pending_zero = args.zero
    last_print = 0.0
    print("tilt is the total; pitch is far-edge-up, roll is right-edge-up.")
    print("Ctrl-C to stop.\n")
    for gravity, accuracy in gravity_stream(dev):
        pitch, roll, tilt = level.feed(gravity)
        if pending_zero and level.zero():
            pending_zero = False
            print("zeroed on the starting pose\n")
            continue
        now = time.monotonic()
        if now - last_print < 0.1:
            continue
        last_print = now
        mark = "  LEVEL" if tilt < LEVEL_DEG else ""
        print(f"  tilt {tilt:6.2f}   pitch {pitch:+7.2f}   roll {roll:+7.2f}   "
              f"|g| {np.linalg.norm(gravity):.3f}   acc {accuracy}{mark}   ",
              end="\r", flush=True)


# ── one pose, measured ───────────────────────────────────────────────────────

def wait_for(prompt: str) -> bool:
    """Wait for Enter. False when there is nobody to ask — piped input, cron.

    The first version of this example project's bench work used a countdown instead, and it
    failed the way countdowns do: the reading was taken before the board was in
    place, and nothing on screen said so. A person pressing Enter when ready
    cannot be rushed.
    """
    if not sys.stdin.isatty():
        return False
    try:
        input(prompt)
    except EOFError:
        return False
    return True


def average_pose(dev, level: Level, count: int) -> tuple[list, list]:
    """Read `count` reports, returning their angles and vector lengths.

    A fresh stream each call, so a pose is measured from reports that arrived
    after it was set up rather than from whatever piled up while somebody was
    moving a plank.
    """
    angles, lengths = [], []
    for gravity, _accuracy in gravity_stream(dev):
        angles.append(level.feed(gravity))
        lengths.append(float(np.linalg.norm(gravity)))
        if len(angles) >= count:
            break
    return angles, lengths


def run_check(dev, args) -> None:
    """Average one pose and print it, with the spread that goes with it.

    This is the mode the README's numbers come from. Rest the board on a
    straight plank, prop one end up by a known amount, and the angle follows
    from a right triangle: the rise over the length of the plank is the sine of
    the tilt. With --zero the example project asks for the flat pose first, because an
    angle has to be measured from somewhere.
    """
    level = Level()

    if args.zero:
        if wait_for("Rest the board on the surface you call flat, then press Enter… "):
            average_pose(dev, level, BUFFER)
            level.zero()
            print("zeroed on that surface")
            wait_for("Now set up the angle you want to measure, then press Enter… ")
        else:
            print("--zero needs a terminal to ask on; measuring from the "
                  "board's own axes instead")

    print(f"Hold the pose. Collecting {args.samples} readings "
          f"({args.samples / REPORT_HZ:.1f} s)…")
    samples, lengths = average_pose(dev, level, args.samples)

    pitch = statistics.fmean(s[0] for s in samples)
    roll = statistics.fmean(s[1] for s in samples)
    tilt = statistics.fmean(s[2] for s in samples)
    spread = max(s[2] for s in samples) - min(s[2] for s in samples)

    print(f"\n  tilt   {tilt:7.3f} deg   spread over the run {spread * 60:.1f} arcmin")
    print(f"  pitch  {pitch:+7.3f} deg   (far edge up is positive)")
    print(f"  roll   {roll:+7.3f} deg   (right edge up is positive)")
    print(f"  |g|    {statistics.fmean(lengths):7.3f} m/s2  "
          f"against {G_STANDARD} — the difference is the accelerometer's, not gravity's")

    if args.truth is not None:
        error = tilt - args.truth
        print(f"\n  tape says {args.truth:.3f} deg, sensor says {tilt:.3f} deg — "
              f"off by {error * 60:+.1f} arcmin ({error:+.3f} deg)")
        if not level.zeroed:
            # Measured from the board's own +Z, so the accelerometer's offset
            # is in there whole. --zero takes out the part of it the flat pose
            # and the tilted one have in common, which is most of it.
            print("  (measured from the board's own axes, offset and all — "
                  "add --zero to measure from a surface instead)")


def has_display() -> bool:
    """Is there anywhere to open a window?

    Over ssh there is not, and the text readout is the answer rather than an
    OpenCV crash.
    """
    import os
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Example project 6: a spirit level on a BNO086 IMU")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--terminal", action="store_true",
                   help="text readout instead of the window (for ssh)")
    p.add_argument("--range", type=float, default=10.0,
                   help="degrees from the middle of the eye to its rim (default 10)")
    p.add_argument("--zero", action="store_true",
                   help="call the starting pose flat; with --check, ask for the "
                        "flat surface first")
    p.add_argument("--check", action="store_true",
                   help="average one pose and print it")
    p.add_argument("--truth", type=float,
                   help="the angle the pose really is, in degrees, for --check")
    p.add_argument("--samples", type=int, default=100,
                   help="readings to average in --check (100 = two seconds)")
    args = p.parse_args(argv)

    if args.range <= 0:
        print("--range has to be a positive number of degrees", file=sys.stderr)
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
        # Gravity is a fused output: the chip separates the steady pull from
        # whatever else is shaking the board and reports each one apart. Asking
        # for it costs one line, and unlike the quaternion of example project 7 it is
        # trustworthy from the first report — nothing here needs calibrating.
        dev.enable_gravity(hz=REPORT_HZ)

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
        dev.disable(SensorId.GRAVITY)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
