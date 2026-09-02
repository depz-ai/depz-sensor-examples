# Detect direction of movement with a single ToF sensor (VL53L8CH / VL53L8CX)

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/tof-direction

IN/OUT counting through a doorway with one VL53L8CH or VL53L8CX: split the 8×8 field into two beams — zero false events across 232 empty frames.

![The app window: the two beams, the IN and OUT counters, and the recent events](https://depz.ai/examples/sensors-05-in-out.png)

Hardware used: [ToF VL53L8CH USB](https://depz.ai/product/tof-sensor-vl53l8ch-usb) / [ToF VL53L8CX USB](https://depz.ai/product/tof-sensor-vl53l8cx-usb).

**Sensor:** DEPZ ToF VL53L8CH / VL53L8CX USB · **Time:** 10 minutes

The code is the same for the VL53L8CH and the VL53L8CX: everything this project uses is the 8×8 ranging matrix the two boards share, so it runs unchanged on either.

[The crossings counter](https://depz.ai/developers/sensors/example-projects/sr04-counter) counted people through a doorway with one ultrasonic beam and ended with a limitation: one sensor counts **episodes of presence**, not crossings, and it cannot tell left from right. Its write-up says the fix needs two sensors side by side — whichever fires first tells you the direction.

This project keeps that promise with one board. The 8×8 matrix is split into two **virtual beams** — the left four columns and the right four. Each beam is nothing more than the presence detector from the crossings counter, and the counter only remembers two things per crossing: which beam saw the person first and which saw them last.

```python
LEFT_COLS = slice(0, 4)
RIGHT_COLS = slice(4, 8)
ALL = slice(0, SIDE)

# The board may be mounted so that people cross the field top-to-bottom
# instead of left-to-right. --vertical cuts the matrix into a top beam and a
# bottom beam instead; the names "left"/"right" become "top"/"bottom".
BEAM_PARTS = {
    "horizontal": (("left", (ALL, LEFT_COLS)), ("right", (ALL, RIGHT_COLS))),
    "vertical": (("top", (LEFT_COLS, ALL)), ("bottom", (RIGHT_COLS, ALL))),
}
```

| first on | last off | outcome |
|---|---|---|
| left | right | **in** |
| right | left | **out** |
| anything else |  | ignored — one beam only, both at once, too short |

Two numbers on the screen, nothing else: a doorway counter that reports "maybes" is a counter nobody reads.

```python
    def _close(self, now: float) -> str | None:
        self.busy = False
        held_ms = (self.quiet_since - self.started_at) * 1000.0
        if held_ms < MIN_CROSSING_MS:
            return self._ignore(f"too short, {held_ms:.0f} ms")

        # The beams' answer, if they have one: a clear first and a clear last.
        a_to_b = None
        if self.seen_a and self.seen_b and self.a.on_since != self.b.on_since \
                and self.a.off_at != self.b.off_at:
            first_is_a = self.a.on_since < self.b.on_since
            last_is_a = self.a.off_at > self.b.off_at
            if first_is_a != last_is_a:
                a_to_b = first_is_a

        # Otherwise, the runner's fallback: where the patch was at the start
        # against where it was at the end.
        travel = self.last_centre - self.first_centre
        if a_to_b is None and abs(travel) >= MIN_TRAVEL_LINES:
            a_to_b = travel > 0

        if a_to_b is None:
            if not (self.seen_a and self.seen_b):
                only = self.name_a if self.seen_a else self.name_b
                return self._ignore(f"only the {only} beam fired, patch moved "
                                    f"{travel:+.1f} lines, {held_ms / 1000:.1f} s")
            return self._ignore(f"no clear direction, patch moved {travel:+.1f} "
                                f"lines, {held_ms / 1000:.1f} s")
        self.why_ignored = None

        if self.swap:
            a_to_b = not a_to_b
        event = "in" if a_to_b else "out"
        if a_to_b:
            self.in_count += 1
        else:
            self.out_count += 1
        self.log.append((now, event))
        del self.log[:-8]
        return event

    def _ignore(self, why: str) -> None:
        self.ignored += 1
        self.why_ignored = why
        return None
```

`--swap` flips in and out if the sensor is mounted the other way round. `--vertical` cuts the matrix into a **top** and a **bottom** beam instead, for a board mounted so that people cross the field top-to-bottom.

## Run

```bash
.venv/bin/python examples/05_tof_direction/tof_direction.py               # live window
.venv/bin/python examples/05_tof_direction/tof_direction.py --swap        # in/out backwards?
.venv/bin/python examples/05_tof_direction/tof_direction.py --vertical    # people cross top-to-bottom
.venv/bin/python examples/05_tof_direction/tof_direction.py --terminal    # text, for ssh
```

The project spends the first three seconds (`--background`) learning the empty doorway — stand clear. Then walk through.

```python
def measure_background(dev, seconds: float) -> tuple[np.ndarray, int]:
    """Watch the empty doorway and remember what each cell normally sees.

    Per cell, not one number: one column may look at a frame two metres away,
    the next down an empty corridor with nothing to reflect off. Cells of the
    second kind get NaN and are handled by `covered` on their own terms.
    """
    stack: list[np.ndarray] = []
    dev.start_ranging()
    try:
        deadline = time.monotonic() + seconds
        for frame in dev.frames():
            stack.append(read_grid(frame))
            if time.monotonic() >= deadline:
                break
    finally:
        dev.stop_ranging()

    cube = np.dstack(stack)
    seen = np.sum(np.isfinite(cube), axis=2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        median = np.nanmedian(cube, axis=2)
    enough = max(3, int(len(stack) * BACKGROUND_MIN_SHARE))
    background = np.where(seen >= enough, median, np.nan)
    return background, len(stack)
```

![The live window over an empty doorway: both beams dark, one IN and one OUT counted, the recent events listed](https://depz.ai/examples/sensors-05-window.png)

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Detect direction of movement with a single ToF sensor (VL53L8CH / VL53L8CX)](https://depz.ai/developers/sensors/example-projects/tof-direction)**.

The complete program: [`tof_direction.py`](./tof_direction.py).
