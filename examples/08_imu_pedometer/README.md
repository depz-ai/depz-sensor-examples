# Build a pedometer from raw IMU acceleration

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/imu-pedometer

Count steps as spikes with a threshold and a deaf window — four verified walks, and why no fixed setting is right at every walking speed.

![The app window: two step counts and the acceleration they came from](https://depz.ai/examples/sensors-08-pedometer.png)

*What the project shows when you run it. The trace is linear acceleration, the horizontal line is the threshold, the marks along the bottom are accepted steps. On the left, the project's own count; beside it, what the chip's built-in pedometer made of the same walk.*

Hardware used: [IMU BNO086 USB](https://depz.ai/product/imu-sensor-bno086-usb).

**Sensor:** BNO086 IMU. **What you get:** a step counter you can check with your own feet — and the point where a simple algorithm stops being enough, measured rather than asserted.

## Run it

```bash
.venv/bin/python examples/08_imu_pedometer/imu_pedometer.py                # live count in a window
.venv/bin/python examples/08_imu_pedometer/imu_pedometer.py --check 20     # walk 20, see who was right
.venv/bin/python examples/08_imu_pedometer/imu_pedometer.py --threshold 4  # reject weaker spikes
.venv/bin/python examples/08_imu_pedometer/imu_pedometer.py --terminal     # text output, for ssh
```

In the window, **R** resets both counters and **Q** quits. The chip's counter cannot actually be zeroed, so the project remembers where it was and subtracts — the same trick as the trip meter in a car.

```python
        if key == ord("r"):
            # The chip's counter cannot be zeroed, so remember where it was and
            # subtract — the same trick as a trip meter on a car.
            chip_at_reset = chip
            counter.steps = 0
            counter.step_times.clear()
```

Carry the board in a pocket or held against your thigh. Not in a hand: see the first section for what that costs.

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Build a pedometer from raw IMU acceleration](https://depz.ai/developers/sensors/example-projects/imu-pedometer)**.

The complete program: [`imu_pedometer.py`](./imu_pedometer.py).
