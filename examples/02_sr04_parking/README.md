# Building a parking sensor. Can DEPZ HC-SR04 USB answer in time?

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/sr04-parking

An HC-SR04 as a parking sensor: zones, a beep that quickens, and the measured price of answering now — one 20 ms frame, 10 cm at 2.5 m/s.

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

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Building a parking sensor. Can DEPZ HC-SR04 USB answer in time?](https://depz.ai/developers/sensors/example-projects/sr04-parking)**.

The complete program: [`sr04_parking.py`](./sr04_parking.py).
