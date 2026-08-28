# Lab 2. Parking sensor

**Sensor:** HC-SR04 ultrasonic
**What you get:** an approach bar and a beep that speeds up as an object closes
in, the way a car does it. And an understanding of why the recipes from
[lab 1](../01_sr04_ruler/) must not be used here.

## Why

Lab 1 chased accuracy: the more readings you average, the honester the
millimetres. Averaging 100 readings takes five seconds — fine for a ruler, the
wall is not going anywhere.

A parking sensor cannot work like that. Five seconds to answer is a car that has
travelled a metre past the point where it should have beeped. What matters here
is not accuracy but **being in time**. This lab is about where that line runs
and what it costs to cross it.

## About the sound

The first version beeped with the terminal bell — the `\a` from every textbook.
On real Ubuntu it turned out to be mute: both GNOME and PyCharm silence it by
default.

So the lab synthesises the sound itself: a 2200 Hz sine wave handed to `paplay`,
which ships with Ubuntu. Nothing to install.

One detail worth opening the code for. The player starts **once for the whole
lab**, not once per beep: the red zone fires sixteen beeps a second while
starting a process takes tens of milliseconds — the sensor would choke. Instead
the lab keeps one open audio stream and pours into it, a tone or silence, for
exactly as many milliseconds as real time has passed.

The second detail is the edges of a beep. Cut a sine wave off abruptly and you
hear a click, so every beep fades in and out over 4 ms.

## How it works

Three zones, like a real parking sensor:

| zone | default | behaviour |
|---|---|---|
| free | beyond 1.5 m | silent |
| close | 1.5 … 0.4 m | beeps, faster as it closes in |
| STOP | inside 0.4 m | solid tone |

The thresholds move with `--near` and `--far`. Between them the beep rate
changes smoothly, from once a second to ten times. That is deliberate: the ear
judges absolute pitch poorly but hears a **change** of rhythm very well — which
is exactly the speed of approach.

## Why a median of three, not a mean of twenty

From lab 1 we know the sensor sometimes answers about a stray object, and such a
miss is always **nearer** than the target. For a ruler you fix that by
collecting statistics. For a parking sensor you cannot — there is no time.

The compromise is the median of the last three readings. A median is the middle
value of the row, so a single miss is thrown away whole.

The cost was measured on the bench: **20 ms, exactly one frame**. Less than a
window of three suggests, and here is why: on a sharp approach two fresh values
outvote the single old one, so the median crosses the threshold on the very next
reading.

A mean is unusable here in principle. One random bounce off something nearby
would drag it down and the sensor would scream at empty space. The median
ignores that miss.

Smoothing is adjustable: `--smooth 1` turns it off entirely (watch the false
alarms appear), `--smooth 9` makes the readings glassy but visibly late. The lab
prints the lag itself.

## The sensor has no "target"

Worth trying yourself, and first noticed on the bench: move an object away from
the sensor and at some distance the beeping, instead of stopping, suddenly gets
**faster**.

The fault is not in the sensor but in the question. We assume it is tracking the
book. It is not — it has no notion of a target. Every reading is about the
nearest thing inside the ~50° cone. While the book is nearest, you measure the
book. Move it far enough away and the nearest thing becomes the desk edge, the
sofa, the door frame or your own hand. That is closer, so the sensor honestly
beeps faster.

A real car parking sensor behaves the same way, and rightly so: you cannot
ignore a pillar. But you have to know about it, or it looks like the sensor is
lying.

The lab flags these swaps: a jump of more than 0.3 m between two readings is
marked "target changed" and counted. Thirty centimetres in 20 ms would be 15 m/s
— objects do not move like that, so the object changed.

## How fast can it possibly go

There is a limit no code gets around: **the sound has to fly there**. To the
object and back is 2 × distance ÷ speed of sound. On top of that the board waits
for the echo to die down, otherwise the next click mixes with the tail of the
previous one (5 ms from the factory).

Hence a simple consequence: **the farther the target, the slower the sensor**.
At one metre the flight takes about 6 ms, at four metres 23 ms.

Measured on the board — ask it to sample faster and see what actually arrives,
target at 1.5 m:

| period requested | actually achieved |
|---|---|
| 50 ms | 20 fps |
| 30 ms | 33 fps |
| 20 ms | 50 fps |
| 10 ms | 79 fps |

The lab asks for 20 ms and gets its 50 frames a second — four times faster than
lab 1 at factory settings.

## Running it

```bash
.venv/bin/python labs/02_sr04_parking/lab2_sr04.py
```

`Ctrl+C` to quit. Useful flags:

- `--near 0.3 --far 1.2` — your own zone thresholds, in metres;
- `--smooth 1` — no smoothing, to watch the false alarms;
- `--mute` — no sound; the bar always works;
- `--temp 30` — air temperature. It barely matters for zones: being 10 degrees
  off shifts a threshold by 2 %, turning a 0.400 m red edge into 0.392. The ear
  will not notice; a tape measure will.

## What to check by hand

1. **The thresholds, with a tape.** Lay the tape out from the sensor and bring a
   book to the 0.4 m mark — the tone must go solid exactly there. Then to 1.5 m,
   where the beeping should start.
2. **Whether you can hear it.** If it is quiet, check the volume and that the
   player works at all:
   `paplay /usr/share/sounds/freedesktop/stereo/bell.oga`.
3. **Reaction speed.** Bring your palm sharply from a metre to 0.3 m. There
   should be no noticeable pause before the solid tone.
4. **What smoothing buys.** Run with `--smooth 1` and with `--smooth 9`, waving
   your hand. The first jitters, the second is smooth but late.

## What was verified

- **STOP threshold:** checked 21 Aug 2026 — the tone goes solid exactly at the
  0.4 m mark;
- **close threshold:** beeping starts at 1.4–1.5 m, about once a second, as
  predicted;
- **target swapping:** confirmed live — move an object past the cone edge and
  the beeping speeds up, because a stray object becomes the nearest one;
- **reaction delay:** measured from a recording of 1251 readings (25 s, a palm
  brought sharply from 1.3 m to 0.2 m at 2–4 m/s). The median of three lags by
  **20 ms — exactly one frame**, 40 ms at worst. The full delay is about
  **40 ms**: up to 20 ms waiting for the next click plus 20 ms of smoothing. At
  2.5 m/s that is **10 cm of travel** — how much closer an obstacle gets before
  the sensor mentions it;
- **a palm appears out of nowhere.** On the way in the sensor did not see it at
  all: one frame showed the wall at 1.3 m, the next a palm at 0.3 m. A jump of
  825 mm in 20 ms would be 41 m/s, which does not happen. A flat palm held at an
  angle sends the echo sideways and is nearly invisible to ultrasound — the same
  effect as slanted surfaces in lab 1;
- **sound:** the terminal bell (`\a`) was silent on this machine, in GNOME and
  in PyCharm alike. The synthesised tone through `paplay` is audible. If it is
  quiet, check which device the audio goes to — on the built-in card the output
  may have stayed on the headphones.
