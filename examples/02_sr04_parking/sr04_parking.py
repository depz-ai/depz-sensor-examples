#!/usr/bin/env python3
"""Example project 2 — Parking sensor (HC-SR04 ultrasonic).

The opposite problem to example project 1. There we chased accuracy and paid
for it with time: the more readings you average, the honester the millimetres.
A parking sensor does not need millimetres — it needs to warn you in time. So
there is almost no averaging here, and the whole project is about where the
line between "accurate" and "in time" runs.

What it does: shows an approach bar and beeps faster the closer the object
gets. Inside the red threshold the beeping turns into a solid tone.

Run:
    python examples/02_sr04_parking/sr04_parking.py                 zones at 0.4 / 1.5 m
    python examples/02_sr04_parking/sr04_parking.py --plot          a parking-display window
    python examples/02_sr04_parking/sr04_parking.py --near 0.3      your own red zone, metres
    python examples/02_sr04_parking/sr04_parking.py --mute          no sound
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import statistics
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

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

# How often to measure. The board throttles itself to the echo flight time
# anyway (a distant target answers slower than a near one), so we ask for more
# than we need and watch what actually arrives.
SAMPLE_PERIOD_US = 20_000

# Beep rate at the zone edges: rare at the far one, frequent at the near one.
BEEP_SLOW_S = 0.90
BEEP_FAST_S = 0.10
# Inside the red threshold the beeps merge into a solid tone.
BEEP_SOLID_S = 0.06

BAR_WIDTH = 34

# A jump larger than this between two readings almost certainly means the sensor
# switched to a different object: nothing moves 0.3 m in 20 ms (that is 15 m/s).
JUMP_M = 0.30
JUMP_SHOW_S = 1.2

# Sound. A terminal bell will not do: GNOME and PyCharm mute it by default and
# the parking sensor comes out silent. So we synthesise the wave ourselves and
# hand it to whatever the system can play: PipeWire, PulseAudio or ALSA on
# Linux, winsound on Windows, afplay on macOS — nothing to install anywhere.
AUDIO_RATE = 22050
BEEP_HZ = 2200
BEEP_MS = 45
FADE_MS = 4  # soft edges: without them each beep starts and ends with a click

GREEN, YELLOW, RED, GREY, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"

# The --plot window: a parking display. Same canvas as the ruler's plot window;
# the last ten seconds of history are kept for the strip at the bottom.
PANEL_W, PANEL_H = 1100, 560
PANEL_PAD_L, PANEL_PAD_R = 78, 40
HISTORY_S = 10.0
TRAIL_S = 1.0  # how long the car marker's trail stays on the zone bar

# BGR, because OpenCV works in that order.
COL_BG = (250, 249, 246)
COL_GRID = (226, 224, 220)
COL_TEXT = (60, 55, 50)
COL_DIM = (150, 145, 140)
COL_ZONE = {  # the terminal colours, as paint
    "free": (90, 170, 70),
    "close": (30, 175, 230),
    "STOP": (60, 60, 220),
    "clear": (150, 145, 140),
}


class Beeper:
    """Beeps through whatever the system can play, started once for the whole run.

    Launching a player per beep is not an option: the red zone fires sixteen a
    second while starting a process takes tens of milliseconds. So we keep ONE
    stream open and pour into it — a tone while beeping, silence while not:
    `pw-play` (PipeWire) or `paplay` (PulseAudio) on a desktop Linux, `aplay`
    (ALSA) on any other Linux. Windows has `winsound` built in. macOS has no
    stdin player, so there two short WAV files are rendered up front and
    `afplay` plays them — a few milliseconds late, which the ear forgives.
    """

    STREAM_PLAYERS = {
        "pw-play": ["--raw", "--format=s16le", f"--rate={AUDIO_RATE}", "--channels=1"],
        "paplay": ["--raw", "--format=s16le", f"--rate={AUDIO_RATE}", "--channels=1"],
        "aplay": ["-q", "-t", "raw", "-f", "S16_LE", "-r", str(AUDIO_RATE), "-c", "1"],
    }

    def __init__(self, muted: bool = False):
        self.backend = None
        self.proc = None
        self.phase = 0        # position inside the wave, so the tone never tears
        self.left = 0         # samples of tone still to play
        self.continuous = False
        self._busy = False    # winsound/afplay: a beep is playing right now
        self._wavs: dict[str, str] = {}
        if muted:
            pass
        elif os.name == "nt":
            self.backend = "winsound"
        elif sys.platform == "darwin" and shutil.which("afplay"):
            self.backend = "afplay"
            self._render_wavs()
        else:
            for name, argv in self.STREAM_PLAYERS.items():
                player = shutil.which(name)
                if not player:
                    continue
                proc = subprocess.Popen(
                    [player, *argv], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # Being on PATH is not the same as understanding these flags:
                # older pw-play builds have no --raw and quit at once. Such a
                # player would swallow every beep in silence, so give it a
                # moment and move on to the next one if it is already gone.
                time.sleep(0.05)
                if proc.poll() is not None:
                    continue
                self.backend = "stream"
                self.proc = proc
                break
        self.enabled = self.backend is not None

    def pulse(self) -> None:
        self.left = int(AUDIO_RATE * BEEP_MS / 1000)
        if self.backend == "winsound":
            self._spawn(lambda: __import__("winsound").Beep(BEEP_HZ, BEEP_MS))
        elif self.backend == "afplay":
            self._spawn(lambda: subprocess.call(["afplay", self._wavs["beep"]]))

    def feed(self, seconds: float) -> None:
        """Pour in exactly as much sound as real time has passed."""
        if self.backend in ("winsound", "afplay"):
            # These players block, so the solid tone is a chain of short notes
            # from a helper thread; pulses were already started by pulse().
            if self.continuous and not self._busy:
                if self.backend == "winsound":
                    self._spawn(lambda: __import__("winsound").Beep(BEEP_HZ, 120))
                else:
                    self._spawn(lambda: subprocess.call(["afplay", self._wavs["solid"]]))
            return
        if not self.enabled or self.proc is None or self.proc.stdin is None:
            return
        n = int(AUDIO_RATE * min(seconds, 0.2))
        fade = AUDIO_RATE * FADE_MS / 1000
        buf = bytearray()
        for _ in range(n):
            if self.continuous or self.left > 0:
                # Only the short beep is enveloped; a solid tone must not fade.
                env = 1.0 if self.continuous else min(1.0, self.left / fade)
                value = int(9000 * env * math.sin(2 * math.pi * BEEP_HZ
                                                  * self.phase / AUDIO_RATE))
                self.phase += 1
                if self.left > 0:
                    self.left -= 1
            else:
                value = 0
                self.phase = 0
            buf += struct.pack("<h", value)
        try:
            self.proc.stdin.write(bytes(buf))
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.enabled = False

    def _spawn(self, play) -> None:
        """Run a blocking player call in a helper thread, one at a time."""
        def run() -> None:
            self._busy = True
            try:
                play()
            finally:
                self._busy = False

        threading.Thread(target=run, daemon=True).start()

    def _render_wavs(self) -> None:
        """macOS: the beep and a 200 ms slice of the solid tone, as WAV files."""
        import tempfile
        import wave
        folder = tempfile.mkdtemp(prefix="depz-parking-")
        for name, ms, enveloped in (("beep", BEEP_MS, True), ("solid", 200, False)):
            n = int(AUDIO_RATE * ms / 1000)
            fade = AUDIO_RATE * FADE_MS / 1000
            frames = bytearray()
            for i in range(n):
                env = min(1.0, i / fade, (n - i) / fade) if enveloped else 1.0
                frames += struct.pack("<h", int(9000 * env * math.sin(
                    2 * math.pi * BEEP_HZ * i / AUDIO_RATE)))
            path = os.path.join(folder, f"{name}.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(AUDIO_RATE)
                w.writeframes(bytes(frames))
            self._wavs[name] = path

    def close(self) -> None:
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            self.proc.terminate()


class Cadence:
    """Turns a beep period into beeps: pulses in the yellow zone, a solid tone
    in the red one, silence beyond. `update` returns True on the frame a pulse
    fires, so a display can flash in time with the sound."""

    def __init__(self, beeper: Beeper):
        self.beeper = beeper
        self.next_beep = 0.0
        self.beeps = 0
        self.last_fed = time.monotonic()

    def update(self, period: float | None, now: float) -> bool:
        fired = False
        self.beeper.continuous = period == BEEP_SOLID_S
        if period is None:
            self.next_beep = 0.0
        elif not self.beeper.continuous and now >= self.next_beep:
            self.beeper.pulse()
            self.beeps += 1
            self.next_beep = now + period
            fired = True
        self.beeper.feed(now - self.last_fed)
        self.last_fed = now
        return fired


def speed_of_sound(air_temp_c: float) -> float:
    """Speed of sound in m/s: 331 at zero, gaining 0.6 per degree."""
    return 331.3 + 0.606 * air_temp_c


def echo_to_m(echo_us: int, air_temp_c: float) -> float:
    """Distance to the object: echo time halved, the sound made a round trip."""
    return echo_us * 1e-6 * speed_of_sound(air_temp_c) / 2.0


def zone_of(metres: float | None, near: float, far: float) -> tuple[str, str]:
    """Zone name and colour. None means no echo — the way ahead is clear."""
    if metres is None:
        return "clear", GREY
    if metres <= near:
        return "STOP", RED
    if metres <= far:
        return "close", YELLOW
    return "free", GREEN


def beep_period(metres: float | None, near: float, far: float) -> float | None:
    """Seconds until the next beep. None means stay silent.

    Between the two thresholds the interval shrinks linearly: the ear is poor at
    judging absolute pitch but excellent at hearing a rhythm speed up.
    """
    if metres is None or metres > far:
        return None
    if metres <= near:
        return BEEP_SOLID_S
    share = (metres - near) / (far - near)  # 0 at the red edge, 1 at the yellow
    return BEEP_FAST_S + (BEEP_SLOW_S - BEEP_FAST_S) * share


def bar(metres: float | None, full_scale: float, color: str) -> str:
    """Approach bar: the closer the object, the longer the bar."""
    if metres is None:
        return GREY + "·" * BAR_WIDTH + RESET
    filled = round(max(0.0, min(1.0, 1.0 - metres / full_scale)) * BAR_WIDTH)
    return color + "█" * filled + RESET + GREY + "░" * (BAR_WIDTH - filled) + RESET


class Smoother:
    """The last few readings, and a parking sensor's two memory rules.

    Median of a short window: it drops a single stray and costs only a couple
    of readings. A mean would not do — one bounce off something nearby would
    drag it down and set off a false alarm.
    """

    def __init__(self, size: int):
        self.size = max(1, size)
        self.window: deque[float] = deque(maxlen=self.size)
        self.prev: float | None = None
        self.last_seen: float | None = None
        self.jumped_at = 0.0
        self.jumps = 0
        self.just_jumped = False

    def add(self, metres: float | None) -> float | None:
        """Take one reading in; return the smoothed distance, or None."""
        self.just_jumped = False
        if metres is None:
            # No echo — either nothing ahead or something soft. Old values must
            # not linger: a parking sensor has to forget what drove off.
            self.window.clear()
            return None
        self.window.append(metres)
        value = statistics.median(self.window)
        # The sensor does not track an object — it answers about the nearest
        # thing in the cone. Move the target past the cone edge and a desk or a
        # door frame becomes the nearest: a sudden jump.
        if self.prev is not None and abs(value - self.prev) > JUMP_M:
            self.jumped_at = time.monotonic()
            self.jumps += 1
            self.just_jumped = True
        self.prev = value
        self.last_seen = value
        return value

    @property
    def jump_flash(self) -> bool:
        return time.monotonic() - self.jumped_at < JUMP_SHOW_S

    def lag_ms(self, rate: float) -> float:
        # The median crosses the threshold once more than half the window is
        # new, i.e. it lags by (N-1)/2 readings. For a window of three that
        # is one frame, which is what the bench measured.
        return (1000 / rate if rate else 0.0) * (self.size - 1) / 2


# ── One frame's worth of facts, shared by the terminal and the window ────────

@dataclass
class PanelState:
    metres: float | None
    zone: str
    period: float | None
    fps: float
    lag_ms: float
    beeps: int
    beep_flash: bool          # a pulse fired on this very frame
    jump_flash: bool          # "target changed" is still being shown
    jumps: int
    last_seen: float | None
    lost: int
    total: int
    source: str
    history: list            # (t, metres or None, jumped) for the last HISTORY_S


def states(dev, args, beeper: Beeper):
    """Read the board and yield one PanelState per measurement — the whole
    per-reading logic in one place, so the terminal and the window agree."""
    smoother = Smoother(args.smooth)
    cadence = Cadence(beeper)
    history: deque = deque()
    frames = lost = 0
    started = time.monotonic()
    for m in dev.stream():
        frames += 1
        now = time.monotonic()
        if not m.valid:
            lost += 1
        value = smoother.add(echo_to_m(m.echo_time_us, args.temp) if m.valid else None)
        name, _ = zone_of(value, args.near, args.far)
        period = beep_period(value, args.near, args.far)
        fired = cadence.update(period, now)
        rate = frames / (now - started) if now > started else 0.0
        history.append((now, value, smoother.just_jumped))
        while history and now - history[0][0] > HISTORY_S:
            history.popleft()
        yield PanelState(value, name, period, rate, smoother.lag_ms(rate), cadence.beeps,
                         fired, smoother.jump_flash, smoother.jumps, smoother.last_seen,
                         lost, frames, f"port {dev.port}", list(history))


# ── The parking display (--plot) ─────────────────────────────────────────────

def import_cv2():
    """OpenCV, imported lazily — the terminal mode must run without it.

    Also silences the one Qt warning: OpenCV ships a Qt build with no fonts of
    its own and Qt complains on every start, yet nothing here uses Qt fonts —
    every label is drawn by cv2.putText. setdefault, so an explicit
    QT_LOGGING_RULES from the environment still wins.
    """
    os.environ.setdefault("QT_LOGGING_RULES", "default.warning=false")
    import cv2
    return cv2


def draw_tiles(frame, tiles) -> None:
    """Bottom row of stat boxes, in the style of the DEPZ viewer."""
    cv2 = import_cv2()
    n = len(tiles)
    gap = 12
    top = PANEL_H - 96
    width = (PANEL_W - PANEL_PAD_L - PANEL_PAD_R - gap * (n - 1)) // n
    value_scale = 0.78 if n <= 4 else (0.66 if n == 5 else 0.56)
    for i, (title, value, note, color) in enumerate(tiles):
        x = PANEL_PAD_L + i * (width + gap)
        cv2.rectangle(frame, (x, top), (x + width, top + 84), COL_GRID, 1)
        cv2.putText(frame, title, (x + 12, top + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.40, COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(frame, value, (x + 12, top + 54), cv2.FONT_HERSHEY_SIMPLEX,
                    value_scale, color, 2, cv2.LINE_AA)
        if note:
            cv2.putText(frame, note, (x + 12, top + 74), cv2.FONT_HERSHEY_SIMPLEX,
                        0.36 if n > 4 else 0.40, COL_DIM, 1, cv2.LINE_AA)


def compose_panel(state: PanelState, args):
    """Render one frame of the parking display.

    Kept separate from the loop so a screenshot for the README can be produced
    headless — same pixels the window shows, no window popping up.
    """
    cv2 = import_cv2()
    import numpy as np

    frame = np.full((PANEL_H, PANEL_W, 3), COL_BG, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    x0, x1 = PANEL_PAD_L, PANEL_W - PANEL_PAD_R
    paint = COL_ZONE[state.zone]
    full = args.far * 1.25  # the zone bar's scale: a bit of green beyond `far`
    now = state.history[-1][0] if state.history else time.monotonic()

    cv2.putText(frame, "DEPZ  Example project 2  Parking sensor", (x0, 36),
                font, 0.7, COL_TEXT, 2, cv2.LINE_AA)
    cv2.putText(frame, f"{state.source}   zones: STOP <= {args.near:.2f} m, "
                f"close <= {args.far:.2f} m", (x0, 62), font, 0.5, COL_DIM, 1, cv2.LINE_AA)

    # The distance, big — what a driver would glance at.
    if state.metres is not None:
        cv2.putText(frame, f"{state.metres:.2f} m", (x0, 165), font, 3.6, paint, 8, cv2.LINE_AA)
        cv2.putText(frame, state.zone, (x0 + 470, 165), font, 2.0, paint, 5, cv2.LINE_AA)
    else:
        cv2.putText(frame, "--.-- m", (x0, 165), font, 3.6, COL_DIM, 8, cv2.LINE_AA)
        cv2.putText(frame, "clear", (x0 + 470, 165), font, 2.0, COL_DIM, 5, cv2.LINE_AA)
        if state.last_seen is not None:
            cv2.putText(frame, f"last seen {state.last_seen:.2f} m", (x0 + 470, 195),
                        font, 0.55, COL_DIM, 1, cv2.LINE_AA)

    # The zone bar: the bumper is at the left edge, the wall drives in from the right.
    y0, y1 = 215, 255

    def px(metres: float) -> int:
        return int(x0 + max(0.0, min(1.0, metres / full)) * (x1 - x0))

    for lo, hi, name in ((0.0, args.near, "STOP"), (args.near, args.far, "close"),
                         (args.far, full, "free")):
        cv2.rectangle(frame, (px(lo), y0), (px(hi), y1), COL_ZONE[name], -1)
    for metres, label in ((0.0, "0"), (args.near, f"{args.near:.2f} m"), (args.far, f"{args.far:.2f} m")):
        x = px(metres)
        cv2.line(frame, (x, y1), (x, y1 + 8), COL_TEXT, 1, cv2.LINE_AA)
        cv2.putText(frame, label, (x - 8, y1 + 24), font, 0.45, COL_TEXT, 1, cv2.LINE_AA)
    cv2.putText(frame, "bumper", (x0, y0 - 10), font, 0.42, COL_DIM, 1, cv2.LINE_AA)

    # The car marker's trail: where it was during the last second — the
    # spacing of the ghosts is the approach speed.
    for t, metres, _ in state.history:
        age = now - t
        if metres is None or age > TRAIL_S:
            continue
        x = px(metres)
        cv2.line(frame, (x, y0 + 4), (x, y1 - 4), (255, 255, 255), 1, cv2.LINE_AA)
    # The car itself.
    if state.metres is not None:
        x = px(state.metres)
        cv2.rectangle(frame, (x - 24, y0 - 32), (x + 24, y0 - 6), COL_TEXT, -1)
        cv2.rectangle(frame, (x - 14, y0 - 44), (x + 14, y0 - 32), COL_TEXT, -1)
        for wx in (x - 15, x + 15):
            cv2.circle(frame, (wx, y0 - 5), 5, COL_TEXT, -1, cv2.LINE_AA)
        cv2.line(frame, (x, y0 - 2), (x, y1 + 2), (255, 255, 255), 2, cv2.LINE_AA)

    # The beep cadence: one dot per beep in the next second; the row flashes on
    # the frame a pulse fires; a bar when solid.
    by = 305
    cv2.putText(frame, "beep", (x0, by + 6), font, 0.55, COL_TEXT, 1, cv2.LINE_AA)
    if state.period == BEEP_SOLID_S:
        cv2.rectangle(frame, (x0 + 70, by - 9), (x0 + 430, by + 9), COL_ZONE["STOP"], -1)
        cv2.putText(frame, "SOLID", (x0 + 450, by + 7), font, 0.65, COL_ZONE["STOP"], 2, cv2.LINE_AA)
    elif state.period:
        per_second = 1.0 / state.period
        for i in range(int(round(per_second))):
            c = (x0 + 80 + i * 36, by)
            if state.beep_flash:
                cv2.circle(frame, c, 12, paint, 2, cv2.LINE_AA)
            cv2.circle(frame, c, 8, paint, -1, cv2.LINE_AA)
        cv2.putText(frame, f"{per_second:.1f} per second", (x0 + 450, by + 7),
                    font, 0.65, COL_TEXT, 2, cv2.LINE_AA)
    else:
        cv2.putText(frame, "silent", (x0 + 80, by + 7), font, 0.65, COL_DIM, 2, cv2.LINE_AA)

    # The last ten seconds: distance over time, thresholds as guides, red dots
    # where the jump detector fired. The approach curve tells the speed.
    hy0, hy1 = 340, 440
    cv2.rectangle(frame, (x0, hy0), (x1, hy1), COL_GRID, 1)

    def hx(t: float) -> int:
        return int(x1 - (now - t) / HISTORY_S * (x1 - x0))

    def hy(metres: float) -> int:
        return int(hy1 - max(0.0, min(1.0, metres / full)) * (hy1 - hy0))

    for metres, name in ((args.near, "STOP"), (args.far, "close")):
        cv2.line(frame, (x0, hy(metres)), (x1, hy(metres)), COL_ZONE[name], 1, cv2.LINE_AA)
    pts = [(hx(t), hy(m)) for t, m, _ in state.history if m is not None]
    for a, b in zip(pts, pts[1:]):
        if abs(a[0] - b[0]) < 40:  # do not bridge a lost-echo gap
            cv2.line(frame, a, b, COL_TEXT, 2, cv2.LINE_AA)
    for t, m, jumped in state.history:
        if jumped and m is not None:
            cv2.circle(frame, (hx(t), hy(m)), 4, COL_ZONE["STOP"], -1, cv2.LINE_AA)
    cv2.putText(frame, "-10 s", (x0 + 4, hy1 - 6), font, 0.4, COL_DIM, 1, cv2.LINE_AA)
    cv2.putText(frame, "now", (x1 - 34, hy1 - 6), font, 0.4, COL_DIM, 1, cv2.LINE_AA)

    # The tiles: the price of answering now, in numbers.
    tempo = ("solid" if state.period == BEEP_SOLID_S
             else f"{1 / state.period:.1f}/s" if state.period else "silent")
    draw_tiles(frame, [
        ("DISTANCE", f"{state.metres:.2f} m" if state.metres is not None else "--", "median of last readings", COL_TEXT),
        ("ZONE", state.zone, f"red to {args.near:.2f}, yellow to {args.far:.2f} m", paint),
        ("BEEP", tempo, "faster the closer", COL_TEXT),
        ("LAG", f"~{state.lag_ms:.0f} ms", f"smoothing over {args.smooth}", COL_TEXT),
        ("RATE", f"{state.fps:.1f} fps", f"echo lost {state.lost} of {state.total}", COL_TEXT),
        ("TARGET CHANGES", "changed!" if state.jump_flash else str(state.jumps),
         f"jumps over {JUMP_M:.1f} m", COL_ZONE["close"] if state.jump_flash else COL_TEXT),
    ])
    return frame


def render_terminal(state: PanelState, args, printed: int) -> int:
    """The terminal display. Returns how many lines it wrote."""
    color = {"free": GREEN, "close": YELLOW, "STOP": RED, "clear": GREY}[state.zone]
    shown = f"{state.metres:5.3f} m" if state.metres is not None else "  —  "
    tempo = "solid" if state.period == BEEP_SOLID_S else (
        f"{1 / state.period:4.1f} per second" if state.period else "silent")
    lines = [
        f"   {color}{shown}{RESET}   {color}{state.zone}{RESET}"
        + (f"   (last seen: {state.last_seen:.3f} m)"
           if state.metres is None and state.last_seen is not None else ""),
        f"   {bar(state.metres, args.far, color)}",
        f"   0{' ' * (BAR_WIDTH - 7)}{args.far:.2f} m   beep: {tempo}",
        f"   {state.fps:4.1f} fps, smoothing lag ~{state.lag_ms:.0f} ms, beeps {state.beeps}"
        + (f"   {YELLOW}⚡ target changed{RESET}" if state.jump_flash
           else f"   target changes: {state.jumps}"),
    ]
    # Move the cursor back up instead of clearing the screen: no flicker.
    if printed:
        sys.stdout.write(f"\033[{printed}A")
    for line in lines:
        sys.stdout.write("\033[2K" + line + "\n")
    sys.stdout.flush()
    return len(lines)


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


def run_plot(dev, args, beeper: Beeper) -> None:
    cv2 = import_cv2()
    title = "DEPZ Example project 2 - parking sensor"
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    try:
        for state in states(dev, args, beeper):
            cv2.imshow(title, compose_panel(state, args))
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
            if not window_open(cv2, title):
                break  # the window's X button quits too
    finally:
        cv2.destroyAllWindows()


def run_live(dev, args, beeper: Beeper) -> None:
    # The terminal view redraws its lines with ANSI cursor codes. Windows
    # Terminal understands them out of the box; the classic cmd.exe console
    # only after this no-op call, which switches it into VT mode.
    if os.name == "nt":
        os.system("")
    sound_note = ("   sound off" if args.mute
                  else ("" if beeper.enabled else "   no audio backend found: sound off"))
    print("DEPZ · Example project 2 · Parking sensor")
    print(f"port {dev.port}   zones: STOP ≤ {args.near:.3f} m, close ≤ {args.far:.3f} m" + sound_note)
    print()
    printed = 0
    for state in states(dev, args, beeper):
        printed = render_terminal(state, args, printed)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Example project 2: a parking sensor on HC-SR04")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--temp", type=float, default=20.0,
                   help="air temperature, °C (millimetres hardly matter for zones)")
    p.add_argument("--near", type=float, default=0.40, help="red zone, metres")
    p.add_argument("--far", type=float, default=1.50, help="yellow zone, metres")
    p.add_argument("--smooth", type=int, default=3,
                   help="how many readings to take the median of (1 = none)")
    p.add_argument("--mute", action="store_true", help="no sound")
    p.add_argument("--plot", action="store_true",
                   help="a parking-display window instead of terminal output")
    args = p.parse_args(argv)

    if args.near >= args.far:
        print("--near must be smaller than --far", file=sys.stderr)
        return 2

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
    beeper = Beeper(muted=args.mute)
    dev.start()
    try:
        if args.plot:
            run_plot(dev, args, beeper)
        else:
            run_live(dev, args, beeper)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        beeper.close()
        dev.stop()
        dev.set_sample_period_us(was_period)
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
