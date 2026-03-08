# Arcade for All

> **Wrist-gesture controlled games for everyone** — powered by the [MbientLab MetaMotion](https://mbientlab.com/metamotion/) inertial sensor and [pygame](https://www.pygame.org/).

Tilt your wrist left and right to move a paddle. Flick your wrist up to launch a ball. Twist to put spin on it. No button presses, no joystick — the only controller is your body.

---

## Contents

1. [Concept & Goal](#concept--goal)
2. [Architecture Overview](#architecture-overview)
3. [Tech Stack](#tech-stack)
4. [Hardware — MetaMotion Sensor](#hardware--metamotion-sensor)
5. [Sensor Gesture Mapping](#sensor-gesture-mapping)
6. [Game Modes](#game-modes)
7. [Games](#games)
8. [Quick Start](#quick-start)
9. [Command-Line Reference](#command-line-reference)
10. [User Guide](#user-guide)
11. [Project Structure](#project-structure)
12. [Troubleshooting](#troubleshooting)
13. [External References](#external-references)

---

## Concept & Goal

MetaMotion Arcade explores **gesture-based interaction** as an accessible, intuitive alternative to traditional game controllers. The primary goals are:

- **Accessibility** — players with limited fine-motor control can use large, natural wrist movements instead of precise button timing.
- **Embodiment** — the mapping between body motion and on-screen action is direct and learnable: "lean left = move left."
- **Education** — the Calibration view teaches users what an inertial measurement unit (IMU) actually measures, turning the sensor into a learning tool.

The project targets the **MbientLab MetaMotion** family of wearable BLE sensors, which provide a compact 6-DoF IMU (accelerometer + gyroscope) that clips onto a wristband. A keyboard fallback mode lets the games run without hardware for development and testing.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        main.py                               │
│  Parses args, owns pygame display, drives the session loop   │
└──────────┬───────────────────────────┬───────────────────────┘
           │                           │
           ▼                           ▼
┌──────────────────┐       ┌───────────────────────┐
│  shared/         │       │  home.py              │
│  sensor.py       │       │  HomeScreen           │
│  ─ BLE thread    │──────▶│  game selection menu  │
│  ─ IMUSample     │       └──────────┬────────────┘
│    data_queue    │                  │ selected game name
└──────────┬───────┘                  ▼
           │              ┌─────────────────────────┐
           ▼              │  games/                 │
┌──────────────────┐      │  bricks/game.py         │
│  shared/         │      │  snake/game.py          │
│  gesture.py      │─────▶│  calibration/game.py   │
│  ─ Gesture-      │      │                         │
│    Interpreter   │      │  Each game:             │
│  ─ GestureState  │      │  run(gesture_src)→"home"│
└──────────────────┘      └─────────────────────────┘
```

**Data flow:**
1. `MetaMotionSensor` streams `IMUSample` objects (ax/ay/az/gx/gy/gz) over BLE into a thread-safe queue at ~100 Hz.
2. `GestureInterpreter` drains the queue, applies a low-pass gravity filter, calibrates the neutral pose, and publishes a `GestureState` (tilt velocity, launch flag, spin, etc.).
3. Games and the home screen call `gesture_src.get_state()` once per frame to read the latest gesture.

---

## Tech Stack

| Component | Library / Tool | Version |
|-----------|---------------|---------|
| Game engine & rendering | [pygame](https://www.pygame.org/) (SDL2) | ≥ 2.5 |
| BLE communication | [bleak](https://bleak.readthedocs.io/) | ≥ 0.21 |
| Numeric processing | [numpy](https://numpy.org/) | ≥ 1.24 |
| Language | Python | 3.9 – 3.12 |
| Platform | macOS 12+, Windows 10+, Linux (bluez) | |

**Why bleak over the official MetaWear SDK?**
The official [MetaWear Python SDK](https://github.com/mbientlab/MetaWear-SDK-Python) requires the `libmetawear` native shared library, which adds a compilation / packaging step. `bleak` is pure Python, cross-platform, and speaks directly to the MetaWear GATT service using the same protocol — so it works on any platform without a C toolchain. See `shared/sensor.py` for the raw BLE command protocol.

---

## Hardware — MetaMotion Sensor

This project targets the **MbientLab MetaMotion** sensor family:

| Model | IMU | Notes |
|-------|-----|-------|
| MetaMotion S | BMI160 (acc + gyro) | Recommended — compact, clip-on form factor |
| MetaMotion R / RL | BMI160 | Same firmware, larger chassis |
| MetaMotion C | BMI270 | Newer IMU; gyro register may differ (`0x04` instead of `0x05`) |
| MetaWear R / C / CPRO | BMI160 | Older hardware, same protocol |

### IMU configuration used

| Parameter | Setting |
|-----------|---------|
| Accelerometer range | ±4 g |
| Gyroscope range | ±500 °/s |
| Output data rate | 100 Hz |
| BLE connection interval | 7.5 ms (requested) |

### Sensor orientation

Wear the sensor on your wrist, **face-up** (LED / button towards the ceiling when resting).

```
         ┌───────────────┐
         │  ● MetaMotion │  ← LED / button face-up
         └───────────────┘
              wrist

  X-axis ──► lateral (left / right)
  Y-axis ──► forward / backward
  Z-axis ──► up / down  (≈ –1 g when flat, gravity pointing down)
```

More about the MetaMotion sensor: [mbientlab.com/metamotion](https://mbientlab.com/metamotion/)

---

## Sensor Gesture Mapping

The `GestureInterpreter` converts raw IMU data into game-friendly signals:

| Physical gesture | Signal | Used by |
|-----------------|--------|---------|
| Tilt wrist **left** | `paddle_velocity < 0` | Bricks paddle, home menu |
| Tilt wrist **right** | `paddle_velocity > 0` | Bricks paddle, home menu |
| Steeper tilt | Higher `abs(paddle_velocity)` | Bricks speed |
| **Flick** wrist upward (quick snap) | `launch = True` (one frame) | Bricks launch, home select |
| Rotate wrist **CW** | `spin > 0` | Bricks ball curve |
| Rotate wrist **CCW** | `spin < 0` | Bricks ball curve |
| Tilt wrist **forward** | `tilt_y < 0` | Snake up |
| Tilt wrist **backward** | `tilt_y > 0` | Snake down |

### Auto-calibration

On startup the interpreter collects ~1 second of samples (100 samples at 100 Hz) to establish the **neutral gravity vector** while the sensor is at rest. All subsequent tilt measurements are relative to that baseline, so it doesn't matter which way the sensor is mounted on the wrist. During calibration, all gestures are ignored and a "Calibrating…" overlay is shown.

### Low-pass gravity filter

A single-pole IIR filter with α = 0.05 separates slow gravity components from fast motion acceleration:

```
smooth = α × sample + (1 – α) × smooth_prev
```

At 100 Hz, α = 0.05 gives a ~2 Hz cutoff — slow enough to track gravity (DC), fast enough to follow deliberate wrist tilts, and immune to quick shaking.

---

## Game Modes

Three control and difficulty modes are available. Sensor modes can be toggled with the **M** key on the home screen.

### ASTRA — Accessible Mode (default with sensor)

Designed for players who benefit from more forgiving timing:

- **Hold-required navigation** — on the home screen, a tilt must be held for 0.35 s before switching cards; a 2.5 s cooldown prevents rapid re-triggers.
- **Wider paddle** — the Bricks paddle is larger, reducing precision demands.
- **Slower ball** — ball speed is reduced.
- **No-fail bouncing** — in Bricks, the ball does not disappear if it passes the paddle; it bounces back from the bottom wall.
- **Snake wall-wrap** — the snake wraps through walls instead of crashing.

### VEERA — Standard Mode

Standard play for users with full motor control:

- Edge-triggered navigation on the home screen (1.2 s cooldown).
- Normal paddle size and ball speed.
- Standard game rules (miss = lose a life, crash = game over).

### Keyboard Mode (`--keyboard`)

Keyboard controls replace the sensor entirely. Ideal for testing without hardware. All sensor gesture features are replaced by key presses:

| Key | Action |
|-----|--------|
| ← → | Move paddle / navigate |
| ↑ ↓ | Snake direction |
| SPACE | Launch ball |
| ESC | Pause / return to menu |
| R | Restart |
| D | Toggle debug HUD |
| F | Toggle fullscreen |
| M | Cycle ASTRA ↔ VEERA (home screen) |

---

## Games

### Bricks

Classic breakout game. Clear all bricks to advance to the next level.

**Sensor controls:** Tilt left/right to move the paddle. Flick up to launch. Rotate CW/CCW to curve the ball.

**Features:**
- 5 levels with increasing difficulty
- 6 brick colours each with different point values
- Power-up drops (wider paddle, extra life, multi-ball)
- ASTRA mode: no-fail bounce from the bottom wall

### Snake

Grid-based snake game. Eat food to grow. Don't crash.

**Sensor controls:** Tilt left/right for lateral movement. Tilt forward/backward for up/down.

**Features:**
- Smooth grid movement at configurable speed
- ASTRA mode: wall-wrap (snake passes through walls rather than crashing)
- Growing tail length with score tracking

### Calibrate *(sensor only)*

Four-panel aviation-style instrument display showing live sensor orientation. Opens as a game card on the home screen only when a sensor is connected.

```
┌─────────────────┬─────────────────┐
│  FRONT VIEW     │  SIDE VIEW      │
│  Roll           │  Pitch          │
│  (attitude AI)  │  (attitude AI)  │
├─────────────────┼─────────────────┤
│  TOP VIEW       │  SENSOR DATA    │
│  Yaw / Compass  │  Live numbers   │
│  (compass rose) │                 │
└─────────────────┴─────────────────┘
```

| Panel | What it shows |
|-------|--------------|
| **Front View** | Circular attitude indicator — sky/ground background rotates by roll angle; fixed wing symbol |
| **Side View** | Circular attitude indicator — horizon shifts up/down with pitch; side airplane profile |
| **Top View** | Compass rose (North = top); airplane silhouette rotates with integrated yaw |
| **Data** | Live pitch/roll/yaw angles; ax/ay/az (g); gx/gy/gz (°/s) |

**Angle calculations:**
- Pitch = `atan2(−ax, √(ay² + az²))`
- Roll = `atan2(ay, az)`
- Yaw = integrated `gz × Δt` (resets with SPACE; drifts without a magnetometer)

**Calibration panel controls:**

| Key | Action |
|-----|--------|
| ESC / Backspace | Return to home screen |
| SPACE / R | Reset yaw accumulator to 0° |
| F | Toggle fullscreen |

---

## Quick Start

### 1 — Clone and create environment

```bash
git clone <repo-url>
cd Bricks

python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 2 — Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3 — Bluetooth permission (macOS only)

**System Settings → Privacy & Security → Bluetooth → add Terminal (or your editor)**

Without this, `bleak` silently finds no devices.

### 4 — Run

```bash
# With sensor (auto-scan)
python main.py

# Keyboard-only (no hardware needed)
python main.py --keyboard

# Fullscreen
python main.py --fullscreen
```

See [SETUP.md](SETUP.md) for detailed setup instructions, pairing, and troubleshooting.

---

## Command-Line Reference

```
python main.py [options]

Options:
  --keyboard          Keyboard-only mode — no sensor required
  --mode MODE         Control mode: keyboard | standard | accessible
  --address ADDR      BLE address of MetaMotion device (skip scan)
  --scan              Scan for nearby BLE devices and exit
  --debug             Show live sensor HUD in game (also toggle with D)
  --fullscreen        Start in fullscreen
  --verbose, -v       Enable verbose logging
```

---

## User Guide

### Wearing the sensor

1. Attach the MetaMotion to your wrist using the clip or wristband.
2. Position it **face-up**: LED and button facing the ceiling when your arm rests on a table.
3. Hold your arm still for ~1 second while the game calibrates (green "Calibrating…" overlay disappears).

### Home screen navigation

| Input | Navigate | Select |
|-------|----------|--------|
| Sensor | Tilt left / right | Flick wrist upward |
| Mouse | Hover | Click |
| Keyboard | ← → | Enter / Space |

In ASTRA mode, hold the tilt gesture for 0.35 s before it registers.

### Playing Bricks

1. Tilt your wrist slightly to move the paddle. Steeper tilt = faster movement.
2. Flick your wrist upward to launch the ball when it's sitting on the paddle.
3. Twist your wrist CW/CCW while the ball is in play to curve it.
4. Clear all bricks to advance. Collect power-up drops when bricks break.

### Playing Snake

1. The snake starts moving automatically.
2. Tilt left/right and forward/back to steer.
3. Eat the red food dot to grow. Avoid your own tail (and walls in VEERA mode).

### Understanding Calibrate

1. Open the Calibrate card from the home screen (sensor mode only).
2. The screen shows four instrument panels.
3. **Tilt left/right** → watch the Front View AI bank left or right.
4. **Tilt forward/back** → watch the Side View AI pitch nose up/down.
5. **Rotate your wrist** → the yaw counter in the Top View integrates.
6. Press **SPACE** to reset the yaw compass to 0° (North).
7. Press **ESC** to return to the home screen.

---

## Project Structure

```
Bricks/
├── main.py                   Entry point — arg parsing, pygame init, session loop
├── home.py                   HomeScreen — game selection menu (2 or 3 cards)
├── requirements.txt          Python dependencies
├── SETUP.md                  Detailed hardware setup & troubleshooting
│
├── shared/
│   ├── sensor.py             MetaMotionSensor — BLE thread, IMUSample dataclass
│   ├── gesture.py            GestureInterpreter, GestureState, KeyboardFallback
│   └── audio.py              Audio manager
│
└── games/
    ├── bricks/
    │   └── game.py           BricksGame(screen, clock).run(gesture_src) → "home"
    ├── snake/
    │   └── game.py           SnakeGame(screen, clock).run(gesture_src) → "home"
    └── calibration/
        └── game.py           CalibrationGame — 4-panel IMU visualizer
```

### Key data types

```python
@dataclass
class IMUSample:
    timestamp: float
    ax: float   # accelerometer x (g)
    ay: float   # accelerometer y (g)
    az: float   # accelerometer z (g)
    gx: float   # gyroscope x (°/s)
    gy: float   # gyroscope y (°/s)
    gz: float   # gyroscope z (°/s)

@dataclass
class GestureState:
    paddle_velocity: float   # –1.0 … +1.0  (left/right tilt)
    launch: bool             # True for one frame on flick
    spin: float              # –1.0 … +1.0  (wrist twist)
    tilt_y: float            # –1.0 … +1.0  (forward/back)
    calibrated: bool         # False while collecting baseline
    abs_ax/ay/az: float      # smoothed absolute accelerometer (g)
    abs_gx/gy/gz: float      # raw gyroscope (°/s)
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No MetaMotion device found" | Check BLE permission (macOS); sensor LED should blink blue; run `python main.py --scan` |
| Sensor connects but no IMU data | Try repositioning near the Mac; firmware may use `0x04` instead of `0x05` for gyro — edit `_CMD_GYRO_DATA_SUB` in `sensor.py` |
| Game feels jittery | Hold sensor still during calibration; increase `calibration_samples` in `GestureConfig` |
| Yaw drifts in Calibrate panel | Expected — gyro integration accumulates error. Press SPACE to reset |
| Window won't open on macOS | `brew install sdl2 sdl2_mixer` then `pip install --force-reinstall pygame` |
| `libmetawear not found` | This project doesn't need it — run `pip uninstall metawear` |

For full troubleshooting, see [SETUP.md](SETUP.md).

---

## External References

### Hardware
- [MbientLab MetaMotion product page](https://mbientlab.com/metamotion/) — sensor specs, wristband accessories
- [MetaWear Hardware Reference](https://mbientlab.com/documents/MetaWear-Hardware-Reference.pdf) — pinouts, register maps, electrical specs
- [BMI160 datasheet (Bosch)](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi160-ds000.pdf) — IMU used in MetaMotion S/R
- [BMI270 datasheet (Bosch)](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi270-ds000.pdf) — IMU used in MetaMotion C

### BLE & Firmware
- [bleak documentation](https://bleak.readthedocs.io/) — cross-platform BLE library
- [MbientLab MetaWear Protocol (GitHub)](https://github.com/mbientlab/MetaWear-Protocol-CSharp) — GATT command reference used to implement `sensor.py`
- [MetaWear SDK Python (GitHub)](https://github.com/mbientlab/MetaWear-SDK-Python) — official SDK (requires libmetawear)
- [MetaWear SDK C++ (GitHub)](https://github.com/mbientlab/MetaWear-SDK-C-and-CPP) — native library source
- [Bluetooth SIG GATT specification](https://www.bluetooth.com/specifications/gatt/) — BLE characteristic & service standards

### Python Libraries
- [pygame documentation](https://www.pygame.org/docs/) — game engine API
- [bleak PyPI](https://pypi.org/project/bleak/) — BLE for Python
- [numpy documentation](https://numpy.org/doc/) — array math

### IMU Theory
- [Starlino IMU Guide](http://www.starlino.com/imu_guide.html) — intuitive explanation of accelerometer/gyro fusion
- [Madgwick filter paper](https://x-io.co.uk/open-source-imu-and-ahrs-algorithms/) — AHRS algorithm (complementary to this project's simpler approach)
- [Tilt sensing with accelerometers — Freescale AN3461](https://cache.freescale.com/files/sensors/doc/app_note/AN3461.pdf) — pitch/roll math from gravity vector

### Accessibility & Gesture Interaction
- [Microsoft Inclusive Design](https://inclusive.microsoft.design/) — design principles for accessible interaction
- [ACM CHI gesture research](https://dl.acm.org/doi/10.1145/3411764) — academic context for gesture-based game input
