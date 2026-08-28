#!/usr/bin/env python3
"""Lab 2 — Parking sensor (HC-SR04 ultrasonic).

The opposite problem to lab 1. There we chased accuracy and paid for it with
time: the more readings you average, the honester the millimetres. A parking
sensor does not need millimetres — it needs to warn you in time. So there is
almost no averaging here, and the whole lab is about where the line between
"accurate" and "in time" runs.

What it does: shows an approach bar and beeps faster the closer the object gets.
Inside the red threshold the beeping turns into a solid tone.

Run:
    python labs/02_sr04_parking/lab2_sr04.py                 zones at 0.4 / 1.5 m
    python labs/02_sr04_parking/lab2_sr04.py --near 0.3      your own red zone, metres
    python labs/02_sr04_parking/lab2_sr04.py --mute          no sound
"""

from __future__ import annotations

import argparse
import math
import shutil
import statistics
import struct
import subprocess
import sys
import time
from collections import deque

try:
    from depz_sensor_sdk import DepzError, NoDepzDeviceError, open_device
except ImportError:  # the SDK lives in the project venv, not in system Python
    raise SystemExit(
        "depz_sensor_sdk is missing — you are probably running system Python.\n"
        "Use the project environment:\n"
        "    .venv/bin/python labs/02_sr04_parking/lab2_sr04.py\n"
        "or create it first:\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
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
# hand it to the system player — every Ubuntu has one, nothing to install.
AUDIO_RATE = 22050
BEEP_HZ = 2200
BEEP_MS = 45
FADE_MS = 4  # soft edges: without them each beep starts and ends with a click

GREEN, YELLOW, RED, GREY, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"


class Beeper:
    """Beeps through `paplay`, started once for the whole lab.

    Launching the player per beep is not an option: the red zone fires sixteen
    a second while starting a process takes tens of milliseconds. So we keep one
    open stream and pour into it — a tone while beeping, silence while not.
    """

    def __init__(self, muted: bool = False):
        self.player = shutil.which("paplay") or shutil.which("pw-play")
        self.enabled = not muted and self.player is not None
        self.proc = None
        self.phase = 0        # position inside the wave, so the tone never tears
        self.left = 0         # samples of tone still to play
        self.continuous = False
        if self.enabled:
            self.proc = subprocess.Popen(
                [self.player, "--raw", "--format=s16le",
                 f"--rate={AUDIO_RATE}", "--channels=1"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def pulse(self) -> None:
        self.left = int(AUDIO_RATE * BEEP_MS / 1000)

    def feed(self, seconds: float) -> None:
        """Pour in exactly as much sound as real time has passed."""
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

    def close(self) -> None:
        if self.proc is not None and self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            self.proc.terminate()


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Lab 2: a parking sensor on HC-SR04")
    p.add_argument("--port", help="board port, if several are plugged in")
    p.add_argument("--temp", type=float, default=20.0,
                   help="air temperature, °C (millimetres hardly matter for zones)")
    p.add_argument("--near", type=float, default=0.40, help="red zone, metres")
    p.add_argument("--far", type=float, default=1.50, help="yellow zone, metres")
    p.add_argument("--smooth", type=int, default=3,
                   help="how many readings to take the median of (1 = none)")
    p.add_argument("--mute", action="store_true", help="no sound")
    args = p.parse_args(argv)

    if args.near >= args.far:
        print("--near must be smaller than --far", file=sys.stderr)
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

    beeper = Beeper(muted=args.mute)
    sound_note = ("   sound off" if args.mute
                  else ("" if beeper.enabled else "   no sound: paplay not found"))

    print("DEPZ · Lab 2 · Parking sensor")
    print(f"port {dev.port}   zones: STOP ≤ {args.near:.3f} m, "
          f"close ≤ {args.far:.3f} m" + sound_note)
    print()
    body = 4
    print("\n" * body, end="")

    window: deque[float] = deque(maxlen=max(1, args.smooth))
    last_fed = time.monotonic()
    next_beep = 0.0
    beeps = 0
    frames = 0
    started = time.monotonic()
    last_seen: float | None = None
    prev: float | None = None
    jumped_at = 0.0
    jumps = 0

    dev.start()
    try:
        for m in dev.stream():
            frames += 1
            if m.valid:
                window.append(echo_to_m(m.echo_time_us, args.temp))
            else:
                # No echo — either nothing ahead or something soft. Old values
                # must not linger: a parking sensor has to forget what drove off.
                window.clear()

            # Median of a short window: it drops a single stray and costs only a
            # couple of readings. A mean would not do — one bounce off something
            # nearby would drag it down and set off a false alarm.
            value = statistics.median(window) if window else None
            if value is not None:
                # The sensor does not track an object — it answers about the
                # nearest thing in the cone. Move the target past the cone edge
                # and a desk or a door frame becomes the nearest: a sudden jump.
                if prev is not None and abs(value - prev) > JUMP_M:
                    jumped_at = time.monotonic()
                    jumps += 1
                prev = value
                last_seen = value

            name, color = zone_of(value, args.near, args.far)
            period = beep_period(value, args.near, args.far)

            now = time.monotonic()
            # Solid tone in the red zone, separate beeps in the yellow one.
            beeper.continuous = period == BEEP_SOLID_S
            if period is None:
                next_beep = 0.0
            elif not beeper.continuous and now >= next_beep:
                beeper.pulse()
                beeps += 1
                next_beep = now + period
            beeper.feed(now - last_fed)
            last_fed = now

            rate = frames / (now - started) if now > started else 0.0
            # The median crosses the threshold once more than half the window is
            # new, i.e. it lags by (N-1)/2 readings. For a window of three that
            # is one frame, which is what the bench measured.
            lag = (1000 / rate if rate else 0.0) * (args.smooth - 1) / 2

            shown = f"{value:5.3f} m" if value is not None else "  —  "
            tempo = "solid" if period == BEEP_SOLID_S else (
                f"{1 / period:4.1f} per second" if period else "silent")

            lines = [
                f"   {color}{shown}{RESET}   {color}{name}{RESET}"
                + (f"   (last seen: {last_seen:.3f} m)"
                   if value is None and last_seen is not None else ""),
                f"   {bar(value, args.far, color)}",
                f"   0{' ' * (BAR_WIDTH - 7)}{args.far:.2f} m   beep: {tempo}",
                f"   {rate:4.1f} fps, smoothing lag ~{lag:.0f} ms, beeps {beeps}"
                + (f"   {YELLOW}⚡ target changed{RESET}"
                   if now - jumped_at < JUMP_SHOW_S else f"   target changes: {jumps}"),
            ]
            sys.stdout.write(f"\033[{body}A")
            for line in lines:
                sys.stdout.write("\033[2K" + line + "\n")
            sys.stdout.flush()
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
