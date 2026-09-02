# Building an ultrasonic ruler. How accurate is DEPZ HC-SR04 USB?

> **Full article — the physics, the measured numbers, interactive demos:**
> https://depz.ai/developers/sensors/example-projects/sr04-ruler

An HC-SR04 over USB against a tape measure: how far one reading is off, how many readings fix it, and what no averaging can fix.

![On the bench: the DEPZ HC-SR04 USB between a tape measure, a steel ruler and a calliper, with a sketch of the readings](https://depz.ai/examples/sensors-01-bench.jpg)

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
python examples/01_sr04_ruler/sr04_ruler.py --temp 30 --plot
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
python examples/01_sr04_ruler/sr04_ruler.py --temp 30 --plot
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
python examples\01_sr04_ruler\sr04_ruler.py --temp 30 --plot
```

This creates a private Python environment inside the repository folder, installs the SDK and OpenCV into it and launches the example straight into its live window — nothing touches your system Python.

The window plots the raw readings as a pale comb and the answer — the last 20 readings averaged, outliers dropped — as a solid line. `--temp` alone is worth 2 % of the distance, so the command sets it — replace 30 with your room's.

`Ctrl+C` quits; in the plot window, `q` or `Esc`.

![The plot window: raw readings behind, the answer in front](https://depz.ai/examples/sensors-01-ruler.png)

*Ten seconds of readings from a wall 0.530 m away. Pale dots: raw readings. Green line: the project's answer — averaged, outliers dropped. Dashed line: the distance measured with a tape.*

## The full write-up

This README is only the launch pad. The physics, the measured numbers, every flag and the interactive demos are in the full article: **[Building an ultrasonic ruler. How accurate is DEPZ HC-SR04 USB?](https://depz.ai/developers/sensors/example-projects/sr04-ruler)**.

The complete program: [`sr04_ruler.py`](./sr04_ruler.py).
