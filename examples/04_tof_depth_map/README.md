# Read an 8×8 depth map from the VL53L8CH / VL53L8CX ToF sensor

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/tof-depth-map

64 distances at once from a VL53L8CH or VL53L8CX in Python: honour the validity flags, measure the grid's orientation — and why no cosine correction belongs in your code.

![The project window: measured tiles on the left, smoothed picture on the right](https://depz.ai/examples/sensors-04-depth-map.png)

*What the project shows when you run it. A hand held 0.38 m in front of the sensor, a wall an even metre behind it. Left: the 64 measurements, holes left as holes. Right: the same 64 numbers smoothed into a picture, with the crosshair reading underneath.*

Hardware used: [ToF VL53L8CH USB](https://depz.ai/product/tof-sensor-vl53l8ch-usb) or [ToF VL53L8CX USB](https://depz.ai/product/tof-sensor-vl53l8cx-usb).

- **Sensor:** VL53L8CH / VL53L8CX time-of-flight matrix
- **What you get:** 64 distances at once — and the three facts that decide whether those distances mean anything at all.

Everything here runs unchanged on both boards of this family: the VL53L8CH and the VL53L8CX carry the same 8×8 ranging matrix and speak to the SDK through the same class, so the code, the flags and the measured numbers below apply to either.

## Run it

```bash
.venv/bin/python examples/04_tof_depth_map/tof_depth_map.py                 # live map in a window
.venv/bin/python examples/04_tof_depth_map/tof_depth_map.py --raw           # validity filter off
.venv/bin/python examples/04_tof_depth_map/tof_depth_map.py --truth 1.000   # centre against a tape
.venv/bin/python examples/04_tof_depth_map/tof_depth_map.py --flat          # point at a flat wall
.venv/bin/python examples/04_tof_depth_map/tof_depth_map.py --terminal      # text map, for ssh
```

The window is the default here, where the ultrasonic projects default to text. Sixty four numbers refreshed fifteen times a second are a picture, and a picture read as a table of digits is not read at all. Over ssh there is no window to open, so the project notices and prints the text map instead.

The first second of every run is silent: the sensor boots with no firmware of its own, and ~84 KB of it is pushed over USB before ranging can start.

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Read an 8×8 depth map from the VL53L8CH / VL53L8CX ToF sensor](https://depz.ai/developers/sensors/example-projects/tof-depth-map)**.

The complete program: [`tof_depth_map.py`](./tof_depth_map.py).
