# Building a parking sensor. Can DEPZ HC-SR04 USB answer in time?

> Web version with interactive demos and highlighted code: https://depz.ai/developers/sensors/example-projects/sr04-parking

![The DEPZ HC-SR04 USB mounted on a car's rear bumper, a concrete wall a hand-width away, chalk marks on the floor](https://depz.ai/examples/sensors-02-bumper.jpg)

Hardware used: [Ultrasonic HC-SR04 USB](https://depz.ai/product/ultrasonic-sensor-hc-sr04-usb).

## Quick start

**Linux**

Plug the HC-SR04 USB into a USB port.

On Ubuntu, press `Ctrl` + `Alt` + `T` to open a terminal. If you have never used Python or a USB serial device on this machine, run these once:

```bash
sudo apt install python3 python3-venv git
sudo usermod -aG dialout $USER
```

Log out and back in or restart the PC, open a terminal again and:

```bash
git clone https://github.com/depz-ai/depz-sensor-examples.git
cd depz-sensor-examples
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python examples/02_sr04_parking/sr04_parking.py --plot
```

**macOS**

Plug the HC-SR04 USB into a USB port.

Install [Python for macOS](https://www.python.org/downloads/macos/) — the `python3` built into macOS is 3.9, too old for the SDK. Then open the Terminal app: press `⌘` + `Space`, type `Terminal`, press `Enter`. If you have never used developer tools on this Mac, run this once — it installs `git`:

```bash
xcode-select --install
```

Then, in that terminal:

```bash
git clone https://github.com/depz-ai/depz-sensor-examples.git
cd depz-sensor-examples
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python examples/02_sr04_parking/sr04_parking.py --plot
```

**Windows**

Plug the HC-SR04 USB into a USB port.

Install [Python](https://www.python.org/downloads/windows/) (keep "Install launcher" ticked) and [Git for Windows](https://git-scm.com/download/win). Then open the Command Prompt: `Win` + `R`, type `cmd`, `Enter`.

In that window:

```bat
git clone https://github.com/depz-ai/depz-sensor-examples.git
cd depz-sensor-examples
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python examples\02_sr04_parking\sr04_parking.py --plot
```

This creates a private Python environment inside the repository folder, installs the SDK and OpenCV into it and runs the project from there — nothing touches your system Python.

With no flags it works in the terminal: an approach bar, the zone, and a beep that quickens as the object closes in — solid inside 0.40 m, silent beyond 1.50 m. `Ctrl+C` quits; in the window, `q` or `Esc`.

## How the sensor measures

The HC-SR04 is the two-eyed module from every robotics kit: one cylinder is a tiny loudspeaker, the other a microphone. For each measurement it sends a short burst of ultrasound — eight clicks at 40 kHz, far above hearing — and listens for the echo. The DEPZ USB board triggers the burst, times how long the echo takes to come back, and hands you that time over USB in microseconds.

Turning the time into a distance is one multiplication: sound travels at about 343 m/s at room temperature, and the echo covered the way to the object **and back**, so distance = time × speed of sound ÷ 2. Reading it through the SDK takes a handful of lines — open the board, start it, and every measurement carries the echo time plus a flag saying whether an echo came back at all:

```python
from depz_sensor_sdk import open_device

SPEED_OF_SOUND = 343.0                     # m/s, air at 20 °C

board = open_device()                      # the DEPZ board on USB
board.start()
for measurement in board.stream():
    if not measurement.valid:              # no echo came back
        continue
    round_trip_s = measurement.echo_time_us / 1_000_000
    distance_m = round_trip_s * SPEED_OF_SOUND / 2   # there and back
    print(f"{distance_m:.3f} m")
```

For a parking sensor the interesting number is not the distance but **how often** one arrives: about 50 a second at 1.5 m, and it is the arrival rate, not the millimetres, that the rest of this project is built on. The speed of sound still depends on air temperature (331 m/s at 0 °C, about 0.6 m/s more per degree), so the project takes it as `--temp` — but ten degrees off moves a 0.400 m threshold to 0.392, and no ear notices that:

```python
def speed_of_sound(air_temp_c: float) -> float:
    """Speed of sound in m/s: 331 at zero, gaining 0.6 per degree."""
    return 331.3 + 0.606 * air_temp_c


def echo_to_m(echo_us: int, air_temp_c: float) -> float:
    """Distance to the object: echo time halved, the sound made a round trip."""
    return echo_us * 1e-6 * speed_of_sound(air_temp_c) / 2.0
```

## How it works

Three zones, like a real parking sensor. The thresholds move with `--near` and `--far`:

| Zone | Default | What it does |
|---|---|---|
| free | beyond 1.5 m | silent |
| close | 1.5 … 0.4 m | beeps, faster as it closes in |
| STOP | inside 0.4 m | solid tone |

![The parking display in the red zone: 0.31 m, STOP, the beep bar solid](https://depz.ai/examples/sensors-02-parking-stop.png)

*Inside 0.40 m: the display turns red and the beeps merge into a solid tone.*

Between the thresholds the beep rate changes smoothly, from once a second to ten times. That is deliberate: the ear judges absolute pitch poorly but hears a **change** of rhythm very well — and the change of rhythm is exactly the speed of approach.

```python
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
```

The sound: the project synthesises a 2200 Hz sine wave itself and pipes it into whatever the system can play — `pw-play` (PipeWire), `paplay` (PulseAudio) or `aplay` (ALSA) on Linux, the built-in `winsound` on Windows, `afplay` on macOS — so there is nothing to install anywhere. The player is started **once for the whole run**, not once per beep — the red zone fires sixteen beeps a second while starting a process takes tens of milliseconds:

```python
import math
import os
import shutil
import struct
import subprocess
import sys
import threading

AUDIO_RATE = 22050
BEEP_HZ = 2200
BEEP_MS = 45
FADE_MS = 4  # soft edges: without them each beep starts and ends with a click


class Beeper:
    """Beeps through whatever the system can play, started once for the whole run."""

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
                if player:
                    self.backend = "stream"
                    self.proc = subprocess.Popen(
                        [player, *argv], stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    break
        self.enabled = self.backend is not None

    def pulse(self) -> None:
        self.left = int(AUDIO_RATE * BEEP_MS / 1000)
        if self.backend == "winsound":
            self._spawn(lambda: __import__("winsound").Beep(BEEP_HZ, BEEP_MS))
        elif self.backend == "afplay":
            self._spawn(lambda: subprocess.call(["afplay", self._wavs["beep"]]))
```

The stream is fed for exactly as many milliseconds as real time has passed — a tone or silence — and every beep fades in and out over 4 ms, because a sine wave cut off abruptly is heard as a click:

```python
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
```

One small class turns the beep period into actual beeps — pulses in the yellow zone, a solid tone in the red one, silence beyond — and reports the frame a pulse fires on, so the display can flash in time with the sound:

```python
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
```

## Why a parking sensor answers now — and what it gives up

[The ruler project](https://depz.ai/developers/sensors/example-projects/sr04-ruler) chased accuracy and paid for it with time: averaging 100 readings takes five seconds, fine for a wall that is not going anywhere. A parking sensor cannot afford that — five seconds is a car a metre past the point where it should have beeped. What matters is not accuracy but **being in time**, and three design decisions follow from it: how little to smooth, what to do when the nearest thing changes, and how fast the sensor can physically go.

### 1. A median of three, not a mean of twenty

From the ruler we know the sensor sometimes answers about a stray object, and such a miss is always **nearer** than the target. The ruler fixes that with statistics — a thousand readings and the densest cluster. A parking sensor has no time for statistics, so it keeps only the last three readings and takes their median: the middle value of the row, which throws a single miss away whole. A lost echo clears the window outright — a parking sensor must forget what drove off:

```python
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
```

A mean is unusable here in principle: one bounce off something nearby would drag it down and the sensor would scream at empty space. The median ignores that one miss. Its cost was measured on the bench: **20 ms, exactly one frame** — less than a window of three suggests, because on a sharp approach two fresh values outvote the single old one and the median crosses the threshold on the very next reading. The project prints the lag itself:

```python
    def lag_ms(self, rate: float) -> float:
        # The median crosses the threshold once more than half the window is
        # new, i.e. it lags by (N-1)/2 readings. For a window of three that
        # is one frame, which is what the bench measured.
        return (1000 / rate if rate else 0.0) * (self.size - 1) / 2
```

Smoothing is adjustable: `--smooth 1` turns it off (watch the false alarms appear), `--smooth 9` makes the readings glassy but visibly late. Try it below: drag the car and compare a display that takes the median of three with one that averages twenty.

Interactive demo (on the web page): two displays on one sensor — a median of the last 3 readings against a mean of the last 20. Drag the car; the mean lags behind and blurs the zone edges, the median flickers but keeps up.

*Illustration: two parking displays reading the same sensor. The left one takes the median of the last 3 readings, the right one the mean of the last 20. Drag the car: the mean lags behind and blurs the zone edges; the median flickers but keeps up.*

### 2. The sensor has no target: the nearest thing wins

Worth trying yourself, and first noticed on the bench: move an object away from the sensor and at some distance the beeping, instead of stopping, suddenly gets **faster**. The fault is not in the sensor but in the question. It is not tracking your book — it has no notion of a target. Every reading is about the nearest thing inside the ~50° cone. Move the book far enough and the nearest thing becomes the desk edge, the sofa, the door frame or your own hand; that is closer, so it honestly beeps faster.

A real car does the same, and rightly so — you cannot ignore a pillar. But you have to know about it, or it looks like the sensor is lying.

The project flags these swaps: a jump of more than 0.3 m between two readings is marked "target changed". Thirty centimetres in 20 ms would be 15 m/s — objects do not move like that, so the object changed:

```python
        # The sensor does not track an object — it answers about the nearest
        # thing in the cone. Move the target past the cone edge and a desk or a
        # door frame becomes the nearest: a sudden jump.
        if self.prev is not None and abs(value - self.prev) > JUMP_M:
            self.jumped_at = time.monotonic()
            self.jumps += 1
            self.just_jumped = True
```

Animation (on the web page): a book moves away from the sensor and the beeping slows — until the book leaves the cone and the desk edge becomes the nearest thing, at which point the beeping speeds up again.

*Illustration: a book moves away from the sensor. While it is the nearest thing in the cone the beeping slows down; the moment it leaves the cone, the desk edge becomes the nearest thing — and the beeping speeds up again.*

### 3. How fast can it possibly go

There is a limit no code gets around: **the sound has to fly there**. To the object and back is 2 × distance ÷ speed of sound, and on top of that the board waits for the echo to die down, otherwise the next click mixes with the tail of the previous one (5 ms from the factory):

```text
2 × 1.5 m ÷ 343 m/s ≈ 8.7 ms in the air  +  5 ms for the echo to die down  ≈ 14 ms per reading
```

Hence the farther the target, the slower the sensor: at one metre the flight alone takes about 6 ms, at four metres 23 ms.

Measured on the board — ask it to sample faster and see what actually arrives, target at 1.5 m:

| Period requested | Actually achieved |
|---|---|
| 50 ms | 20 fps |
| 30 ms | 33 fps |
| 20 ms | 50 fps |
| 10 ms | 79 fps |

The project asks for 20 ms and gets its 50 frames a second — four times faster than the ruler at factory settings.

## What the script does

**With no flags** the script prints in the terminal: the distance and the zone, an approach bar, the beep rate, and a status line with the frame rate, the smoothing lag and the beep count. A jump between two readings flashes "target changed".

**`--plot`** opens the parking display: the distance in big digits with its zone, the zone bar with the car on it (the car leaves a one-second trail, so the spacing of the ghosts is your approach speed), one dot per beep in the next second — the row flashes in time with the sound, and turns into a solid bar in the red zone — a strip with the last ten seconds of distance against the two thresholds, red dots where the target changed, and a row of tiles: distance, zone, beep rate, smoothing lag, frame rate with lost echoes, target changes. It closes on `q`, `Esc` or the window's ✕.

![The parking display: the distance in big digits, a red-yellow-green zone bar with a car on it, and a row of beep dots](https://depz.ai/examples/sensors-02-parking.png)

*The parking display: the distance, the zone bar with the car on it, and how fast it is beeping.*

All the flags in one place:

| Flag | Default | What it does |
|---|---|---|
| `--near m` | 0.40 | the red zone: solid tone inside it |
| `--far m` | 1.50 | the yellow zone: beeping starts here |
| `--smooth N` | 3 | median of the last N readings; 1 turns smoothing off, 9 is glassy but late |
| `--temp °C` | 20 | air temperature — it barely matters for zones: 10 degrees off moves a 0.400 m threshold to 0.392 |
| `--mute` | off | no sound; the bar and the window keep working |
| `--plot` | off | the parking display window instead of the terminal (needs OpenCV) |
| `--port PATH` | auto | the board's port, if several boards are plugged in |

Taken together, `--smooth` and the two thresholds are the whole trade-off: how much of a stray to tolerate against how many milliseconds to wait, and where "close" begins for your bumper.

## Benchmark

The numbers in this article come from one bench session (21 Aug 2026): the board on a desk, a tape laid out from the sensor, a book and a palm as targets.

| What | Setup | Result |
|---|---|---|
| STOP threshold | a book brought to the 0.4 m mark on the tape | the tone goes solid exactly there |
| close threshold | the book at 1.4–1.5 m | beeping starts, about once a second |
| target swapping | the book moved out past the cone edge | the beeping speeds up — a desk edge becomes the nearest thing |
| reaction delay | 1251 readings, 25 s, a palm brought sharply from 1.3 m to 0.2 m at 2–4 m/s | median of three lags 20 ms (one frame), 40 ms at worst |
| rate vs period | target at 1.5 m, period 50/30/20/10 ms | 20/33/50/79 fps — the table above |

- **A palm appears out of nowhere.** On the way in the sensor did not see it at all: one frame showed the wall at 1.3 m, the next a palm at 0.3 m. A jump of 825 mm in 20 ms would be 41 m/s, which does not happen. A flat palm held at an angle sends the echo sideways and is nearly invisible to ultrasound — the same thing slanted surfaces do in the ruler's list of limits.
- **The full delay is about 40 ms:** up to 20 ms waiting for the next click plus 20 ms of smoothing. At 2.5 m/s that is 10 cm of travel — how much closer an obstacle gets before the sensor mentions it.
- **Sound:** the terminal bell (`\a`) was silent on this machine, in GNOME and in PyCharm alike; the synthesised tone through the system player is audible. If it is quiet, check which device the audio goes to — on the built-in card the output may have stayed on the headphones.

## The answer: is it in time?

|  | What the bench showed |
|---|---|
| **One reading** | every 20 ms — 50 a second at 1.5 m, slower the farther the target |
| **Smoothing lag** | median of three: one frame, 20 ms; 40 ms at worst |
| **Full delay** | about 40 ms, i.e. 10 cm of travel at 2.5 m/s |
| **A stray echo** | always nearer — a single one is dropped by the median, two in a row are believed |
| **A lost echo** | the display goes blank at once: nothing may linger from what drove off |

The bottom line: yes, at parking speeds — and the margin is set by three things. How much you smooth: every extra reading in `--smooth` is another 20 ms. How fast you creep: 10 cm of margin at 2.5 m/s is 4 cm at 1 m/s. And what is in the cone: soft coats and slanted panels do not answer at all, so a blank display is a warning, not an all-clear.

## Limits of the sensor

- **Soft and slanted things do not reflect.** A coat, a hedge, snow, a wall met at more than about 15° — the echo goes elsewhere and the display says nothing. For a parking sensor that is the dangerous case: treat "clear" as "nothing hard straight ahead", not as "nothing there".
- **Below two centimetres the sensor is blind:** the transducer is still ringing and drowns the echo. Set `--near` well above that.
- **A doorway is a black hole:** the sound leaves and never comes back — the display goes blank exactly as if the way were clear.

## A parking sensor is not a ruler

Same board, opposite job: the ruler trades time for precision, this project trades precision for time, and almost every decision flips — how many readings to keep, whether a nearer stray echo is noise or the most important reading of all, whether a lost echo is a statistic or a warning. The full side-by-side table is on [the ruler's page](https://depz.ai/developers/sensors/example-projects/sr04-ruler#ruler-vs-parking).

## The complete code

Everything above is taken from one file, [sr04_parking.py](https://github.com/depz-ai/depz-sensor-examples/blob/main/examples/02_sr04_parking/sr04_parking.py) — here it is in full:

The complete program: [`sr04_parking.py`](./sr04_parking.py).
