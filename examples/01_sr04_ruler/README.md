# Building an ultrasonic ruler. How accurate is DEPZ HC-SR04 USB?

> Web version with interactive demos and highlighted code: https://depz.ai/developers/sensors/example-projects/sr04-ruler

![The plot window: raw readings behind, the answer in front](https://depz.ai/examples/sensors-01-ruler.png)

*Ten seconds of a sensor pointed at a wall 0.530 m away. The pale comb is the raw readings; the green line is this project's answer — averaged, outliers dropped — sitting on the dashed measured distance.*

Hardware used: [Ultrasonic HC-SR04 USB](https://depz.ai/product/ultrasonic-sensor-hc-sr04-usb).

## Short answer

An ultrasonic sensor looks simple: send a click, wait for the echo, halve it. In practice a single reading is almost always wrong, and wrong for three different reasons at once. This project takes them apart one at a time, ends with a number you can check against a tape measure — and tells you when the sensor cannot be trusted at all.

|  | What the bench showed |
|---|---|
| **Single reading** | comes in 4.3 mm steps — one 40 kHz wave period; measured 4.46 mm apart |
| **100 readings averaged** | the spread shrinks from 4.5 mm to 0.3 mm |
| **Stray echoes** | 6–9 % at one metre, 70 % with a sofa in the cone — every one of them **nearer** than the target, so the densest cluster ignores them |
| **Against a tape** | 2.3 % short with the default air temperature; with `--temp 30` a constant −10 mm remains — bench geometry, not the sensor |
| **Readings needed** | about 50 on a clean bench, 100 with strong interference |

## Quick start

One Python file, one USB board. Plug the HC-SR04 USB in, install the SDK, run:

```bash
git clone https://github.com/depz-ai/depz-sensor-examples.git
cd depz-sensor-examples
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/depz-sensor list        # the board should show up here
```

```bash
.venv/bin/python examples/01_sr04_ruler/sr04_ruler.py --temp 30
```

Four modes, one flag apart:

| Flag | What it does |
|---|---|
| `--temp 30` | air temperature in °C (default 20) — set it, it is worth 2 % |
| `--plot` | a window with a time plot instead of the terminal (needs OpenCV) |
| `--truth 1.550` | the tape-measured distance in metres, drawn as a dashed line |
| `--study` | collect a sample set and print how much averaging you need |
| `--window 50` | averaging window in live mode |
| `--port /dev/ttyACM0` | if several boards are plugged in |

The terminal modes work without OpenCV; only `--plot` needs it. `Ctrl+C` quits; in the plot window, `q` or `Esc`.

## How the sensor measures

The transducer clicks at 40 kHz and listens. The board times how long it takes for the returning sound to cross a threshold and reports that in microseconds. We turn it into a distance ourselves: time times the speed of sound, halved — the sound travelled to the object and back.

Reading the board through the SDK is a few lines: open it, set the sample period, and consume the stream — every message carries the echo time in microseconds and a validity flag.

```python
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
```

```python
    dev.start()
    try:
        for m in dev.stream():
            win.add(echo_to_m(m.echo_time_us, args.temp) if m.valid else None)
            if not win.ready:
                continue
            st = win.stats(step_m)
```

The speed of sound depends on air temperature: 331 m/s at zero, gaining about 0.6 m/s per degree. At 20 °C that is 343 m/s. Across four metres, a cold room and a hot one differ by six centimetres, so the temperature is a flag: `--temp`.

```python
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
```

## Why a single reading is wrong — three reasons

### 1. Readings come in 4.3 mm steps — so average them

One wave period at 40 kHz lasts 25 microseconds. When the echo is loud, the threshold is crossed on the same wave every time and readings barely move. When the echo is weak, its loudness hovers around the threshold and the sensor triggers on one wave, then on the next. The result jumps by exactly one period:

```text
343 m/s × 25 µs ÷ 2 ≈ 4.3 mm
```

Halved for the same reason — the round trip. That is the **step**: no single reading can be finer than it.

**Measured on the bench.** Readings fell into two clusters, 0.0955 m and 0.0998 m, 4.46 mm apart against a predicted 4.3 mm. Confirmed.

The jitter has an upside. If the truth sits between two steps, the sensor lands on the upper one more often the closer the truth is to it. So **the average of many readings settles between the steps** and beats a single one: averaging 100 readings shrank the spread from 4.5 mm to 0.3 mm. Try it below — drag the true distance and watch the average find it between the steps.

Interactive demo (on the web page): readings snap to 4.3 mm steps with jitter; a running average converges on the true value between the steps.

### 2. It answers about the nearest thing in a 50° cone — so take the densest cluster

The sensor looks through a cone of about 50° and answers about the **nearest** object inside it, no matter where the axis points. At one metre that cone covers a circle nearly a metre across; at 1.5 m, a metre and a half.

Hence the rule: a stray object can only pull the reading **shorter**. A reading "farther than the target" has nowhere to come from.

**Measured on the bench.** Against a wall at one metre, 6–9 % of readings were strays and every single one was nearer than the target. Moving the sensor back to 1.55 m brought a sofa at the foot of the wall into the lower edge of the cone: strays jumped to 70 %, smeared from 0.8 m to 1.5 m. The wall kept giving its own narrow peak throughout.

That is why the project takes its answer from the **densest cluster, not the median**. The target alone reflects consistently and forms a narrow peak, while a sofa, a desk grazed edge-on or a door frame smear out. On the sofa bench the median missed by 0.10 m and the plain average by 0.13 m; the cluster gave the correct 1.515 m.

```python
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
```

The price of that rejection is sample size. On a short block the densest cluster can settle on a stray reflection, and then it loses to a plain average. The project prints the point where it becomes reliable: about 50 readings on a clean bench, 100 with the sofa in view.

### 3. Offset vs scale — set the temperature, then measure at two distances

Even with a single target and plenty of data, a gap against the tape remains. It has two parts, and telling them apart needs **two** distances:

- **a constant offset** — the same at any range. Everything geometric lives here: a misaimed axis, and the fact that the sensor measures the shortest path to the wall while the tape runs along its own line;
- **a scale error** — growing proportionally. Either the air temperature is wrong, or the board's clock drifts.

Separate them like this: measure, move the sensor a known distance **without turning it**, measure again. Everything constant cancels in the difference and only the scale is left.

**Measured on the bench** (21 Aug 2026, room at 30 °C):

| tape | answer at `--temp 20` | error | answer at `--temp 30` | error |
|---|---|---|---|---|
| 1.050 m | 1.020 m | −29.9 mm | 1.039 m | −10.6 mm |
| 1.550 m | 1.516 m | −34.4 mm | 1.541 m | −9.3 mm |

This is the payoff. With the temperature left at its default the sensor read 2.3 % short, which looked exactly like a scale error — a drifting board clock. But the room was at 30 °C, not 20: sound travels 349.5 m/s there instead of 343, 1.9 % faster. Passing `--temp 30` dropped the gap from 35 mm to 9.

What remains is visible in the table: **about −10 mm at both distances**. Equal, therefore an offset and not a scale error. The board's clock is honest, so is the speed of sound now, and those ten millimetres are the bench geometry: a misaimed axis, and the shortest path versus the tape's own line.

## What the tool shows

**Live mode** (the default) prints the single value, the answer with outliers dropped, the plain average and median for comparison, the window spread, jitter and a count of missing echoes. Everything comes out of one sliding window:

```python
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
```

**`--plot`** draws three things at once: the raw readings as a pale comb, the answer as a solid line, and every reading the rejection discarded as a red dot. Add `--truth` and the measured distance shows up as a dashed line, so you can see whether the answer sits on it or beside it.

```python
            # A reading counts as dropped when it sits further from the answer
            # than the cluster width used by robust_mean.
            dropped = (reading is not None and answer is not None
                       and abs(reading - answer) > step_m * 3)
            history.append((now, reading, answer, dropped))
```

The tiles below the plot deliberately cover different time spans, so they answer different questions:

| tile | what it says |
|---|---|
| ANSWER | the averaged answer, outliers dropped |
| SINGLE READING | the latest raw value, and how far a reading typically strays from the mean |
| MIN / MAX | the extremes across the whole plotted 10 s, strays included |
| SPREAD | how much it wavers right now, over the averaging window alone |
| RATE | readings per second, and how many echoes came back empty |
| VS MEASURED | the gap against `--truth` |

**`--study`** collects a sample set (hold the sensor still), draws a histogram — the steps and the stray reflections are both visible there — and builds a table of how far the answer wanders when averaged over N readings, with and without rejection. That table is where the "50 readings clean, 100 with a sofa" numbers come from.

```python
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
```

## Limits of the sensor

- **Soft and slanted surfaces do not reflect.** Fabric, foam and curtains swallow the sound; a flat surface tilted by more than about 15° sends the echo away. The tell-tale sign is the "echo lost" line.
- **Below two centimetres the sensor is blind:** the transducer is still ringing and drowns the echo.
- **A doorway is a black hole:** the sound leaves and never comes back.

## Bench notes

Bench (21 Aug 2026, room at 30 °C): board on a desk, aimed at a flat patch of wall, sofa at the foot of the wall. Distances measured with a tape to the front rim of the transducer cylinders, good to ±2–3 mm.

- **Thickness of a ChArUco board in front of the wall:** the sensor reported a 10.2 mm difference between readings with and without it. The board itself is 6 mm and did not sit flush against the wall — the remaining ~4 mm is the gap.
- **The plot window** (screenshot above) is a real capture from this bench, not a mock-up: the raw comb covers 12.9 mm — three resolution steps — at 50 Hz, while the averaged answer sits **0.1 mm** from the measured 0.530 m. That is closer than the tape can be read, so treat it as "within tape accuracy" rather than as a hundredth of a millimetre of truth.
- **A 500 mm baseline is short for a claim about scale.** The tape is good to ±3 mm, which is 0.6 % of the baseline. The conclusion holds because the remainder is the same at both points, but a three-metre baseline would settle it properly.

Next in the series: the same sensor with the opposite requirement — [a parking sensor that has to answer now](https://depz.ai/developers/sensors/example-projects/sr04-parking).

## The complete code

Everything above is taken from one file, `sr04_ruler.py` — here it is in full:

The complete program: [`sr04_ruler.py`](./sr04_ruler.py).

The complete, runnable code of this example project is on GitHub: [sr04-ruler on GitHub](https://github.com/depz-ai/depz-sensor-examples/tree/main/examples/01_sr04_ruler).
