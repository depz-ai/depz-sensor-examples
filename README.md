# DEPZ Sensor Examples — hands-on labs for the DEPZ USB sensors

Short, self-contained example projects for the [DEPZ](https://depz.ai) USB
sensor line: an ultrasonic rangefinder, a time-of-flight matrix and an IMU.
One Python file per lab, ten minutes from plugging in to a working number —
and in every lab a number you can check yourself, with a tape measure, a level
or your own feet.

This is the sensor season of the series. The stereo-camera one lives in
[depz-camera-examples](https://github.com/depz-ai/depz-camera-examples).

**Status:** the ultrasonic and IMU labs are finished; the ToF labs are in
progress. More ToF labs may follow.

| # | Lab | Sensor | What it does |
|---|---|---|---|
| 1 | [An honest ruler](labs/01_sr04_ruler/) | HC-SR04 | The resolution step, rejecting stray echoes, and how many readings an honest distance takes. Predicted step 4.3 mm, measured 4.46 mm; averaging 100 readings shrinks the spread from 4.5 mm to 0.3 mm |
| 2 | [Parking sensor](labs/02_sr04_parking/) | HC-SR04 | Three zones, a bar and a real beep that quickens as you close in — and why reaction speed and accuracy pull against each other |
| 3 | [Counting crossings](labs/03_sr04_counter/) | HC-SR04 | Thresholds with hysteresis, a detection zone measured from the noise floor — and the honest limit: one sensor counts presence, not direction |
| 4 | [The first depth map](labs/04_tof_depth_map/) | VL53L8CH | 64 distances at once: junk in empty cells, the grid's real orientation, and whether a flat wall arrives flat. Wall at 1.000 m by tape, centre reads 1.009 m, cell noise 4.3 mm |
| 5 | [In and out](labs/05_tof_direction_f/) | VL53L8CH | Two halves of the matrix used as two beams: direction from which one fired first. Counts walking pace reliably; running is not verified yet |
| 6 | [A spirit level](labs/06_imu_level/) | BNO086 | Tilt from gravity alone — no calibration, no quaternion. Checked against a tape at three known angles: the scale is honest to 0.13%, the zero is 0.61° off, and the sensor's own \|g\| says why |
| 7 | [Orientation in space](labs/07_imu_orientation/) | BNO086 | The quaternion, and the two ways it lies while looking healthy: a heading the chip admits could be 180° out, and Euler angles that fall apart at vertical. Checked against a right angle: 90.65° and 89.82° |
| 8 | [A pedometer](labs/08_imu_pedometer/) | BNO086 | Counting footfalls from acceleration, checked with feet over four walks. Carried in a hand the steps vanish entirely; and no fixed threshold works at every walking speed — the settings exact on a slow walk count 42 for 30 on a brisk one |

## The hardware

Three boards. Each connects over USB-C and appears as a serial port
(`/dev/ttyACM*` on Linux, `COM*` on Windows).

| Sensor | What it measures | Range |
|---|---|---|
| **HC-SR04** ultrasonic | one distance to the nearest object in a ~50° cone | 2 cm … 4 m |
| **VL53L8CH** ToF matrix | 64 distances at once, on an 8×8 grid | up to ~4 m |
| **BNO086** IMU | orientation, acceleration, rotation | — |

The boards never send data on their own and only speak binary frames, so the
labs talk to them through `depz-sensor-sdk` rather than driving the protocol by
hand. Firmware is uploaded by the SDK at startup — the first second of a run is
silent for that reason, which is normal and not a fault.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Check that the sensor is visible:

```bash
.venv/bin/depz-sensor list
```

Run a lab:

```bash
.venv/bin/python labs/01_sr04_ruler/lab1_sr04.py
```

The port is found automatically when one board is plugged in. With several,
name it: `--port /dev/ttyACM0`.

Output is terminal graphics, so nothing extra is needed and the labs work over
ssh. The ToF and IMU labs also open a window, which needs a desktop session. Your
terminal has to support colour — every modern one does.

## If the sensor does not answer

- **Linux: `ModemManager` grabs the port.** The board is listed, but every
  attempt to open it ends in a timeout (`no reply to cmd 0x04 within 1000ms`).
  Tell ModemManager to leave DEPZ boards alone — create
  `/etc/udev/rules.d/61-depz-sensors.rules`:

  ```
  SUBSYSTEM=="tty", ATTRS{idVendor}=="1bcf", ENV{ID_MM_DEVICE_IGNORE}="1"
  SUBSYSTEM=="usb", ATTR{idVendor}=="1bcf", ENV{ID_MM_DEVICE_IGNORE}="1"

  # and, while you are here, access without sudo
  SUBSYSTEM=="tty", ATTRS{idVendor}=="1bcf", MODE="0660", GROUP="dialout", TAG+="uaccess"
  ```

  Then `sudo udevadm control --reload-rules` and replug the board. Disable the
  rule rather than the whole service — other devices need ModemManager.

- **`Device or resource busy`** — something else is holding the port: another
  script, or the viewer. Find it with `fuser -v /dev/ttyACM0`.

- **The board shows up as `0483:df11 STM Device in DFU Mode`** — it has no
  firmware on it. See the flashing instructions in the
  [SDK documentation](https://depz.ai/developers).

## Documentation

- SDK documentation and downloads: <https://depz.ai/developers>
- These examples on the website:
  <https://depz.ai/developers/sensors/example-projects>

## Licence

[MIT](LICENSE) — use the code in your own projects, commercial ones included.
