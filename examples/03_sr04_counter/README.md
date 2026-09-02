# Count doorway crossings with an ultrasonic sensor

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/sr04-counter

A crossing counter from one HC-SR04: two thresholds, a 300 ms release backed by measured gap statistics — and the honest limits of a single 50° cone.

![Four crossings against a flat background](https://depz.ai/examples/sensors-03-counter.png)

*Twenty seconds at 50 readings per second. The flat line is the background — a board 1.17 m away. Each dip is a person crossing the beam; the red marks are the crossings the project counted. The pale band between the two dashed lines is the hysteresis. Four crossings walked, four counted, nothing rejected.*

Hardware used: [Ultrasonic HC-SR04 USB](https://depz.ai/product/ultrasonic-sensor-hc-sr04-usb).

- **Sensor:** HC-SR04 ultrasonic
- **What you get:** a counter that reports how many times something crossed the beam — and a clear idea of what such a counter can and cannot know.

## Running it

```bash
.venv/bin/python examples/03_sr04_counter/sr04_counter.py --temp 30
```

Three seconds of countdown to step out of the beam, two seconds of background measurement, then it counts. `Ctrl+C` to stop. Add `--plot` for the window.

Flags: `--enter 0.75` and `--exit 0.85` are the thresholds as a fraction of the background, `--margin 0.10` is how far below the nearest stray the zone must stay, `--release 0.30` the clear time that ends a crossing, `--min-near 100` the milliseconds needed to count one.

## How to set it up

![The bench: sensor on a stand, a flat board as the background](https://depz.ai/examples/sensors-03-bench.jpg)

*The bench these numbers come from. The sensor sits on a light stand at the edge of a doorway; the flat chipboard panel opposite is the background, 1.170 m away by tape. People cross between them. The panel is what makes this setup work — it is flat, faces the beam head-on and returns a clean echo, so the background is a sharp number instead of the smeared 1.3–1.6 m a grazed door frame produced.*

Everything above boils down to three rules:

1. **The beam crosses the path**, like a barrier — not along it.
2. **People must pass much closer than the background.** A flat board or wall straight ahead is ideal; a door frame the beam grazes is the worst case.
3. **Nobody in the beam during the countdown**, or the background is measured against a person and the zone collapses.

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Count doorway crossings with an ultrasonic sensor](https://depz.ai/developers/sensors/example-projects/sr04-counter)**.

The complete program: [`sr04_counter.py`](./sr04_counter.py).
