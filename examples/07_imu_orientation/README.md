# Get reliable orientation from the BNO086 in Python

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/imu-orientation

Rotation vector vs game vector, the one calibration motion that actually works, and why a vertical board breaks Euler angles but not quaternions.

![The app window: the board redrawn in space, the angles beside it](https://depz.ai/examples/sensors-07-orientation.png)

*What the project shows when you run it. The green box is the board: pale side with a chip is the component side, the grey block is the USB connector. Turn the real one and this one follows.*

Hardware used: [IMU BNO086 USB](https://depz.ai/product/imu-sensor-bno086-usb).

**Sensor:** BNO086 IMU. **What you get:** the full orientation of the board — and the two ways that number lies to you without ever looking broken.

## Run it

```bash
.venv/bin/python examples/07_imu_orientation/imu_orientation.py              # live board in a window
.venv/bin/python examples/07_imu_orientation/imu_orientation.py --game       # ignore the compass
.venv/bin/python examples/07_imu_orientation/imu_orientation.py --calibrate  # walk through calibration
.venv/bin/python examples/07_imu_orientation/imu_orientation.py --drift 120  # leave it still, measure drift
.venv/bin/python examples/07_imu_orientation/imu_orientation.py --two-poses --truth 90   # check a known angle
.venv/bin/python examples/07_imu_orientation/imu_orientation.py --terminal   # text output, for ssh
```

In the window: **G** swaps between the two vectors, **T** tares (calls the current pose zero, on the chip itself), **R** undoes the tare, **S** writes the calibration into the board so the next power-up starts better, **Q** quits.

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Get reliable orientation from the BNO086 in Python](https://depz.ai/developers/sensors/example-projects/imu-orientation)**.

The complete program: [`imu_orientation.py`](./imu_orientation.py).
