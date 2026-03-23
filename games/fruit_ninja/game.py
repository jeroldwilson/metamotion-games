"""
game.py — Fruit Slice game (pygame)

Cursor / blade
──────────────
  Sensor mode  : abs_gz (yaw °/s) → horizontal cursor velocity.
                 abs_gy (pitch °/s) → vertical cursor velocity (up = up).
                 Cursor is invisible until the hand moves; appears as a
                 glowing slash trail.  Any motion over a fruit slices it.
                 Starts at screen centre on each new game.

  Keyboard mode: Mouse cursor is the blade — moving it over fruits slices
                 them.  The cursor/trail only shows while the mouse moves.

Modes
─────
  Astra  (accessible): slower fruits, larger hit zones, auto-aim to nearest
                       fruit on any motion, 60-second session, no lives.
  Veeran (standard)  : 3 lives, bombs, normal speed — game over on 0 lives.
"""

import math
import random
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple, TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from shared.gesture import GestureState

# ── Screen ────────────────────────────────────────────────────────────────────
W, H  = 800, 600
FPS   = 60

# ── Physics ───────────────────────────────────────────────────────────────────
# Lowered from 880 → 380 so fruits float slowly and reach the top of the screen.
# Peak height = vy² / (2 × GRAVITY).  At vy=650, G=380: peak ≈ 556 px (93%).
GRAVITY      = 380.0    # px/sec² downward

# ── Gyro → cursor mapping ─────────────────────────────────────────────────────
# Raw gyro in °/s.  Cursor velocity = gyro * scale (px per °).
GYRO_SCALE_X  = 5.0     # px / (°/s) horizontal  (abs_gz → X)  — Veeran
GYRO_SCALE_Y  = 5.0     # px / (°/s) vertical    (abs_gy → Y)  — Veeran
GYRO_DEAD     = 18.0    # °/s dead-zone (prevents drift at rest) — Veeran
GYRO_VISIBLE  = 35.0    # °/s magnitude to show cursor trail — Veeran
GYRO_SLICE    = 35.0    # °/s magnitude to count as slicing — Veeran

# Astra (accessible) — much more responsive cursor
GYRO_SCALE_X_ACC = 9.0   # higher sensitivity: small hand motion → big cursor move
GYRO_SCALE_Y_ACC = 9.0
GYRO_DEAD_ACC    = 6.0   # tiny dead-zone: almost any movement registers
GYRO_VISIBLE_ACC = 8.0   # cursor trail appears with very little motion
GYRO_SLICE_ACC   = 8.0   # slice triggers with very little motion
AUTO_AIM_PULL    = 580.0 # px/sec pull toward nearest fruit when moving (Astra)

# Mouse movement (keyboard mode)
MOUSE_MOVE_PX    = 4    # pixels mouse must move to count as slicing
CURSOR_HIDE_MS   = 300  # ms after last move before cursor vanishes

# ── Game rules ────────────────────────────────────────────────────────────────
LIVES_START   = 3
ACC_DURATION  = 60       # seconds per session (accessible)
STAR_3        = 20
STAR_2        = 10

# ── Spawn ─────────────────────────────────────────────────────────────────────
SPAWN_INTERVAL     = 1.0
SPAWN_INTERVAL_ACC_START = 2.8   # Astra: wide gap between fruits at start
SPAWN_INTERVAL_ACC_FULL  = 1.8   # Astra: interval at full speed
BOMB_PROB          = 0.13

# ── Astra adaptive fruit speed ────────────────────────────────────────────────
# Speed ramps from SLOW at score 0 up to FULL at ACC_SPEED_FULL_SCORE slices.
# With GRAVITY=380, peak heights:
#   SLOW lo=-580: 580²/(2×380) ≈ 443 px (74% of screen)
#   SLOW hi=-650: 650²/(2×380) ≈ 556 px (93% of screen)
#   FULL lo=-660: 660²/(2×380) ≈ 573 px (96% of screen)
#   FULL hi=-740: 740²/(2×380) ≈ 721 px (above top — fruit pops over the edge)
ACC_VY_SLOW       = (-650, -580)   # gentle start — reaches 74–93% of screen
ACC_VY_FULL       = (-740, -660)   # full Astra speed — reaches 96%–above top
ACC_VX_SLOW       =  70            # ± horizontal drift at start (px/s)
ACC_VX_FULL       = 140            # ± horizontal drift at full speed (px/s)
ACC_SPEED_FULL_SCORE = 15          # reach full speed after this many slices

# ── Slice detection ───────────────────────────────────────────────────────────
SLICE_EXTRA_STD  = 8     # extra px added to fruit radius for hit-zone
SLICE_EXTRA_ACC  = 50    # Astra: very forgiving hit-zone

# ── Colours ───────────────────────────────────────────────────────────────────
BG       = (15, 10, 35)
TEXT_CLR = (255, 255, 255)
DIM_CLR  = (160, 160, 180)

# ── Fruit definitions ─────────────────────────────────────────────────────────
# Each entry: (name, base_radius)
FRUIT_LIST = [
    ("apple",      44),
    ("watermelon", 54),
    ("orange",     42),
    ("banana",     40),
    ("strawberry", 37),
    ("lemon",      38),
    ("pomegranate",40),
]
FRUIT_NAMES   = [d[0] for d in FRUIT_LIST]
FRUIT_RADII   = {d[0]: d[1] for d in FRUIT_LIST}
BOMB_R        = 38

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Fruit:
    x: float; y: float
    vx: float; vy: float
    kind: str; r: int
    rot: float = 0.0
    rot_spd: float = 1.5
    alive: bool = True
    hazard: bool = False


@dataclass
class FruitHalf:
    x: float; y: float
    vx: float; vy: float
    kind: str; r: int
    angle: float; flip: bool
    alpha: int = 255
    alive: bool = True


@dataclass
class Particle:
    x: float; y: float
    vx: float; vy: float
    color: Tuple[int, int, int]
    life: float; max_life: float
    r: int = 3


@dataclass
class ScoreFloat:
    x: float; y: float
    text: str
    color: Tuple[int, int, int]
    life: float = 1.2
    vy: float = -55.0


# ══════════════════════════════════════════════════════════════════════════════
# Fruit surface cache
# ══════════════════════════════════════════════════════════════════════════════

_surf_cache: dict = {}


def _fruit_surf(kind: str, r: int, alpha: int = 255) -> pygame.Surface:
    key = (kind, r, alpha)
    if key in _surf_cache:
        return _surf_cache[key]
    size = r * 2 + 12
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = r + 6
    _DRAWERS[kind](surf, cx, cy, r, alpha)
    _surf_cache[key] = surf
    return surf


