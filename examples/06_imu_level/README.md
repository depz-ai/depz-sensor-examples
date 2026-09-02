# Build a digital spirit level with the BNO086 IMU

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/imu-level

Tilt from the gravity vector — no quaternion, no calibration. Verified against a propped plank: an honest scale and a 0.61° zero offset, traced to its cause.

![The app window: a bubble in a round eye, the tilt angles beside it](https://depz.ai/examples/sensors-06-level.png)

*What the project shows when you run it — the wedge measurement from further down this page. The board rests on a plank that was declared flat with the Z key, and one end of the plank has since been raised by 0.056 m over its 0.794 m.*

Hardware used: [IMU BNO086 USB](https://depz.ai/product/imu-sensor-bno086-usb).

**Sensor:** BNO086 IMU. **What you get:** tilt in degrees, from the one thing an IMU knows for free — which way is down. Verified against a tape measure to 0.6°, and the story of where that 0.6° comes from.

## Run it

```bash
.venv/bin/python examples/06_imu_level/imu_level.py                       # live bubble in a window
.venv/bin/python examples/06_imu_level/imu_level.py --range 5             # finer scale, ±5°
.venv/bin/python examples/06_imu_level/imu_level.py --zero                # start already zeroed
.venv/bin/python examples/06_imu_level/imu_level.py --check               # average one pose, print it
.venv/bin/python examples/06_imu_level/imu_level.py --check --truth 4.045 # against a known wedge
.venv/bin/python examples/06_imu_level/imu_level.py --terminal            # text readout, for ssh
```

In the window: **Z** declares the current pose flat, **R** goes back to the board's own axes, **Q** quits.

The window is the default, as in the ToF projects. Two angles would read perfectly well as text, but a bubble shows the direction of the lean at a glance, which is the whole reason spirit levels have bubbles. Over ssh there is no window to open, so the project notices and prints the text readout instead.

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Build a digital spirit level with the BNO086 IMU](https://depz.ai/developers/sensors/example-projects/imu-level)**.

The complete program: [`imu_level.py`](./imu_level.py).
