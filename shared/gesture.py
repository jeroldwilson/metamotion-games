"""
gesture.py — Wrist-tilt gesture interpreter

Converts raw IMU samples from the MetaMotion sensor into a simple
paddle-control signal consumed by the game engine.

Orientation & calibration
──────────────────────────
On startup the interpreter collects ~1 s of samples while the sensor is
at rest to measure the neutral gravity vector (calibration).  All tilt
measurements are relative to that baseline, so it doesn't matter which
way the sensor is physically mounted on the wrist.

Gesture mapping
───────────────
  Wrist tilted LEFT  → tilt < -tilt_threshold  →  MOVE LEFT
  Wrist tilted RIGHT → tilt > +tilt_threshold  →  MOVE RIGHT
  Wrist flat         → |tilt| < tilt_threshold  →  STOP

  Wrist flick UP (quick gy spike)             →  LAUNCH / POWER-SERVE
  Wrist twist CW  (gz positive)               →  CURVE RIGHT (ball spin)
  Wrist twist CCW (gz negative)               →  CURVE LEFT  (ball spin)

  Wrist tilted FORWARD → tilt_y < -tilt_threshold → UP (Snake)
  Wrist tilted BACK    → tilt_y > +tilt_threshold → DOWN (Snake)

All thresholds are adjustable via the GestureConfig dataclass.
"""

import time
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional

from shared.sensor import IMUSample


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class GestureConfig:
    # Tilt dead-zone in g (calibration-relative lateral gravity shift).
    # ~0.05 g ≈ 3° of tilt — keeps paddle still when wrist is approximately flat.
    tilt_threshold: float = 0.05

    # Tilt value (g) at which paddle reaches full speed.
    # 0.5 g ≈ 30° of tilt — comfortable range for fast play.
    tilt_max: float = 0.5

    # Gyroscope flick threshold (°/s) to trigger a LAUNCH event.
    flick_threshold: float = 300.0

    # Low-pass smoothing factor for gravity extraction [0–1].
    # Lower = heavier filtering = slower response but motion-noise immune.
    # 0.05 at 100 Hz ≈ 2 Hz cutoff: tracks slow tilts, ignores quick shakes.
    alpha: float = 0.05

    # Number of samples in the rolling window used for flick detection.
    flick_window: int = 8

    # Dead-zone for gyro twist (°/s).
    twist_dead_zone: float = 30.0

    # Time (s) between repeated LAUNCH events to prevent accidental double-fire.
    launch_cooldown: float = 0.5

    # Samples collected for auto-calibration (≈1 s at 100 Hz).
    # Keep sensor still during this period.
    calibration_samples: int = 100

    # Minimum seconds between gesture triggers on each axis.
    # Used by game modules in accessible mode; GestureInterpreter ignores it.
    gesture_cooldown: float = 0.8


# ── Gesture state ─────────────────────────────────────────────────────────────

@dataclass
class GestureState:
    """
    Published to the game on every update tick.

    paddle_velocity : float  [-1.0 … +1.0]
        Negative = move left, positive = move right, 0 = stationary.
        Magnitude encodes speed (gentle tilt → slow, extreme tilt → fast).

    launch : bool
        True for exactly one frame when a LAUNCH flick is detected.

    spin : float  [-1.0 … +1.0]
        Wrist rotation mapped to ball spin hint.
        Negative = curve left, positive = curve right.

    tilt_y : float  [-1.0 … +1.0]
        Forward/back tilt (ay axis relative to calibrated neutral).
        Negative = wrist tilted forward (→ UP in Snake).
        Positive = wrist tilted back    (→ DOWN in Snake).

    raw_ax : float  — tilt value relative to calibrated neutral (for debug HUD)
    raw_gz : float  — smoothed gyroscope Z (for debug HUD)
    calibrated : bool  — False while collecting calibration samples

    abs_ax : float  — smoothed absolute accelerometer ax (g) — for calibration view
    abs_ay : float  — smoothed absolute accelerometer ay (g)
    abs_az : float  — smoothed absolute accelerometer az (g)
    abs_gx : float  — raw gyro gx (°/s)
    abs_gy : float  — raw gyro gy (°/s)
    abs_gz : float  — raw gyro gz (°/s)
    """
    paddle_velocity: float = 0.0
    launch: bool = False
    spin: float = 0.0
    tilt_y: float = 0.0
    raw_ax: float = 0.0
    raw_gz: float = 0.0
    calibrated: bool = False
    # Absolute IMU values for calibration visualizer
    abs_ax: float = 0.0
    abs_ay: float = 0.0
    abs_az: float = 0.0
    abs_gx: float = 0.0
    abs_gy: float = 0.0
    abs_gz: float = 0.0


