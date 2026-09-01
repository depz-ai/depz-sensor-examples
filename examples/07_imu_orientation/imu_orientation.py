#!/usr/bin/env python3
"""Example project 7 — Orientation in space (BNO086 IMU).

Example project 6 asked the sensor one question — where is down — and got an answer that
was trustworthy from the first report. This example project asks the whole question: how is
the board turned, in all three directions at once. The answer arrives as a
quaternion, and the quaternion is where the trouble starts.

Not because the maths is hard. Because it looks fine when it is not.

Switch the board on and the rotation vector immediately reports four tidy
numbers. Nothing blinks, nothing complains. Ask the chip how sure it is and it
says its heading could be out by **180 degrees** — it does not know which way
you are pointing at all, and it will keep saying so, politely, in a field most
people never read. That field is the subject of this example project as much as the
quaternion is.

The other half of the example project is what a quaternion turns into. Roll, pitch and yaw
are what people actually want, and converting is four lines of trigonometry —
but those three numbers have a hole in them. Stand the board on its edge and
they blow up while the quaternion sails on unbothered. You can watch it happen
in the window: the board on screen keeps turning smoothly, and the numbers next
to it go mad.

Run:
    python examples/07_imu_orientation/imu_orientation.py              live board in a window
    python examples/07_imu_orientation/imu_orientation.py --game       ignore the compass
    python examples/07_imu_orientation/imu_orientation.py --calibrate   walk through calibration
    python examples/07_imu_orientation/imu_orientation.py --drift 120   leave it still, measure the drift
    python examples/07_imu_orientation/imu_orientation.py --terminal    text output, for ssh
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
        "    .venv/bin/python examples/07_imu_orientation/imu_orientation.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

REPORT_HZ = 50

# Two quaternions, and the difference between them is the example project's second lesson.
#
# ROTATION_VECTOR uses the magnetometer, so its yaw is measured from magnetic
# north — an angle that means something to the outside world, and that the chip
# has to earn by calibrating.
#
# GAME_ROTATION_VECTOR leaves the magnetometer out. Its yaw is measured from
# wherever the board happened to point when the vector started, so it means
# nothing to anyone else — but it is available instantly and it never lurches
# sideways because someone put a screwdriver on the table.
FUSED = SensorId.ROTATION_VECTOR
GAME = SensorId.GAME_ROTATION_VECTOR

# Past this pitch, yaw and roll stop being separate quantities — see the
# gimbal-lock section of the README. 75° leaves room to see it coming.
GIMBAL_WARN_DEG = 75.0

# Tilt change that means somebody touched the board during a drift run. Well
# above the 0.08° of noise example project 6 measured, well below any real nudge.
MOVED_DEG = 1.0

# The board, in its own axes, as a box to draw: +X right, +Y away from you,
# +Z up, exactly as measured in example project 6. Half-sizes, in units where the board's
# long side is 1.
BOARD = (0.50, 0.34, 0.045)
USB_HALF = (0.16, 0.05, 0.055)   # the connector, drawn on the -Y edge

# ── window geometry, BGR colours ─────────────────────────────────────────────
WIN_W, WIN_H = 980, 620
SCENE_CX, SCENE_CY, SCENE_SCALE = 310, 320, 230

COL_BG = (250, 249, 246)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_WARN = (65, 69, 214)
COL_GOOD = (130, 170, 86)
COL_FLOOR = (222, 219, 213)
COL_NORTH = (70, 130, 205)
# Which face is which has to be readable in a glance, or the picture is
# decoration. The component side is the pale one and carries a chip; the
# underside is nearly black. Turn the board over and you can see that you did.
COL_TOP = (150, 205, 130)        # component side — the pale one
COL_BOTTOM = (46, 66, 40)        # underside — nearly black
COL_SIDE = (92, 132, 80)
COL_CHIP = (58, 58, 62)          # the sensor itself, drawn on the top face
COL_USB = (170, 170, 176)        # the connector: bare metal, and the nose of the board
COL_EDGE = (250, 249, 246)


def ypr_deg(q) -> tuple[float, float, float]:
    """Quaternion to yaw, pitch, roll in degrees.

    Three turns applied in order — yaw about the vertical, then pitch, then
    roll — which is the convention everything from aircraft to phone APIs uses.
    The middle one is an arcsine, and that is where the trouble in this example project
    comes from: an arcsine has nowhere to go past ±90°.
    """
    x, y, z, w = q
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    return tuple(math.degrees(v) for v in (yaw, pitch, roll))


def quat_matrix(q) -> np.ndarray:
    """Quaternion to a 3x3 rotation matrix — the form you can multiply points by.

    This is the honest way to draw the board: no angles are computed, so
    nothing here can blow up at 90°. The picture in the window keeps working
    exactly where the numbers beside it stop.
    """
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def wrap180(deg: float) -> float:
    """An angle folded into −180…180, so 359° and −1° are the same thing."""
    return (deg + 180.0) % 360.0 - 180.0


# ── the camera ───────────────────────────────────────────────────────────────

def camera_basis(eye=(1.0, -1.6, 0.75)):
    """Where the scene is watched from: front, right and a little above.

    Returns three directions — right, up, forward — so a point in world
    coordinates can be turned into a point on screen plus a depth to sort by.
    """
    forward = np.array(eye, dtype=float)
    forward /= np.linalg.norm(forward)
    right = np.cross((0.0, 0.0, 1.0), forward)
    right /= np.linalg.norm(right)
    up = np.cross(forward, right)
    return right, up, forward


RIGHT, UP, FORWARD = camera_basis()


def project(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """World points to (screen pixels, depth). Nearer things have larger depth."""
    xs = points @ RIGHT * SCENE_SCALE + SCENE_CX
    ys = -(points @ UP) * SCENE_SCALE + SCENE_CY
    return np.stack([xs, ys], axis=1).astype(np.int32), points @ FORWARD


def box_faces(half: tuple[float, float, float], centre=(0.0, 0.0, 0.0)):
    """The six faces of a box, each as four corners in the box's own axes."""
    hx, hy, hz = half
    cx, cy, cz = centre
    c = [(cx + sx * hx, cy + sy * hy, cz + sz * hz)
         for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    # index bits: x, y, z — 0 is the minus side
    idx = {
        "top": (1, 3, 7, 5), "bottom": (0, 4, 6, 2),
        "far": (2, 6, 7, 3), "near": (0, 1, 5, 4),
        "left": (0, 2, 3, 1), "right": (4, 5, 7, 6),
    }
    return {name: np.array([c[i] for i in ids]) for name, ids in idx.items()}


def draw_scene(img, rot: np.ndarray, north_known: bool) -> None:
    """The floor, the north arrow and the board, painted far to near."""
    import cv2

    # The floor: a grid on the horizontal plane, so a tilt has something to be
    # tilted against. Without it a rotating box is just a rotating box.
    grid, span = 3, 0.9
    for i in range(-grid, grid + 1):
        t = span * i / grid
        for a, b in (((t, -span, -0.38), (t, span, -0.38)),
                     ((-span, t, -0.38), (span, t, -0.38))):
            pts, _ = project(np.array([a, b]))
            cv2.line(img, tuple(pts[0]), tuple(pts[1]), COL_FLOOR, 1, cv2.LINE_AA)

    # Which way the yaw is measured from. With the magnetometer that is north;
    # without it, it is wherever the board pointed when the stream started.
    tip, _ = project(np.array([(0.0, 0.0, -0.38), (0.0, 0.85, -0.38)]))
    cv2.arrowedLine(img, tuple(tip[0]), tuple(tip[1]), COL_NORTH, 2, cv2.LINE_AA,
                    tipLength=0.12)
    label, _ = project(np.array([(0.06, 0.95, -0.38)]))
    cv2.putText(img, "N" if north_known else "start", tuple(label[0]),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, COL_NORTH, 1, cv2.LINE_AA)

    faces = []
    for name, corners in box_faces(BOARD).items():
        colour = COL_TOP if name == "top" else COL_BOTTOM if name == "bottom" else COL_SIDE
        faces.append((corners @ rot.T, colour))
    for name, corners in box_faces(USB_HALF, centre=(0.0, -BOARD[1] - 0.04, 0.0)).items():
        faces.append((corners @ rot.T, COL_USB))
    # Painter's algorithm: sort by depth and paint the far ones first. With six
    # opaque faces that is all the hidden-surface removal a box needs.
    for corners, colour in sorted(faces, key=lambda f: project(f[0])[1].mean()):
        pts, _ = project(corners)
        cv2.fillConvexPoly(img, pts, colour, cv2.LINE_AA)
        cv2.polylines(img, [pts], True, COL_EDGE, 1, cv2.LINE_AA)

    # The chip on the component side, drawn last so the face underneath cannot
    # win the depth sort and hide it — but only while that face is turned
    # towards the camera. Flip the board over and the chip goes away with it,
    # which is the whole point of drawing it: it says which side you are seeing.
    up_now = rot @ np.array([0.0, 0.0, 1.0])
    if float(np.dot(up_now, FORWARD)) > 0:
        chip = np.array([(-0.07, -0.04, BOARD[2]), (0.19, -0.04, BOARD[2]),
                         (0.19, 0.16, BOARD[2]), (-0.07, 0.16, BOARD[2])])
        pts, _ = project(chip @ rot.T)
        cv2.fillConvexPoly(img, pts, COL_CHIP, cv2.LINE_AA)


def draw_window(state: dict, args) -> None:
    """Render one frame. Separate from the loop so the README shot is headless."""
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    img = np.full((WIN_H, WIN_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    using_game = args.game
    q = state["game_q"] if using_game else state["fused_q"]
    yaw, pitch, roll = ypr_deg(q)
    locked = abs(pitch) > GIMBAL_WARN_DEG

    cv2.putText(img, "DEPZ - Example project 7 - Orientation in space", (40, 44),
                font, 0.72, COL_TEXT, 1, cv2.LINE_AA)
    cv2.putText(img, "game rotation vector — no compass, yaw is relative to the start"
                if using_game else
                "rotation vector — yaw is measured from magnetic north",
                (40, 72), font, 0.46, COL_DIM, 1, cv2.LINE_AA)

    draw_scene(img, quat_matrix(q), north_known=not using_game)

    tx = 600
    for i, (name, value) in enumerate((("yaw", yaw), ("pitch", pitch), ("roll", roll))):
        y = 150 + i * 62
        shaky = locked and name in ("yaw", "roll")
        cv2.putText(img, name, (tx, y), font, 0.52, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(img, "deg", (tx + 250, y + 6), font, 0.42, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(img, f"{value:+7.1f}", (tx + 76, y + 6), font, 1.15,
                    COL_WARN if shaky else COL_TEXT, 2, cv2.LINE_AA)

    if locked:
        cv2.putText(img, "near vertical: yaw and roll are the same turn here",
                    (tx, 348), font, 0.44, COL_WARN, 1, cv2.LINE_AA)
        cv2.putText(img, "the board on the left is still correct",
                    (tx, 370), font, 0.44, COL_DIM, 1, cv2.LINE_AA)

    # How much the chip trusts itself. This is the number people never read.
    acc = state["game_acc"] if using_game else state["fused_acc"]
    y0 = 420
    cv2.putText(img, f"accuracy {acc}/3", (tx, y0), font, 0.52,
                COL_GOOD if acc >= 2 else COL_WARN, 1, cv2.LINE_AA)
    if using_game:
        cv2.putText(img, "no heading to be wrong about", (tx + 130, y0),
                    font, 0.42, COL_DIM, 1, cv2.LINE_AA)
    else:
        est = state["fused_est"]
        cv2.putText(img, f"heading could be out by {est:.0f} deg", (tx + 130, y0),
                    font, 0.42, COL_GOOD if est < 15 else COL_WARN, 1, cv2.LINE_AA)

    if state["fused_q"] is not None and state["game_q"] is not None:
        fy, fp, fr = ypr_deg(state["fused_q"])
        gy, gp, gr = ypr_deg(state["game_q"])
        cv2.putText(img, f"compass adds {wrap180(fy - gy):+.1f} deg to the game "
                         f"vector's yaw", (tx, y0 + 30), font, 0.44, COL_DIM, 1,
                    cv2.LINE_AA)
        # Measured, not asserted. Away from vertical the two vectors agree on
        # tilt to a fraction of a degree, because gravity gives it to both. In
        # the gimbal-locked pose they do not, and the gap belongs on screen.
        gap = max(abs(wrap180(fp - gp)), abs(wrap180(fr - gr)))
        cv2.putText(img, f"pitch and roll agree to {gap:.1f} deg — gravity gives "
                         f"those to both", (tx, y0 + 52), font, 0.44,
                    COL_WARN if gap > 2 else COL_DIM, 1, cv2.LINE_AA)

    keys = [
        ("G", "show the compass vector (yaw from north)" if using_game
              else "show the game vector (no compass at all)"),
        ("T", "tare: call this pose zero"),
        ("R", "undo the tare"),
        ("S", "save the calibration into the board"),
        ("Q", "quit"),
    ]
    x = 40
    for key, text in keys:
        cv2.putText(img, key, (x, WIN_H - 28), font, 0.46, COL_TEXT, 1, cv2.LINE_AA)
        cv2.putText(img, text, (x + 18, WIN_H - 28), font, 0.42, COL_DIM, 1, cv2.LINE_AA)
        x += 28 + int(cv2.getTextSize(text, font, 0.42, 1)[0][0])

    if state.get("tared"):
        cv2.putText(img, "tared — angles are measured from the pose you tared, "
                         "not from north", (40, WIN_H - 54), font, 0.44,
                    COL_GOOD, 1, cv2.LINE_AA)
    # Pressing a key should say something on the screen the user is looking at,
    # not only in a terminal behind the window.
    if state.get("notice") and time.monotonic() < state.get("notice_until", 0):
        cv2.putText(img, state["notice"], (40, 104), font, 0.52, COL_GOOD, 1,
                    cv2.LINE_AA)
    return img


# ── streaming ────────────────────────────────────────────────────────────────

def new_state() -> dict:
    return {"fused_q": None, "game_q": None, "fused_acc": 0, "game_acc": 0,
            "fused_est": 180.0, "mag_acc": 0, "tared": False,
            "notice": "", "notice_until": 0.0}


def notify(state: dict, text: str, seconds: float = 2.5) -> None:
    """Say it in the window, where the person is actually looking."""
    state["notice"] = text
    state["notice_until"] = time.monotonic() + seconds
    print(text)


def pump(dev, state: dict, sensors):
    """Yield once per report, keeping `state` current."""
    for rep in dev.reports(sensors=sensors):
        if rep.sensor_id == SensorId.MAGNETOMETER:
            state["mag_acc"] = rep.accuracy
            yield rep
            continue
        q = (rep.i, rep.j, rep.k, rep.real)
        if rep.sensor_id == FUSED:
            state["fused_q"] = q
            state["fused_acc"] = rep.accuracy
            state["fused_est"] = math.degrees(rep.accuracy_rad or math.pi)
        else:
            state["game_q"] = q
            state["game_acc"] = rep.accuracy
        yield rep


def run_window(dev, args) -> None:
    import os
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2

    title = "DEPZ Example project 7 - orientation"
    state = new_state()
    last_draw = 0.0
    for _rep in pump(dev, state, (FUSED, GAME)):
        if state["fused_q"] is None or state["game_q"] is None:
            continue
        now = time.monotonic()
        if now - last_draw < 1 / 30:
            continue
        last_draw = now
        try:
            cv2.imshow(title, draw_window(state, args))
        except cv2.error as exc:
            raise SystemExit(
                f"Cannot open a window ({exc.err.strip() or exc}).\n"
                "Add --terminal for the text readout, which needs no display."
            ) from None
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            break
        if key == ord("g"):
            args.game = not args.game
            notify(state, "game vector — yaw from where it started"
                          if args.game else
                          "compass vector — yaw from magnetic north")
        if key == ord("t"):
            # The chip's own zeroing: it remembers this pose and reports every
            # later one relative to it. Unlike example project 6's Z key this happens on
            # the sensor, and persist_tare() would keep it across a power cycle.
            dev.tare_now()
            state["tared"] = True
            notify(state, "tared — this pose is now zero")
        if key == ord("r"):
            # Undo: a tare is stored as a rotation the chip applies to every
            # report, so setting that rotation to "no rotation at all" clears it.
            dev.set_reorientation(0.0, 0.0, 0.0, 1.0)
            state["tared"] = False
            notify(state, "tare undone — back to north")
        if key == ord("s"):
            dev.save_dcd()
            notify(state, f"calibration saved at accuracy {state['fused_acc']}/3")
            print(f"calibration saved (accuracy was {state['fused_acc']}/3, "
                  f"heading estimate {state['fused_est']:.0f} deg)")
    cv2.destroyAllWindows()


def run_terminal(dev, args) -> None:
    state = new_state()
    last = 0.0
    print("yaw/pitch/roll in degrees. Ctrl-C to stop.\n")
    for _rep in pump(dev, state, (FUSED, GAME)):
        if state["fused_q"] is None or state["game_q"] is None:
            continue
        now = time.monotonic()
        if now - last < 0.1:
            continue
        last = now
        y, p, r = ypr_deg(state["game_q"] if args.game else state["fused_q"])
        delta = wrap180(ypr_deg(state["fused_q"])[0] - ypr_deg(state["game_q"])[0])
        flag = "  GIMBAL" if abs(p) > GIMBAL_WARN_DEG else ""
        print(f"  yaw {y:+7.1f}  pitch {p:+6.1f}  roll {r:+7.1f}   "
              f"acc {state['fused_acc']}/3  est {state['fused_est']:5.1f}  "
              f"compass adds {delta:+6.1f}{flag}   ", end="\r", flush=True)


# ── calibration ──────────────────────────────────────────────────────────────

CAL_STEPS = (
    (12, "Hold it still, flat on the table — this is the part that does nothing."),
    (12, "Now turn it slowly about the vertical, like a dial. Full circle."),
    (12, "Tip it nose up and nose down, all the way over if you can."),
    (12, "Roll it left and right, then trace a lazy figure-of-eight in the air."),
)


def run_calibrate(dev, args) -> None:
    """Walk through the movements and watch what each one is worth.

    The point is not the ritual — it is seeing which movements move the number.
    Holding still does nothing at all, and the readout says so while you wait.
    """
    dev.enable_magnetometer(hz=10)
    state = new_state()
    print("Calibration. Four steps, twelve seconds each — watch the accuracy.\n")
    history = []
    step_started = time.monotonic()
    last_print = 0.0
    step = 0
    print(f">>> {CAL_STEPS[0][1]}")
    for _rep in pump(dev, state, (FUSED, GAME, SensorId.MAGNETOMETER)):
        if state["fused_q"] is None:
            continue
        now = time.monotonic()
        elapsed = now - step_started
        # Ten lines a second, not a hundred: the reports arrive far faster than
        # anyone can read, and redirected to a file the difference is megabytes.
        if now - last_print > 0.1:
            last_print = now
            print(f"    acc {state['fused_acc']}/3   heading estimate "
                  f"{state['fused_est']:5.1f} deg   magnetometer "
                  f"{state['mag_acc']}/3   {CAL_STEPS[step][0] - elapsed:4.0f}s left   ",
                  end="\r", flush=True)
        if elapsed >= CAL_STEPS[step][0]:
            history.append((CAL_STEPS[step][1], state["fused_acc"], state["fused_est"]))
            print(f"    ended at acc {state['fused_acc']}/3, "
                  f"heading estimate {state['fused_est']:.1f} deg          ")
            step += 1
            if step >= len(CAL_STEPS):
                break
            step_started = now
            print(f"\n>>> {CAL_STEPS[step][1]}")

    print("\nWhat each step was worth:")
    for text, acc, est in history:
        print(f"  acc {acc}/3   est {est:5.1f} deg   {text}")

    if state["fused_acc"] >= 2:
        print("\nSaving that into the chip so the next power-up starts better.")
        dev.save_dcd()
        print("saved — run the example project again and watch the opening heading estimate")
    else:
        print("\nAccuracy never reached 2, so there is nothing worth saving. "
              "Try again away from the desk: monitors, speakers and laptop "
              "hinges all have magnets in them.")


# ── drift ────────────────────────────────────────────────────────────────────

def run_drift(dev, args) -> None:
    """Leave the board alone and see how far each yaw wanders.

    The game vector has nothing to hold its heading down: gyroscope only, and a
    gyroscope integrated over time drifts. The fused vector has the magnetometer
    pulling it back to north. This measures both, on your bench, in degrees.
    """
    state = new_state()
    print(f"Put the board down and do not touch it for {args.drift:.0f} s.\n")
    start = None
    start_tilt = None
    t0 = time.monotonic()
    worst = {"fused": 0.0, "game": 0.0}
    for _rep in pump(dev, state, (FUSED, GAME)):
        if state["fused_q"] is None or state["game_q"] is None:
            continue
        now = time.monotonic() - t0
        yaws = {"fused": ypr_deg(state["fused_q"])[0],
                "game": ypr_deg(state["game_q"])[0]}
        _, pitch, roll = ypr_deg(state["game_q"])
        if start is None:
            if now < 2.0:      # two seconds to let go of the board
                continue
            start = yaws
            start_tilt = (pitch, roll)
            continue

        # Drift is what a still board does on its own. Nudge it and the number
        # stops meaning that — and a wrong number that looks right is worse
        # than no number, so the run says so and stops instead.
        if max(abs(pitch - start_tilt[0]), abs(roll - start_tilt[1])) > MOVED_DEG:
            print(f"\n\n  the board moved at {now:.0f}s — drift is only "
                  f"meaningful while it lies still. Run it again.")
            return
        for k in worst:
            worst[k] = max(worst[k], abs(wrap180(yaws[k] - start[k])))
        print(f"    {now:5.0f}s   fused yaw {wrap180(yaws['fused'] - start['fused']):+7.2f}"
              f"   game yaw {wrap180(yaws['game'] - start['game']):+6.2f}   "
              f"acc {state['fused_acc']}/3  est {state['fused_est']:5.1f}   "
              f"(worst {worst['fused']:.2f} / {worst['game']:.2f})   ",
              end="\r", flush=True)
        if now > args.drift:
            break
    print(f"\n\n  over {args.drift:.0f} s, with the board untouched:")
    print(f"    rotation vector (with compass)  wandered up to {worst['fused']:.2f} deg")
    print(f"    game vector (gyro only)         wandered up to {worst['game']:.2f} deg")
    print(f"    the fused vector ended at accuracy {state['fused_acc']}/3, "
          f"heading estimate {state['fused_est']:.1f} deg")
    if worst["fused"] > worst["game"] + 1.0:
        # Worth saying out loud, because it is the opposite of what people
        # expect from a compass: an uncalibrated magnetometer does not hold a
        # heading down, it drags it around.
        print("\n    the compass moved the heading more than the gyroscope did — "
              "that is\n    an uncalibrated magnetometer correcting towards a "
              "field that is mostly\n    the desk. --calibrate first, or use "
              "--game and do not ask for north.")


def quat_angle(a, b) -> float:
    """The angle of the single rotation that takes pose `a` to pose `b`, in degrees.

    Works for any pair of poses, including the vertical ones where roll, pitch
    and yaw fall apart — which is exactly why the bench checks use it.
    """
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b))))
    return math.degrees(2 * math.acos(dot))


def board_up(q) -> np.ndarray:
    """Where the world's up points, written in the board's own axes.

    The rotation matrix turns board coordinates into world ones; its transpose
    does the reverse, and the third column of that is world-up seen from the
    board. Comparing this between two poses gives the change in tilt without
    ever touching an Euler angle — so it keeps working where they break.
    """
    return quat_matrix(q).T @ np.array([0.0, 0.0, 1.0])


def mean_quat(qs):
    """The average of near-identical quaternions, renormalised.

    Quaternions do not average like numbers in general, but over a still second
    these are all the same rotation to within noise, and the mean of them is too.
    """
    m = np.mean(np.array(qs), axis=0)
    return tuple(m / np.linalg.norm(m))


# A pose counts as held when nothing has moved more than this for HOLD_S.
# Measured on the bench: a board lying on a table wobbles 0.2-0.35°, one propped
# against something upright wobbles about 1.0° — a hand-held check has to accept
# the second, or it waits for a stillness that never comes and the person gives
# up and puts the board back down.
STILL_DEG = 1.4
HOLD_S = 1.5
MOVE_DEG = 4.0      # and as a new pose once it has moved at least this far


def wait_for_still(stream, state: dict, label: str, reference=None) -> dict:
    """Watch until the board is put down and left alone, then average that pose.

    No Enter to press and no countdown to lose a race with: the board itself
    says when it is ready by holding still, which is the one signal that cannot
    be out of step with what the person is doing.
    """
    buf: list = []
    last_print = 0.0
    while True:
        next(stream)
        if state["fused_q"] is None or state["game_q"] is None:
            continue
        now = time.monotonic()
        buf.append((now, state["game_q"], state["fused_q"]))
        buf[:] = [row for row in buf if now - row[0] <= HOLD_S]
        centre = mean_quat([row[1] for row in buf])
        wobble = max(quat_angle(row[1], centre) for row in buf)
        held = len(buf) > 10 and now - buf[0][0] >= HOLD_S * 0.95 and wobble < STILL_DEG
        if now - last_print > 0.4:
            last_print = now
            # Both numbers, always: how far it has come from the first pose, and
            # how close to still it is against the threshold it has to beat.
            turned = ("" if reference is None else
                      f"   turned {quat_angle(state['game_q'], reference):6.1f} deg")
            print(f"    {label}: {'holding still' if held else 'waiting for it to settle'}"
                  f"{turned}   wobble {wobble:5.2f} deg (needs < {STILL_DEG})   ",
                  end="\r", flush=True)
        if held:
            print(f"    {label}: held.                                        ")
            return {"game": centre, "fused": mean_quat([row[2] for row in buf])}


def wait_for_move(stream, state: dict, reference) -> None:
    """Watch until the board is picked up and moved somewhere new."""
    last_print = 0.0
    while True:
        next(stream)
        moved = quat_angle(state["game_q"], reference)
        now = time.monotonic()
        if now - last_print > 0.4:
            last_print = now
            print(f"    now move it to the second pose   moved {moved:6.1f} deg   ",
                  end="\r", flush=True)
        if moved > MOVE_DEG:
            print("    moving…                                              ")
            return


def run_two_poses(dev, args) -> None:
    """Two poses, and the honest difference between them.

    One command for both bench checks. Turn the board against a right angle and
    the yaw change should be 90°; prop it on a wedge of known geometry and the
    tilt change should be the wedge's own angle — the same arcsine of rise over
    length that example project 6 was verified with.
    """
    state = new_state()
    print("Set up the first pose and let go. Nothing to press — the example project takes "
          "the reading\nwhen the board stops moving, and again after you have "
          "moved it.\n")
    # One subscription to the report stream, read from start to finish. A fresh
    # dev.reports() per wait would register a new queue every time round the
    # loop, and every one of them would go on filling up behind us.
    stream = pump(dev, state, (FUSED, GAME))
    a = wait_for_still(stream, state, "first pose")
    wait_for_move(stream, state, a["game"])
    b = wait_for_still(stream, state, "second pose", reference=a["game"])

    print()
    if quat_angle(a["game"], b["game"]) < 5.0:
        print("  The two poses are almost the same one. If the board went back "
              "to where it\n  started before it settled, that is what got "
              "measured — run it again.\n")
    for name in ("game", "fused"):
        d_yaw = wrap180(ypr_deg(b[name])[0] - ypr_deg(a[name])[0])
        cos = float(np.dot(board_up(a[name]), board_up(b[name])))
        d_tilt = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        label = "game vector (gyro only)" if name == "game" else "with compass          "
        print(f"  {label}   total turn {quat_angle(a[name], b[name]):7.2f} deg"
              f"   of which yaw {d_yaw:+8.2f}   tilt {d_tilt:7.2f}")

    if args.truth is not None:
        # Compared against the total, never against yaw or tilt separately.
        # Those two are a decomposition, and a decomposition can put the same
        # real rotation in different places — stand the board up and the yaw
        # column reads 171° for a quarter turn. The total is one number about
        # one rotation and it cannot be talked out of it.
        for name in ("game", "fused"):
            err = quat_angle(a[name], b[name]) - args.truth
            label = "game vector" if name == "game" else "with compass"
            print(f"\n  {label}: turned {quat_angle(a[name], b[name]):.2f} deg "
                  f"against {args.truth:.3f} by tape — off by {err * 60:+.1f} "
                  f"arcmin ({err:+.2f} deg)")


def has_display() -> bool:
    import os
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Example project 7: orientation from the BNO086's quaternion")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--terminal", action="store_true",
                   help="text readout instead of the window (for ssh)")
    p.add_argument("--game", action="store_true",
                   help="use the game rotation vector — no compass, no waiting")
    p.add_argument("--calibrate", action="store_true",
                   help="walk through the movements and watch the accuracy climb")
    p.add_argument("--drift", type=float, metavar="SECONDS",
                   help="leave the board still and measure how far yaw wanders")
    p.add_argument("--two-poses", action="store_true",
                   help="hold two poses and print the turn between them")
    p.add_argument("--truth", type=float, metavar="DEG",
                   help="the angle that turn really is, for --two-poses")
    args = p.parse_args(argv)

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
        # Both vectors at once. They cost nothing to run together and the
        # difference between them is half of what this example project has to show.
        dev.enable_rotation_vector(hz=REPORT_HZ)
        dev.enable_game_rotation_vector(hz=REPORT_HZ)

        if args.two_poses:
            run_two_poses(dev, args)
        elif args.calibrate:
            run_calibrate(dev, args)
        elif args.drift:
            run_drift(dev, args)
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
        for sensor in (FUSED, GAME, SensorId.MAGNETOMETER):
            dev.disable(sensor)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