def _fruit_half_surf(kind: str, r: int, flip: bool, alpha: int) -> pygame.Surface:
    size = r * 2 + 12
    full = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = r + 6
    _DRAWERS[kind](full, cx, cy, r, alpha)
    # White cut line down the centre
    lw = max(1, r // 14)
    pygame.draw.line(full, (255, 255, 255, alpha), (cx, cy - r), (cx, cy + r), lw)
    # Mask out one half
    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    mask.fill((0, 0, 0, 0))
    if flip:
        pygame.draw.rect(mask, (255, 255, 255, 255), (cx, 0, size - cx, size))
    else:
        pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, cx, size))
    result = pygame.Surface((size, size), pygame.SRCALPHA)
    result.blit(full, (0, 0))
    # Apply mask using BLEND_RGBA_MIN to cut away the other half
    result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return result


# ── Individual fruit drawing functions ────────────────────────────────────────

def _draw_apple(surf, cx, cy, r, alpha):
    a = alpha
    # Body
    pygame.draw.circle(surf, (210, 38, 38, a), (cx, cy), r)
    # Highlight
    pygame.draw.circle(surf, (255, 130, 100, int(a * 0.45)),
                       (cx - r // 3, cy - r // 3), max(2, r // 3))
    # Stem
    sw = max(2, r // 9)
    sh = max(5, r // 3)
    pygame.draw.rect(surf, (100, 58, 18, a),
                     (cx - sw // 2, cy - r - sh + 2, sw, sh))
    # Leaf
    lw, lh = max(5, r // 2), max(3, r // 4)
    pygame.draw.ellipse(surf, (55, 170, 55, a),
                        (cx + 1, cy - r - sh // 2, lw, lh))
    pygame.draw.line(surf, (40, 120, 40, a),
                     (cx + 1, cy - r - sh // 2 + lh // 2),
                     (cx + lw, cy - r - sh // 2 + lh // 2), 1)


def _draw_watermelon(surf, cx, cy, r, alpha):
    a = alpha
    # Green rind
    pygame.draw.circle(surf, (45, 155, 45, a), (cx, cy), r)
    # Lighter green stripes (3 arcs)
    for i in range(3):
        angle = -math.pi / 2 + i * 2 * math.pi / 3
        ex = int(cx + r * math.cos(angle))
        ey = int(cy + r * math.sin(angle))
        lw = max(2, r // 7)
        pygame.draw.line(surf, (85, 200, 85, int(a * 0.7)),
                         (cx, cy), (ex, ey), lw)
    # Red flesh
    pygame.draw.circle(surf, (215, 52, 52, a), (cx, cy), max(2, r - r // 6))
    # Seeds
    for i in range(5):
        angle = -math.pi / 2 + i * 2 * math.pi / 5
        dist  = r * 0.45
        sx = int(cx + dist * math.cos(angle))
        sy = int(cy + dist * math.sin(angle))
        sr = max(2, r // 10)
        pygame.draw.ellipse(surf, (28, 28, 28, a),
                            (sx - sr, sy - sr * 2, sr * 2, sr * 3))


def _draw_orange(surf, cx, cy, r, alpha):
    a = alpha
    # Body
    pygame.draw.circle(surf, (240, 145, 25, a), (cx, cy), r)
    # Highlight
    pygame.draw.circle(surf, (255, 210, 120, int(a * 0.4)),
                       (cx - r // 3, cy - r // 3), max(2, r // 3))
    # Segments (thin lines)
    for i in range(8):
        angle = i * math.pi / 4
        ex = int(cx + r * 0.9 * math.cos(angle))
        ey = int(cy + r * 0.9 * math.sin(angle))
        pygame.draw.line(surf, (200, 110, 15, int(a * 0.35)),
                         (cx, cy), (ex, ey), 1)
    # Navel
    pygame.draw.circle(surf, (200, 110, 15, int(a * 0.6)),
                       (cx, cy), max(2, r // 8))
    # Tiny stem bump
    pygame.draw.circle(surf, (80, 130, 40, a),
                       (cx, cy - r + max(2, r // 6)), max(2, r // 8))


def _draw_banana(surf, cx, cy, r, alpha):
    a = alpha
    # Banana as a thick curved crescent polygon
    pts_outer = []
    pts_inner = []
    n = 22
    for i in range(n):
        t     = i / (n - 1)
        angle = math.pi * 0.12 + t * math.pi * 0.76
        ro    = r * 1.05
        ri    = r * 0.52
        # Outer arc curves slightly upward in the middle
        yo_off = -r * 0.18 * math.sin(math.pi * t)
        yi_off = -r * 0.10 * math.sin(math.pi * t)
        pts_outer.append((int(cx + ro * math.cos(angle)),
                          int(cy + ro * 0.75 * math.sin(angle) + yo_off)))
        pts_inner.append((int(cx + ri * math.cos(angle)),
                          int(cy + ri * 0.75 * math.sin(angle) + yi_off)))
    pts = pts_outer + pts_inner[::-1]
    pygame.draw.polygon(surf, (240, 215, 45, a), pts)
    # Highlight stripe
    hi_pts = []
    for i in range(n):
        t     = i / (n - 1)
        angle = math.pi * 0.12 + t * math.pi * 0.76
        rm    = r * 0.78
        yo    = -r * 0.14 * math.sin(math.pi * t)
        hi_pts.append((int(cx + rm * math.cos(angle)),
                       int(cy + rm * 0.75 * math.sin(angle) + yo)))
    if len(hi_pts) >= 2:
        pygame.draw.lines(surf, (255, 245, 160, int(a * 0.55)), False, hi_pts,
                          max(2, r // 7))
    # Brown tips
    for p in (pts_outer[0], pts_outer[-1]):
        pygame.draw.circle(surf, (110, 65, 18, a), p, max(2, r // 8))


def _draw_strawberry(surf, cx, cy, r, alpha):
    a = alpha
    # Body: slightly pointy bottom — use a polygon
    pts = []
    n   = 30
    for i in range(n):
        angle = -math.pi / 2 + i * 2 * math.pi / n
        # Pointy at bottom: shrink radius near angle = +pi/2
        squeeze = 0.78 + 0.22 * math.cos(angle - math.pi / 2)
        px = int(cx + r * squeeze * math.cos(angle))
        py = int(cy + r * 1.12 * squeeze * math.sin(angle))
        pts.append((px, py))
    pygame.draw.polygon(surf, (210, 45, 70, a), pts)
    # Highlight
    pygame.draw.circle(surf, (255, 140, 150, int(a * 0.4)),
                       (cx - r // 3, cy - r // 3), max(2, r // 4))
    # Seeds (small yellow dots)
    rng = random.Random(42)
    for _ in range(8):
        angle = rng.uniform(0, 2 * math.pi)
        dist  = rng.uniform(r * 0.2, r * 0.72)
        sx = int(cx + dist * math.cos(angle) * 0.85)
        sy = int(cy + dist * math.sin(angle) * 1.05)
        pygame.draw.circle(surf, (255, 220, 80, a), (sx, sy), max(1, r // 10))
    # Green leaves at top
    for i in range(4):
        angle = -math.pi / 2 + (i - 1.5) * math.pi / 5
        lx = int(cx + (r * 0.6) * math.cos(angle))
        ly = int(cy + (r * 0.6) * math.sin(angle) - r * 0.2)
        pygame.draw.line(surf, (55, 165, 55, a), (cx, cy - r // 2),
                         (lx, ly), max(2, r // 8))


def _draw_lemon(surf, cx, cy, r, alpha):
    a = alpha
    # Oval body (wider than tall) with pointed ends
    pts = []
    n   = 32
    for i in range(n):
        angle   = i * 2 * math.pi / n
        stretch = 1.0 + 0.22 * (math.cos(2 * angle))   # bulge on sides
        px = int(cx + r * stretch * math.cos(angle))
        py = int(cy + r * 0.78  * math.sin(angle))
        pts.append((px, py))
    pygame.draw.polygon(surf, (245, 225, 45, a), pts)
    # Highlight
    pygame.draw.ellipse(surf, (255, 250, 180, int(a * 0.45)),
                        (cx - r // 2, cy - r // 3, r, r // 2))
    # Texture dots
    for i in range(6):
        angle = i * math.pi / 3
        dx = int(cx + r * 0.45 * math.cos(angle))
        dy = int(cy + r * 0.32 * math.sin(angle))
        pygame.draw.circle(surf, (210, 185, 25, int(a * 0.4)),
                           (dx, dy), max(1, r // 12))
    # Small nub at right end
    pygame.draw.circle(surf, (180, 155, 20, a),
                       (cx + int(r * 1.18), cy), max(2, r // 9))


def _draw_pomegranate(surf, cx, cy, r, alpha):
    a = alpha
    # Body: deep red
    pygame.draw.circle(surf, (180, 25, 45, a), (cx, cy), r)
    # Slightly lighter inner circle
    pygame.draw.circle(surf, (210, 45, 60, int(a * 0.7)), (cx, cy), max(2, r - r // 5))
    # Highlight
    pygame.draw.circle(surf, (240, 100, 110, int(a * 0.35)),
                       (cx - r // 3, cy - r // 3), max(2, r // 3))
    # Crown at top (5 small triangular points)
    crown_pts = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        cr    = (r // 5) if i % 2 == 0 else (r // 10)
        crown_pts.append((int(cx + cr * math.cos(angle)),
                          int(cy - r + r // 5 + cr * math.sin(angle))))
    pygame.draw.polygon(surf, (100, 20, 30, a), crown_pts)


def _draw_bomb(surf, cx, cy, r, alpha):
    a = alpha
    # Shadow
    pygame.draw.circle(surf, (20, 20, 20, int(a * 0.35)),
                       (cx + r // 6, cy + r // 6), r)
    # Body
    pygame.draw.circle(surf, (42, 42, 42, a), (cx, cy), r)
    # Shine
    pygame.draw.circle(surf, (100, 100, 100, int(a * 0.5)),
                       (cx - r // 3, cy - r // 3), max(2, r // 4))
    pygame.draw.circle(surf, (200, 200, 200, int(a * 0.4)),
                       (cx - r // 3, cy - r // 3), max(1, r // 7))
    # Fuse base
    fx1, fy1 = cx + r // 2, cy - r // 2
    fx2 = cx + int(r * 0.75)
    fy2 = cy - int(r * 0.95)
    pygame.draw.line(surf, (160, 100, 30, a), (fx1, fy1), (fx2, fy2),
                     max(2, r // 8))
    # Spark
    for i in range(6):
        angle = -math.pi / 4 + i * math.pi / 3
        sl = max(2, r // 6)
        pygame.draw.line(surf, (255, 200, 50, a),
                         (fx2, fy2),
                         (fx2 + int(sl * math.cos(angle)),
                          fy2 + int(sl * math.sin(angle))), 1)
    pygame.draw.circle(surf, (255, 220, 60, a), (fx2, fy2), max(2, r // 9))


_DRAWERS = {
    "apple":       _draw_apple,
    "watermelon":  _draw_watermelon,
    "orange":      _draw_orange,
    "banana":      _draw_banana,
    "strawberry":  _draw_strawberry,
    "lemon":       _draw_lemon,
    "pomegranate": _draw_pomegranate,
    "bomb":        _draw_bomb,
}

# Juice colour per fruit kind
JUICE_COLORS = {
    "apple":       (215,  55,  55),
    "watermelon":  (210,  55,  55),
    "orange":      (240, 150,  30),
    "banana":      (245, 220,  50),
    "strawberry":  (210,  55,  80),
    "lemon":       (245, 225,  45),
    "pomegranate": (180,  25,  45),
    "bomb":        ( 80,  80,  80),
}


# ══════════════════════════════════════════════════════════════════════════════
# Game class
# ══════════════════════════════════════════════════════════════════════════════

class FruitNinjaGame:
    """
    Fruit Slice.  Same interface as all arcade games:
      FruitNinjaGame(screen, clock, debug, mode, audio).run(gesture_src) → "home"
    """

    def __init__(
        self,
        screen:       pygame.Surface,
        clock:        pygame.time.Clock,
        debug:        bool = False,
        mode:         str  = "standard",
        audio=None,
        game_submode: str  = "play",   # "play" | "learn" | "test"
        username:     str  = "",
    ):
        self._clock        = clock
        self._mode         = mode
        self._audio        = audio
        self._debug        = debug
        self._game_submode = game_submode
        self._username     = username
        self._gesture_src  = None
        self._submode_toast: float = 0.0   # seconds remaining for toast message
        self._show_validation: bool = False

        # Gesture learning system — created lazily when learn/test mode is active
        self._learner = None
        if game_submode in ("learn", "test"):
            self._init_learner()
        if game_submode == "test" and self._learner is not None:
            self._learner.start_validation()
            self._show_validation = True

        _surf_cache.clear()
        self._init_layout(screen)
        self._reset()

    def _init_learner(self) -> None:
        """Create the GestureLearningSystem (deferred so it only loads when needed)."""
        if self._learner is not None:
            return
        try:
            from shared.gesture_learner import GestureLearningSystem
            self._learner = GestureLearningSystem(username=self._username)
        except ImportError:
            print("[fruit_ninja] scikit-learn not installed — learn/test mode disabled.")

    def _switch_submode(self, new_mode: str) -> None:
        """Switch between play/learn/test submodes at runtime."""
        if new_mode == self._game_submode:
            return
        # Save any pending learn data before switching away
        if self._learner is not None and self._game_submode in ("learn", "test"):
            self._learner.save_and_train()
        self._game_submode = new_mode
        self._show_validation = False
        if new_mode in ("learn", "test"):
            self._init_learner()
        if new_mode == "test" and self._learner is not None:
            self._learner.start_validation()
            self._show_validation = True
        self._submode_toast = 2.5

    # ── Layout ────────────────────────────────────────────────────────────────

    def _init_layout(self, screen: pygame.Surface) -> None:
        self._screen = screen
        self._W, self._H = screen.get_size()
        sc = min(self._W / 800, self._H / 600)
        self._sc            = sc
        self._is_fullscreen = not (self._W == 800 and self._H == 600)
        self._gravity       = GRAVITY * sc
        # Veeran gyro scales
        self._gyro_px_x = GYRO_SCALE_X     * self._W / 800
        self._gyro_px_y = GYRO_SCALE_Y     * self._H / 600
        # Astra gyro scales — higher so tiny motions move the cursor noticeably
        self._gyro_px_x_acc = GYRO_SCALE_X_ACC * self._W / 800
        self._gyro_px_y_acc = GYRO_SCALE_Y_ACC * self._H / 600

        self._font_lg = pygame.font.SysFont("monospace", max(24, int(48 * sc)), bold=True)
        self._font_md = pygame.font.SysFont("monospace", max(12, int(24 * sc)), bold=True)
        self._font_sm = pygame.font.SysFont("monospace", max( 8, int(14 * sc)))
        _surf_cache.clear()

    def _toggle_fullscreen(self) -> None:
        self._is_fullscreen = not self._is_fullscreen
        new = pygame.display.set_mode(
            (0, 0) if self._is_fullscreen else (800, 600),
            pygame.FULLSCREEN if self._is_fullscreen else 0,
        )
        self._init_layout(new)
        self._reset()

    # ── Public entry ──────────────────────────────────────────────────────────

    def run(self, gesture_src) -> str:
        self._gesture_src = gesture_src
        self._reset()
        pygame.mouse.set_visible(False)
        if self._audio:
            self._audio.start_background()
        while True:
            dt = min(self._clock.tick(FPS) / 1000.0, 0.05)
            result = self._handle_events()
            if result:
                # Save gesture session and retrain model before leaving
                if self._learner is not None:
                    self._learner.save_and_train()
                if self._audio:
                    self._audio.stop_background()
                return result
            if not self._paused and not self._game_over:
                self._update(dt)
            self._draw()
            pygame.display.flip()

    # ── State ─────────────────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._score     = 0
        self._lives     = LIVES_START
        self._paused    = False
        self._game_over = False
        self._stars     = 0
        self._timer     = float(ACC_DURATION)

        # Cursor always starts at screen centre
        self._blade_x  = float(self._W // 2)
        self._blade_y  = float(self._H // 2)
        self._moving   = False
        self._last_move_ms = 0   # ticks of last meaningful movement

        # Trail: list of (x, y, ticks_ms)
        self._trail: List[Tuple[float, float, int]] = []

        # Previous mouse pos for delta
        mx, my = pygame.mouse.get_pos()
        self._prev_mx = mx
        self._prev_my = my

        self._fruits:       List[Fruit]      = []
        self._halves:       List[FruitHalf]  = []
        self._particles:    List[Particle]   = []
        self._score_floats: List[ScoreFloat] = []
        self._miss_flash   = 0.0
        self._spawn_cd     = 0.6

    # ── Events ────────────────────────────────────────────────────────────────

    def _handle_events(self) -> Optional[str]:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit(0)
            elif event.type == pygame.KEYDOWN:
                r = self._on_key(event.key)
                if r:
                    return r
        return None

    def _on_key(self, key: int) -> Optional[str]:
        if key == pygame.K_ESCAPE:
            if self._game_over:
                return "home"
            self._paused = not self._paused
        elif key == pygame.K_x and self._paused:
            return "home"
        elif key == pygame.K_r and self._game_over:
            self._reset()
        elif key == pygame.K_r and not self._game_over:
            self._switch_submode("play")
        elif key == pygame.K_l:
            self._switch_submode("learn")
        elif key == pygame.K_t:
            self._switch_submode("test")
        elif key == pygame.K_v and self._game_submode == "test":
            if self._learner is not None:
                if not self._show_validation:
                    self._learner.start_validation()
                self._show_validation = not self._show_validation
        elif key == pygame.K_d:
            self._debug = not self._debug
        elif key == pygame.K_f:
            self._toggle_fullscreen()
        return None

    # ── Update ────────────────────────────────────────────────────────────────

    def _update(self, dt: float) -> None:
        if self._miss_flash > 0:
            self._miss_flash = max(0.0, self._miss_flash - dt)
        if self._submode_toast > 0:
            self._submode_toast = max(0.0, self._submode_toast - dt)

        gs = self._gesture_src.get_state() if self._gesture_src else None

        # Feed raw IMU data into the gesture learner buffer every frame
        if gs is not None and self._learner is not None:
            self._learner.update(gs)

        self._update_blade(dt, gs)

        if self._mode == "accessible":
            self._timer -= dt
            if self._timer <= 0:
                self._timer = 0.0
                self._game_over = True
                self._stars = (3 if self._score >= STAR_3 else
                               2 if self._score >= STAR_2 else 1)

        self._update_spawn(dt)
        self._update_fruits(dt)

        # Learn mode: try to capture a labelled gesture window
        if gs is not None and self._learner is not None and self._game_submode == "learn":
            fruits_xy = [(f.x, f.y) for f in self._fruits]
            self._learner.try_record(
                gs,
                (self._blade_x, self._blade_y),
                fruits_xy,
            )

        self._detect_slices()
        self._update_halves(dt)
        self._update_particles(dt)
        self._update_score_floats(dt)

    # ── Blade / cursor ────────────────────────────────────────────────────────

    def _update_blade(self, dt: float, gs) -> None:
        now_ms   = pygame.time.get_ticks()
        mx, my   = pygame.mouse.get_pos()
        mouse_dx = mx - self._prev_mx
        mouse_dy = my - self._prev_my
        mouse_moved = math.hypot(mouse_dx, mouse_dy) >= MOUSE_MOVE_PX

        if mouse_moved:
            # Keyboard / mouse mode: cursor follows mouse exactly
            self._blade_x = float(mx)
            self._blade_y = float(my)
            self._moving  = True
            self._last_move_ms = now_ms
        elif gs is not None:
            # Sensor mode: integrate raw gyro values
            # abs_gz (yaw)   → horizontal
            # abs_gy (pitch) → vertical (positive gy = wrist pitches up = cursor up)
            gz = gs.abs_gz
            gy = gs.abs_gy

            # Astra uses a smaller dead-zone so tiny movements still register
            dead = GYRO_DEAD_ACC if self._mode == "accessible" else GYRO_DEAD
            if abs(gz) < dead:
                gz = 0.0
            if abs(gy) < dead:
                gy = 0.0

            # Astra uses higher scale — small motion → noticeable cursor movement
            px_x = self._gyro_px_x_acc if self._mode == "accessible" else self._gyro_px_x
            px_y = self._gyro_px_y_acc if self._mode == "accessible" else self._gyro_px_y

            # Test mode: ML model predicts direction; normal: raw gyro integration
            if self._game_submode == "test" and self._learner is not None:
                tdx, tdy = self._learner.get_cursor_delta(gs, px_x, px_y, dt)
                self._blade_x += tdx
                self._blade_y += tdy
            else:
                self._blade_x += -gz * px_x * dt   # invert: yaw left → cursor left
                self._blade_y += gy  * px_y * dt   # invert: raise hand → cursor up

            gyro_mag       = math.hypot(gz, gy)
            visible_thresh = GYRO_VISIBLE_ACC if self._mode == "accessible" else GYRO_VISIBLE
            slice_thresh   = GYRO_SLICE_ACC   if self._mode == "accessible" else GYRO_SLICE

            self._moving = (gyro_mag >= slice_thresh) or gs.launch
            if gyro_mag >= visible_thresh or gs.launch:
                self._last_move_ms = now_ms

            # Astra auto-aim: when moving, pull blade toward nearest fruit
            # Pull is proportional to distance so it feels assistive, not jarring
            if self._mode == "accessible" and self._moving and self._fruits:
                nearest = min(
                    self._fruits,
                    key=lambda f: math.hypot(f.x - self._blade_x, f.y - self._blade_y),
                )
                dx = nearest.x - self._blade_x
                dy = nearest.y - self._blade_y
                d  = math.hypot(dx, dy)
                if d > 1.0:
                    # Stronger pull when fruit is further away, gentler when close
                    pull_frac = min(1.0, d / (self._W * 0.3))
                    pull = AUTO_AIM_PULL * pull_frac * self._sc * dt
                    self._blade_x += dx / d * pull
                    self._blade_y += dy / d * pull
        else:
            self._moving = False

        # Clamp to screen
        self._blade_x = max(0.0, min(float(self._W), self._blade_x))
        self._blade_y = max(0.0, min(float(self._H), self._blade_y))

        # Build trail (only while visibly moving)
        cursor_visible = (now_ms - self._last_move_ms) < CURSOR_HIDE_MS
        if cursor_visible:
            self._trail.append((self._blade_x, self._blade_y, now_ms))
        self._trail = [(x, y, t) for x, y, t in self._trail
                       if now_ms - t <= CURSOR_HIDE_MS]

        self._prev_mx = mx
        self._prev_my = my

    # ── Spawn ─────────────────────────────────────────────────────────────────

    def _acc_progress(self) -> float:
        """0.0 (start) → 1.0 (full speed) based on score. Astra only."""
        return min(1.0, self._score / ACC_SPEED_FULL_SCORE)

    def _update_spawn(self, dt: float) -> None:
        self._spawn_cd -= dt
        if self._spawn_cd > 0 or len(self._fruits) >= 8:
            return
        self._spawn_fruit()
        if self._mode == "accessible":
            t        = self._acc_progress()
            interval = SPAWN_INTERVAL_ACC_START + t * (SPAWN_INTERVAL_ACC_FULL - SPAWN_INTERVAL_ACC_START)
        else:
            interval = max(0.5, SPAWN_INTERVAL - self._score * 0.01)
        self._spawn_cd = random.uniform(interval * 0.75, interval * 1.25)

    def _spawn_fruit(self) -> None:
        sc  = self._sc
        # Random X, start just below bottom
        x   = random.uniform(0.1 * self._W, 0.9 * self._W)
        y   = float(self._H + 15)

        # Velocity: Astra ramps from gentle to full speed with score;
        # Veeran always uses full speed.
        if self._mode == "accessible":
            t   = self._acc_progress()           # 0.0 → 1.0
            lo  = ACC_VY_SLOW[0] + t * (ACC_VY_FULL[0] - ACC_VY_SLOW[0])
            hi  = ACC_VY_SLOW[1] + t * (ACC_VY_FULL[1] - ACC_VY_SLOW[1])
            vy  = random.uniform(lo, hi) * sc
            vxr = ACC_VX_SLOW + t * (ACC_VX_FULL - ACC_VX_SLOW)
            vx  = random.uniform(-vxr, vxr) * sc
        else:
            # Standard: -740 to -900 → peak 721–1067 px (all reach top or above)
            vy  = random.uniform(-900, -740) * sc
            vx  = random.uniform(-190, 190) * sc

        if random.random() < BOMB_PROB and self._mode != "accessible":
            r = int(BOMB_R * sc)
            fruit = Fruit(x=x, y=y, vx=vx, vy=vy, kind="bomb", r=r,
                          hazard=True, rot_spd=random.uniform(-3.5, 3.5))
        else:
            name = random.choice(FRUIT_NAMES)
            r    = int(FRUIT_RADII[name] * sc)
            fruit = Fruit(x=x, y=y, vx=vx, vy=vy, kind=name, r=r,
                          rot_spd=random.uniform(-2.5, 2.5))
        self._fruits.append(fruit)

    # ── Physics ───────────────────────────────────────────────────────────────

    def _update_fruits(self, dt: float) -> None:
        for f in self._fruits:
            if not f.alive:
                continue
            f.vy  += self._gravity * dt
            f.x   += f.vx * dt
            f.y   += f.vy * dt
            f.rot  = (f.rot + f.rot_spd * dt) % (2 * math.pi)
            if f.y > self._H + f.r * 2:
                f.alive = False
                if not f.hazard:
                    self._on_miss()
        self._fruits = [f for f in self._fruits if f.alive]

    def _on_miss(self) -> None:
        if self._mode != "accessible":
            self._lives -= 1
            self._miss_flash = 0.45
            if self._lives <= 0:
                self._game_over = True
                self._stars = 0

    # ── Slice detection ───────────────────────────────────────────────────────

    def _detect_slices(self) -> None:
        if not self._moving or len(self._trail) < 1:
            return

        extra = int((SLICE_EXTRA_ACC if self._mode == "accessible" else SLICE_EXTRA_STD)
                    * self._sc)
        bx, by = self._blade_x, self._blade_y

        for fruit in self._fruits:
            if not fruit.alive:
                continue
            hit_r = fruit.r + extra
            # Check current blade position
            if math.hypot(fruit.x - bx, fruit.y - by) <= hit_r:
                fruit.alive = False
                self._on_slice(fruit)
                continue
            # Check trail segment (last two points)
            if len(self._trail) >= 2:
                x0, y0, _ = self._trail[-2]
                x1, y1, _ = self._trail[-1]
                if _seg_circle(x0, y0, x1, y1, fruit.x, fruit.y, hit_r):
                    fruit.alive = False
                    self._on_slice(fruit)

    def _on_slice(self, fruit: Fruit) -> None:
        if fruit.hazard:
            self._spawn_explosion(fruit.x, fruit.y)
            self._miss_flash = 0.6
            self._score_floats.append(ScoreFloat(
                x=fruit.x, y=fruit.y, text="BOMB!", color=(255, 80, 40)))
            if self._mode != "accessible":
                self._lives -= 1
                if self._lives <= 0:
                    self._game_over = True
                    self._stars = 0
        else:
            self._score += 1
            if self._audio:
                self._audio.play_collect()
            self._spawn_halves(fruit)
            clr = JUICE_COLORS.get(fruit.kind, (200, 200, 200))
            self._spawn_juice(fruit.x, fruit.y, clr, fruit.r)
            self._score_floats.append(ScoreFloat(
                x=fruit.x, y=fruit.y, text="+1", color=(255, 255, 255)))

    # ── Halves & particles ────────────────────────────────────────────────────

    def _spawn_halves(self, fruit: Fruit) -> None:
        sc = self._sc
        for flip in (False, True):
            spread = random.uniform(90, 200) * sc
            self._halves.append(FruitHalf(
                x=fruit.x, y=fruit.y,
                vx=fruit.vx + spread * (-1 if flip else 1),
                vy=fruit.vy * 0.45 - random.uniform(20, 80) * sc,
                kind=fruit.kind, r=fruit.r,
                angle=fruit.rot, flip=flip,
            ))

    def _spawn_juice(self, x, y, color, r) -> None:
        sc = self._sc
        for _ in range(random.randint(9, 16)):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(80, 300) * sc
            life  = random.uniform(0.3, 0.7)
            size  = max(2, int(random.uniform(2, 5) * sc))
            jit   = lambda c: max(0, min(255, c + random.randint(-25, 25)))
            self._particles.append(Particle(
                x=x, y=y,
                vx=speed * math.cos(angle),
                vy=speed * math.sin(angle) - random.uniform(30, 110) * sc,
                color=(jit(color[0]), jit(color[1]), jit(color[2])),
                life=life, max_life=life, r=size,
            ))

    def _spawn_explosion(self, x, y) -> None:
        sc = self._sc
        for _ in range(22):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(90, 340) * sc
            life  = random.uniform(0.3, 0.7)
            r     = max(2, int(random.uniform(2, 6) * sc))
            rv    = random.randint(170, 255)
            self._particles.append(Particle(
                x=x, y=y,
                vx=speed * math.cos(angle),
                vy=speed * math.sin(angle),
                color=(rv, random.randint(40, 80), 15),
                life=life, max_life=life, r=r,
            ))

    def _update_halves(self, dt: float) -> None:
        for h in self._halves:
            if not h.alive:
                continue
            h.vy    += self._gravity * dt
            h.x     += h.vx * dt
            h.y     += h.vy * dt
            h.angle += 2.8 * dt
            h.alpha  = max(0, h.alpha - int(dt * 195))
            if h.alpha == 0 or h.y > self._H + 80:
                h.alive = False
        self._halves = [h for h in self._halves if h.alive]

    def _update_particles(self, dt: float) -> None:
        for p in self._particles:
            p.vy   += self._gravity * 0.38 * dt
            p.x    += p.vx * dt
            p.y    += p.vy * dt
            p.life -= dt
        self._particles = [p for p in self._particles if p.life > 0]

    def _update_score_floats(self, dt: float) -> None:
        for sf in self._score_floats:
            sf.y   += sf.vy * dt
            sf.life -= dt
        self._score_floats = [sf for sf in self._score_floats if sf.life > 0]

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self._screen.fill(BG)
        self._draw_bg()
        self._draw_halves()
        self._draw_fruits()
        self._draw_particles()
        self._draw_trail()
        self._draw_score_floats()
        self._draw_hud()
        if self._miss_flash > 0:
            self._draw_miss_flash()
        if self._debug:
            self._draw_debug()
        if self._paused:
            self._draw_overlay("PAUSED", "ESC to resume   X to menu")
        if self._game_over:
            self._draw_game_over()
        if self._show_validation and self._game_submode == "test":
            self._draw_validation_panel()

    def _draw_bg(self) -> None:
        surf = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        cx   = self._W // 2
        base = self._H + int(40 * self._sc)
        for i in range(5, 0, -1):
            pygame.draw.circle(surf, (28, 16, 55, i * 4),
                               (cx, base), int(self._H * 0.62 * i / 3))
        self._screen.blit(surf, (0, 0))

    def _draw_fruits(self) -> None:
        for f in self._fruits:
            s = _fruit_surf(f.kind, f.r, 255)
            # Rotate
            rs = pygame.transform.rotate(s, math.degrees(f.rot))
            self._screen.blit(rs, rs.get_rect(center=(int(f.x), int(f.y))))

    def _draw_halves(self) -> None:
        for h in self._halves:
            s = _fruit_half_surf(h.kind, h.r, h.flip, h.alpha)
            rs = pygame.transform.rotate(s, math.degrees(h.angle))
            self._screen.blit(rs, rs.get_rect(center=(int(h.x), int(h.y))))

    def _draw_particles(self) -> None:
        for p in self._particles:
            a = max(0, int(255 * p.life / p.max_life))
            if a == 0:
                continue
            s = pygame.Surface((p.r * 2, p.r * 2), pygame.SRCALPHA)
            pygame.draw.circle(s, (*p.color, a), (p.r, p.r), p.r)
            self._screen.blit(s, (int(p.x) - p.r, int(p.y) - p.r))

    def _draw_trail(self) -> None:
        """Draw glowing blade trail — only visible while moving."""
        now_ms = pygame.time.get_ticks()
        pts    = self._trail[-24:]
        if len(pts) < 2:
            return
        for i in range(1, len(pts)):
            x0, y0, t0 = pts[i - 1]
            x1, y1, t1 = pts[i]
            age  = (now_ms - t1) / CURSOR_HIDE_MS
            fade = max(0.0, 1.0 - age)
            if fade <= 0:
                continue
            w = max(1, int((4.0 - age * 3.5) * self._sc))
            c = int(230 * fade)
            pygame.draw.line(self._screen,
                             (c, int(c * 0.82) + 20, 255),
                             (int(x0), int(y0)), (int(x1), int(y1)), w)
        # Tip glow
        x, y, _ = pts[-1]
        age = (now_ms - pts[-1][2]) / CURSOR_HIDE_MS
        if age < 0.4:
            fade = 1.0 - age / 0.4
            gr   = max(2, int(7 * self._sc * fade))
            gs   = pygame.Surface((gr * 2 + 4, gr * 2 + 4), pygame.SRCALPHA)
            a    = int(180 * fade)
            pygame.draw.circle(gs, (200, 230, 255, a), (gr + 2, gr + 2), gr)
            self._screen.blit(gs, (int(x) - gr - 2, int(y) - gr - 2))

    def _draw_score_floats(self) -> None:
        for sf in self._score_floats:
            a = max(0, min(255, int(255 * sf.life / 1.2)))
            s = self._font_md.render(sf.text, True, sf.color)
            s.set_alpha(a)
            self._screen.blit(s, s.get_rect(center=(int(sf.x), int(sf.y))))

    def _draw_hud(self) -> None:
        sc = self._sc
        ss = self._font_md.render(f"Score: {self._score}", True, TEXT_CLR)
        self._screen.blit(ss, (max(4, int(12 * sc)), max(4, int(8 * sc))))

        if self._mode == "accessible":
            t   = max(0.0, self._timer)
            clr = (255, 100, 100) if t < 10 else TEXT_CLR
            ts  = self._font_md.render(f"{t:.0f}s", True, clr)
            self._screen.blit(ts, ts.get_rect(
                center=(self._W // 2, max(12, int(18 * sc)))))
            badge = self._font_sm.render("ASTRA", True, (80, 220, 100))
            self._screen.blit(badge, badge.get_rect(
                right=self._W - max(4, int(10 * sc)),
                top=max(4, int(8 * sc))))
        else:
            r   = max(6, int(10 * sc))
            gap = r * 2 + max(3, int(5 * sc))
            y   = max(r + 2, int(16 * sc))
            for i in range(self._lives):
                x = self._W - max(r + 4, int(18 * sc)) - i * gap
                pygame.draw.circle(self._screen, (215, 50, 50), (x, y), r)
                pygame.draw.circle(self._screen, (255, 150, 100),
                                   (x - r // 3, y - r // 3), max(1, r // 3))

        # Learn / test submode indicators
        if self._game_submode == "learn" and self._learner is not None:
            n    = self._learner.total_recordings
            lbl  = self._font_sm.render(f"LEARN  {n} rec  [R=regular T=test]", True, (255, 130, 60))
            self._screen.blit(lbl, lbl.get_rect(
                right=self._W - max(4, int(10 * sc)),
                bottom=self._H - max(4, int(8 * sc))))
            if self._learner.rec_flash_active:
                rec_s = self._font_md.render("● REC", True, (255, 60, 60))
                self._screen.blit(rec_s, rec_s.get_rect(
                    center=(self._W // 2, max(24, int(40 * sc)))))

        elif self._game_submode == "test" and self._learner is not None:
            if self._learner.model_ready:
                lbl = self._font_sm.render(
                    "TEST  MODEL READY  [V=validate  R=regular  L=learn]",
                    True, (100, 220, 255))
                self._screen.blit(lbl, lbl.get_rect(
                    right=self._W - max(4, int(10 * sc)),
                    bottom=self._H - max(4, int(8 * sc))))
            else:
                n = self._learner.saved_sample_count
                need = 10
                if n == 0:
                    reason = "no data yet — press L to learn first"
                elif n < need:
                    reason = f"only {n}/{need} samples — press L and play more"
                else:
                    reason = f"{n} samples saved — retraining…"
                lbl = self._font_sm.render(
                    f"TEST  NO MODEL  ({reason})", True, (255, 140, 100))
                self._screen.blit(lbl, lbl.get_rect(
                    right=self._W - max(4, int(10 * sc)),
                    bottom=self._H - max(4, int(8 * sc))))

        elif self._game_submode == "play":
            hint = self._font_sm.render("L=learn  T=test", True, (100, 100, 130))
            self._screen.blit(hint, hint.get_rect(
                right=self._W - max(4, int(10 * sc)),
                bottom=self._H - max(4, int(8 * sc))))

        # Submode switch toast
        if self._submode_toast > 0:
            labels = {"play": "REGULAR MODE", "learn": "LEARN MODE", "test": "TEST MODE"}
            colors = {"play": (180, 180, 255), "learn": (255, 130, 60), "test": (100, 220, 255)}
            toast_lbl = labels.get(self._game_submode, self._game_submode.upper())
            toast_clr = colors.get(self._game_submode, TEXT_CLR)
            alpha = min(255, int(self._submode_toast / 2.5 * 255))
            ts = self._font_md.render(toast_lbl, True, toast_clr)
            ts.set_alpha(alpha)
            self._screen.blit(ts, ts.get_rect(center=(self._W // 2, max(60, int(90 * sc)))))

    def _draw_miss_flash(self) -> None:
        a    = int(self._miss_flash / 0.45 * 90)
        surf = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        surf.fill((255, 40, 40, a))
        self._screen.blit(surf, (0, 0))

    def _draw_debug(self) -> None:
        gs  = self._gesture_src.get_state() if self._gesture_src else None
        sc  = self._sc
        lines = [
            f"blade ({self._blade_x:.0f}, {self._blade_y:.0f})",
            f"moving: {self._moving}",
        ]
        if gs:
            lines += [
                f"abs_gz (yaw H):  {gs.abs_gz:+.1f} °/s",
                f"abs_gy (pitch V):{gs.abs_gy:+.1f} °/s",
                f"launch: {gs.launch}",
            ]
        row = max(10, int(16 * sc))
        y0  = self._H - max(60, int(100 * sc))
        for i, line in enumerate(lines):
            s = self._font_sm.render(line, True, (180, 220, 180))
            self._screen.blit(s, (10, y0 + i * row))

    def _draw_overlay(self, title: str, subtitle: str = "") -> None:
        sc  = self._sc
        dim = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self._screen.blit(dim, (0, 0))
        t = self._font_lg.render(title, True, TEXT_CLR)
        self._screen.blit(t, t.get_rect(
            center=(self._W // 2, self._H // 2 - max(15, int(30 * sc)))))
        if subtitle:
            s = self._font_md.render(subtitle, True, DIM_CLR)
            self._screen.blit(s, s.get_rect(
                center=(self._W // 2, self._H // 2 + max(10, int(20 * sc)))))

    def _draw_game_over(self) -> None:
        sc = self._sc
        cx = self._W // 2
        cy = self._H // 2

        dim = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 180))
        self._screen.blit(dim, (0, 0))

        if self._game_submode == "learn" and self._learner is not None:
            title = "Session Done!"
            sub   = f"Captured {self._learner.total_recordings} gestures — saving model…"
        elif self._game_submode == "test":
            title = "Test Done!"
            sub   = "Model drove the blade — how did it feel?"
        elif self._mode == "accessible":
            title = "Great Practice!"
            sub   = "You kept moving — awesome!"
        else:
            title = "GAME OVER!" if self._lives <= 0 else "TIME'S UP!"
            sub   = "Nice slicing!"

        t = self._font_lg.render(title, True, TEXT_CLR)
        self._screen.blit(t, t.get_rect(center=(cx, cy - int(80 * sc))))

        sc_s = self._font_md.render(f"Score: {self._score}", True, TEXT_CLR)
        self._screen.blit(sc_s, sc_s.get_rect(center=(cx, cy - int(38 * sc))))

        sz   = max(14, int(26 * sc))
        gap  = int(8 * sc)
        sx0  = cx - (3 * (sz * 2 + gap)) // 2
        for i in range(3):
            filled = i < self._stars
            clr    = (255, 225, 50) if filled else (70, 70, 95)
            self._draw_star(sx0 + i * (sz * 2 + gap), cy + int(8 * sc),
                            sz, clr, filled)

        sub_s = self._font_sm.render(sub, True, DIM_CLR)
        self._screen.blit(sub_s, sub_s.get_rect(center=(cx, cy + int(52 * sc))))

        ctrl = self._font_sm.render("R = play again   ESC = menu", True, DIM_CLR)
        self._screen.blit(ctrl, ctrl.get_rect(center=(cx, cy + int(82 * sc))))

    def _draw_validation_panel(self) -> None:
        """
        Fixed-size validation panel pinned to the bottom-right corner.
        Size is constant regardless of window/fullscreen resolution.
        """
        from shared.gesture_learner import DIRECTIONS

        # ── Fixed pixel dimensions — never scale with sc ──────────────────────
        PW   = 300   # panel width  (px)
        PAD  = 10    # inner padding
        MARGIN = 10  # gap from screen edge
        FONT = self._font_sm   # always use the small font

        # ── Fonts baked at fixed sizes (independent of sc) ────────────────────
        f_title = pygame.font.SysFont("monospace", 11, bold=True)
        f_body  = pygame.font.SysFont("monospace", 10)

        # ── Build content lines first so we know panel height ─────────────────
        lines = []   # list of (text, color, indent)

        def add(text, color=(180, 180, 200), indent=0):
            lines.append((text, color, indent))

        add("MODEL VALIDATION  [V close]", (100, 180, 255))
        add("")

        if self._learner is None:
            add("No learner.", (200, 100, 100))
        elif self._learner.validation_running:
            dots = "." * (1 + (pygame.time.get_ticks() // 400) % 3)
            add(f"Validating{dots}", (220, 210, 80))
            add("5-fold CV running…", (110, 110, 140))
        else:
            res = self._learner.validation_result
            if res is None:
                add("Press V to validate.", (140, 140, 170))
            elif res.error:
                add(f"! {res.error}", (220, 90, 70))
            else:
                acc_pct = int(res.overall_accuracy * 100)
                acc_clr = (80, 215, 100) if acc_pct >= 75 else \
                          (230, 195, 55) if acc_pct >= 55 else (215, 75, 75)
                add(f"Accuracy: {acc_pct}%", acc_clr)
                add(f"{res.n_samples} samples  {res.n_sessions} sessions", (110, 110, 140))
                add("")

                dir_labels = {"right": "→R", "left": "←L", "up": "↑U", "down": "↓D"}
                dirs_in_data = [d for d in DIRECTIONS if d in res.per_class]

                add("Dir   Acc   n    bar", (110, 110, 150))
                add("─" * 28, (50, 50, 75))
                for d in dirs_in_data:
                    info    = res.per_class[d]
                    pct     = int(info["accuracy"] * 100)
                    support = info["support"]
                    is_weak = (d == res.weakest_class)
                    clr     = (255, 215, 70) if is_weak else (185, 185, 210)
                    bar_filled = int(info["accuracy"] * 8)
                    bar_str    = "█" * bar_filled + "░" * (8 - bar_filled)
                    add(f"{dir_labels[d]:<5} {pct:>3}%  {support:>3}  {bar_str}", clr)

                add("")
                short = {"right": "R", "left": "L", "up": "U", "down": "D"}
                dirs_m = [d for d in DIRECTIONS if d in res.confusion]
                if dirs_m:
                    add("Confusion (true→pred):", (110, 110, 150))
                    hdr = "     " + "  ".join(f"{short[d]:<2}" for d in dirs_m)
                    add(hdr, (100, 130, 170))
                    max_off = max(
                        (res.confusion[t][p] for t in dirs_m for p in dirs_m if t != p),
                        default=1) or 1
                    for true_d in dirs_m:
                        row = f"{short[true_d]:<3}  "
                        row += "  ".join(f"{res.confusion[true_d].get(p,0):<2}" for p in dirs_m)
                        diag_ok = res.confusion[true_d].get(true_d, 0)
                        row_clr = (80, 200, 100) if diag_ok >= res.per_class[true_d]["support"] * 0.7 \
                                  else (185, 185, 210)
                        add(row, row_clr)

                if res.weakest_class and not res.error:
                    weak_acc = int(res.per_class[res.weakest_class]["accuracy"] * 100)
                    if weak_acc < 80:
                        arrow = {"right": "→", "left": "←", "up": "↑", "down": "↓"}
                        add("")
                        add(f"Tip: more {arrow.get(res.weakest_class,'')} {res.weakest_class} ({weak_acc}%)",
                            (255, 210, 70))

        # ── Measure height ────────────────────────────────────────────────────
        LINE_H = 13
        PH = PAD * 2 + len(lines) * LINE_H

        # ── Position: bottom-right corner, fixed margin ───────────────────────
        px = self._W - PW - MARGIN
        py = self._H - PH - MARGIN

        # ── Draw panel background (semi-transparent) ──────────────────────────
        panel = pygame.Surface((PW, PH), pygame.SRCALPHA)
        panel.fill((6, 6, 18, 155))        # more transparent: alpha 155
        self._screen.blit(panel, (px, py))
        pygame.draw.rect(self._screen, (60, 100, 170, 180),
                         (px, py, PW, PH), 1, border_radius=6)

        # ── Render text lines ─────────────────────────────────────────────────
        iy = py + PAD
        for text, color, *_ in lines:
            if text:
                surf = f_body.render(text, True, color)
                self._screen.blit(surf, (px + PAD, iy))
            iy += LINE_H

    def _draw_star(self, cx, cy, size, color, filled) -> None:
        pts = []
        for i in range(10):
            angle = -math.pi / 2 + i * math.pi / 5
            r     = size if i % 2 == 0 else size * 0.4
            pts.append((int(cx + r * math.cos(angle)),
                        int(cy + r * math.sin(angle))))
        if filled:
            pygame.draw.polygon(self._screen, color, pts)
        else:
            pygame.draw.polygon(self._screen, color, pts, max(1, int(2 * self._sc)))


# ── Geometry helper ───────────────────────────────────────────────────────────

def _seg_circle(
    x1: float, y1: float, x2: float, y2: float,
    cx: float, cy: float, r: float,
) -> bool:
    """True if segment (x1,y1)→(x2,y2) intersects circle (cx,cy,r)."""
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - cx, y1 - cy
    a = dx * dx + dy * dy
    if a == 0:
        return math.hypot(fx, fy) <= r
    b    = 2 * (fx * dx + fy * dy)
    c    = fx * fx + fy * fy - r * r
    disc = b * b - 4 * a * c
    if disc < 0:
        return False
    sq   = math.sqrt(disc)
    t1   = (-b - sq) / (2 * a)
    t2   = (-b + sq) / (2 * a)
    return (0 <= t1 <= 1) or (0 <= t2 <= 1) or (t1 < 0 < t2)