# ── Main interpreter ──────────────────────────────────────────────────────────

class GestureInterpreter:
    """
    Runs in its own thread, draining the sensor queue and maintaining
    the latest GestureState.  The game reads `interpreter.state` each frame.

    Calibration happens automatically during the first ~1 s: hold the sensor
    in the neutral (rest) wrist position while the LED is first turning green.
    """

    def __init__(
        self,
        sensor_queue: queue.Queue,
        config: Optional[GestureConfig] = None,
    ):
        self._q        = sensor_queue
        self.config    = config or GestureConfig()
        self.state     = GestureState()
        self._lock     = threading.Lock()

        # Gravity-extraction low-pass filter — all 3 axes
        self._smooth_ax: float = 0.0
        self._smooth_ay: float = 0.0
        self._smooth_az: float = 0.0
        self._smooth_gz: float = 0.0

        # Calibrated neutral gravity vector
        self._cal_ax: float = 0.0
        self._cal_ay: float = 0.0
        self._cal_az: float = 0.0
        self._calibrated: bool = False
        # Accumulation buffer for calibration samples
        self._cal_buf: List[tuple] = []

        # Rolling window for flick detection (stores recent gy samples)
        self._gy_window: deque = deque(maxlen=self.config.flick_window)

        self._last_launch_time: float = 0.0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._sample_count: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="gesture-interp"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def recalibrate(self) -> None:
        """Force a new calibration cycle (call when sensor position changes)."""
        with self._lock:
            self._calibrated = False
            self._cal_buf.clear()
            self.state.calibrated = False
        print("[gesture] Recalibration started — hold sensor still…")

    def get_state(self) -> GestureState:
        """Thread-safe snapshot of the latest gesture state."""
        with self._lock:
            return GestureState(
                paddle_velocity=self.state.paddle_velocity,
                launch=self.state.launch,
                spin=self.state.spin,
                tilt_y=self.state.tilt_y,
                raw_ax=self.state.raw_ax,
                raw_gz=self.state.raw_gz,
                calibrated=self.state.calibrated,
                abs_ax=self.state.abs_ax,
                abs_ay=self.state.abs_ay,
                abs_az=self.state.abs_az,
                abs_gx=self.state.abs_gx,
                abs_gy=self.state.abs_gy,
                abs_gz=self.state.abs_gz,
            )

    # ── Processing loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                sample: IMUSample = self._q.get(timeout=0.05)
            except queue.Empty:
                # No new data — decay velocity toward zero to prevent drift
                with self._lock:
                    self.state.paddle_velocity *= 0.85
                    self.state.launch = False
                continue
            self._process(sample)

    def _process(self, s: IMUSample) -> None:
        cfg = self.config
        a   = cfg.alpha

        # ── Gravity-extraction low-pass filter (all 3 axes) ────────────────
        # Low alpha (0.05) means only very slow changes pass through —
        # exactly what we want: gravity is DC, motion acceleration is AC.
        self._smooth_ax = a * s.ax + (1 - a) * self._smooth_ax
        self._smooth_ay = a * s.ay + (1 - a) * self._smooth_ay
        self._smooth_az = a * s.az + (1 - a) * self._smooth_az
        self._smooth_gz = a * s.gz + (1 - a) * self._smooth_gz

        # ── Auto-calibration ───────────────────────────────────────────────
        # Collect the first N smoothed samples to establish the neutral
        # gravity vector.  During this phase, paddle stays at zero.
        if not self._calibrated:
            self._cal_buf.append((self._smooth_ax, self._smooth_ay, self._smooth_az))
            if len(self._cal_buf) >= cfg.calibration_samples:
                n = len(self._cal_buf)
                self._cal_ax = sum(v[0] for v in self._cal_buf) / n
                self._cal_ay = sum(v[1] for v in self._cal_buf) / n
                self._cal_az = sum(v[2] for v in self._cal_buf) / n
                self._calibrated = True
                self._cal_buf.clear()
                print(
                    f"[gesture] Calibrated — neutral gravity: "
                    f"ax={self._cal_ax:+.3f}  ay={self._cal_ay:+.3f}  az={self._cal_az:+.3f} g"
                )
            with self._lock:
                self.state.paddle_velocity = 0.0
                self.state.launch = False
                self.state.calibrated = False
            return

        # ── Tilt from gravity vector relative to calibrated neutral ────────
        # When the wrist tilts sideways, lateral gravity (ax) increases while
        # vertical gravity (az) decreases.  Subtracting the neutral ax gives
        # the pure tilt component, independent of sensor mounting orientation.
        tilt = self._smooth_ax - self._cal_ax

        thr   = cfg.tilt_threshold
        t_max = cfg.tilt_max

        if abs(tilt) < thr:
            velocity = 0.0
        else:
            # Map [thr … t_max] → [0 … 1], clamp at 1
            magnitude = (abs(tilt) - thr) / max(t_max - thr, 1e-6)
            magnitude = min(magnitude, 1.0)
            velocity  = magnitude if tilt > 0 else -magnitude

        # ── Forward/back tilt (ay axis) for Snake up/down control ─────────
        tilt_y_raw = self._smooth_ay - self._cal_ay
        if abs(tilt_y_raw) < thr:
            tilt_y = 0.0
        else:
            magnitude_y = (abs(tilt_y_raw) - thr) / max(t_max - thr, 1e-6)
            magnitude_y = min(magnitude_y, 1.0)
            tilt_y = magnitude_y if tilt_y_raw > 0 else -magnitude_y

        # ── Flick detection for LAUNCH ─────────────────────────────────────
        # Sharp spike in gy (pitch axis) = flick upward.
        self._gy_window.append(s.gy)
        launch = False
        if len(self._gy_window) == cfg.flick_window:
            peak = max(abs(v) for v in self._gy_window)
            now  = time.monotonic()
            if (
                peak > cfg.flick_threshold
                and now - self._last_launch_time > cfg.launch_cooldown
            ):
                launch = True
                self._last_launch_time = now

        # ── Spin from wrist twist (gz) ─────────────────────────────────────
        gz   = self._smooth_gz
        dead = cfg.twist_dead_zone
        if abs(gz) < dead:
            spin = 0.0
        else:
            spin = (gz - dead) / 200.0 if gz > 0 else (gz + dead) / 200.0
            spin = max(-1.0, min(1.0, spin))

        # ── Publish ────────────────────────────────────────────────────────
        with self._lock:
            self.state.paddle_velocity = velocity
            self.state.launch          = launch
            self.state.spin            = spin
            self.state.tilt_y          = tilt_y
            self.state.raw_ax          = tilt   # calibration-relative for HUD
            self.state.raw_gz          = gz
            self.state.calibrated      = True
            # Absolute IMU values for calibration visualizer
            self.state.abs_ax          = self._smooth_ax
            self.state.abs_ay          = self._smooth_ay
            self.state.abs_az          = self._smooth_az
            self.state.abs_gx          = s.gx
            self.state.abs_gy          = s.gy
            self.state.abs_gz          = s.gz

        # Log every 10th sample (~10 Hz at 100 Hz sensor rate)
        self._sample_count += 1
        if self._sample_count % 10 == 0:
            print(
                f"[gesture] tilt={tilt:+.3f}g  vel={velocity:+.3f}"
                f"  gy={s.gy:+.1f}°/s  launch={launch}"
            )


# ── Keyboard fallback (used when no sensor is connected) ─────────────────────

class KeyboardFallback:
    """
    Mimics GestureInterpreter but maps keyboard state to GestureState.
    Allows the game to run without a physical sensor for testing.
    """

    def __init__(self):
        self._left   = False
        self._right  = False
        self._launch = False
        self._lock   = threading.Lock()

    def press_left(self)     -> None:
        with self._lock: self._left = True
    def press_right(self)    -> None:
        with self._lock: self._right = True
    def release_left(self)   -> None:
        with self._lock: self._left = False
    def release_right(self)  -> None:
        with self._lock: self._right = False
    def trigger_launch(self) -> None:
        with self._lock: self._launch = True

    def get_state(self) -> GestureState:
        with self._lock:
            v = (-0.85 if self._left else 0) + (0.85 if self._right else 0)
            launch = self._launch
            self._launch = False          # one-shot
            return GestureState(
                paddle_velocity=v,
                launch=launch,
                calibrated=True,
                tilt_y=0.0,
            )

    # Lifecycle stubs (no-ops for API compatibility)
    def start(self) -> None: pass
    def stop(self)  -> None: pass
