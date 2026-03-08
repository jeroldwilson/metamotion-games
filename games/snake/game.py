"""
snake/game.py — Classic Snake game (pygame)

Controls
────────
  Sensor mode  : Tilt wrist LEFT/RIGHT to turn left/right.
                 Tilt wrist FORWARD/BACK to turn up/down.
  Keyboard mode: Arrow keys for all 4 directions.
  Both modes   : ESC = pause / back to menu, R = restart, H = home.

Design decisions
────────────────
- 800×600 grid with 20px cells (40 columns × 30 rows).
- Snake speed increases slightly each time food is eaten.
- Sensor gesture uses edge-triggered debounce: one tilt crossing = one turn.
- Keyboard UP/DOWN are read directly from pygame.key.get_pressed() since
  KeyboardFallback only exposes left/right tilt.
"""

import random
import sys
import time
from collections import deque
from enum import Enum
from typing import Optional, Tuple, TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from shared.gesture import GestureState

# ── Constants ─────────────────────────────────────────────────────────────────
W, H           = 800, 600
FPS            = 60
CELL           = 20
COLS           = W // CELL   # 40
ROWS           = H // CELL   # 30
MOVE_INTERVAL  = 0.12        # seconds per grid step (base)
MIN_INTERVAL   = 0.05        # fastest possible
SPEED_FACTOR   = 0.005       # shaved off interval per food eaten
TILT_THRESH    = 0.35        # gesture threshold to register a direction change

# Accessible mode
MOVE_INTERVAL_ACCESSIBLE = 0.50   # slower, fixed speed (no acceleration) — half of standard
ACCESSIBLE_GESTURE_CD    = 0.8    # seconds between gesture triggers per axis

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (15,  15,  25)
GRID_CLR    = (38,  38,  60)
SNAKE_CLR   = (100, 240, 120)
SNAKE_HEAD  = (185, 255, 195)
FOOD_CLR    = (255, 100, 100)  # fallback (unused when fruits are drawn)
TEXT_CLR    = (255, 255, 255)
DIM_CLR     = (165, 165, 180)

FRUITS      = ["apple", "orange", "cherry", "strawberry", "grapes", "watermelon", "lemon"]


# ── Direction enum ────────────────────────────────────────────────────────────

class Dir(Enum):
    UP    = (0, -1)
    DOWN  = (0,  1)
    LEFT  = (-1, 0)
    RIGHT = (1,  0)

    def opposite(self) -> "Dir":
        return {
            Dir.UP:    Dir.DOWN,
            Dir.DOWN:  Dir.UP,
            Dir.LEFT:  Dir.RIGHT,
            Dir.RIGHT: Dir.LEFT,
        }[self]


# ── Game class ────────────────────────────────────────────────────────────────

class SnakeGame:
    """
    Classic Snake game using the same gesture/keyboard infrastructure.

    Accepts an existing pygame display surface and clock (owned by main.py).
    Returns "home" when the player exits to the selection screen.
    """

    def __init__(
        self,
        screen: pygame.Surface,
        clock: pygame.time.Clock,
        debug: bool = False,
        mode: str = "standard",
        audio=None,
    ):
        self._clock  = clock
        self._debug  = debug
        self._mode   = mode
        self._audio  = audio
        self._gesture_src = None
        self._init_layout(screen)
        self._reset()

    def _init_layout(self, screen: pygame.Surface) -> None:
        """Compute all screen-size-dependent layout variables."""
        self._screen = screen
        sw, sh = screen.get_size()
        self._W    = sw
        self._H    = sh
        self._is_fullscreen = not (sw == 800 and sh == 600)
        # Largest integer cell that fits the 40×30 grid on screen
        self._cell = min(sw // COLS, sh // ROWS)
        # Offsets to centre the grid (letterbox if aspect differs)
        self._ox   = (sw - self._cell * COLS) // 2
        self._oy   = (sh - self._cell * ROWS) // 2
        # Fruit-drawing scale relative to the base 20-px cell
        self._fsc  = self._cell / 20.0

        sc = self._fsc
        self._font_lg = pygame.font.SysFont("monospace", max(24, int(48 * sc)), bold=True)
        self._font_md = pygame.font.SysFont("monospace", max(12, int(24 * sc)))
        self._font_sm = pygame.font.SysFont("monospace", max( 8, int(14 * sc)))

    def _toggle_fullscreen(self) -> None:
        """Switch between fullscreen (native res) and windowed (800×600)."""
        self._is_fullscreen = not self._is_fullscreen
        if self._is_fullscreen:
            new_screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            new_screen = pygame.display.set_mode((800, 600))
        # Game state is in grid coords — safe to keep; just reinit layout.
        self._init_layout(new_screen)

    # ── Public entry point ─────────────────────────────────────────────────────

    def run(self, gesture_src) -> str:
        """Run the game loop. Returns 'home' when player exits to menu."""
        self._gesture_src = gesture_src
        self._reset()
        pygame.mouse.set_visible(True)
        if self._audio:
            self._audio.start_background()
        while True:
            dt = self._clock.tick(FPS) / 1000.0
            result = self._handle_events()
            if result:
                if self._audio:
                    self._audio.stop_background()
                return result
            if not self._paused and not self._game_over:
                self._update(dt)
            self._draw()
            pygame.display.flip()

    # ── State management ───────────────────────────────────────────────────────

    def _reset(self) -> None:
        # Start in the middle, moving right, 3 cells long
        cx, cy = COLS // 2, ROWS // 2
        self._body: deque = deque([
            (cx, cy), (cx - 1, cy), (cx - 2, cy)
        ])
        self._direction   = Dir.RIGHT
        self._next_dir    = Dir.RIGHT
        self._score       = 0
        self._game_over   = False
        self._paused      = False
        self._move_timer  = 0.0
        self._move_interval = (
            MOVE_INTERVAL_ACCESSIBLE if self._mode == "accessible" else MOVE_INTERVAL
        )
        self._food        = self._spawn_food()
        self._fruit_type  = random.choice(FRUITS)

        # Gesture debounce state
        self._last_tilt_x: int = 0   # -1, 0, +1
        self._last_tilt_y: int = 0

        # Accessible mode: per-axis gesture cooldown timestamps
        self._cd_x_until: float = 0.0
        self._cd_y_until: float = 0.0

    def _spawn_food(self) -> Tuple[int, int]:
        """Randomly place food on an empty cell."""
        occupied = set(self._body) if hasattr(self, '_body') else set()
        while True:
            pos = (random.randint(0, COLS - 1), random.randint(0, ROWS - 1))
            if pos not in occupied:
                return pos

    # ── Direction input ────────────────────────────────────────────────────────

    def _apply_direction(self, new_dir: Dir) -> None:
        """Buffer a direction change; reject 180° reversals."""
        if new_dir != self._direction.opposite():
            self._next_dir = new_dir

    def _read_gesture(self) -> None:
        """Map GestureState tilt to a direction change (edge-triggered)."""
        if self._gesture_src is None:
            return
        gs = self._gesture_src.get_state()
        if not gs.calibrated:
            return

        tilt_x = 0
        if gs.paddle_velocity < -TILT_THRESH:
            tilt_x = -1
        elif gs.paddle_velocity > TILT_THRESH:
            tilt_x = 1

        tilt_y = 0
        if gs.tilt_y < -TILT_THRESH:
            tilt_y = -1   # forward tilt → UP
        elif gs.tilt_y > TILT_THRESH:
            tilt_y = 1    # back tilt → DOWN

        if self._mode == "accessible":
            self._read_gesture_accessible(tilt_x, tilt_y)
        else:
            self._read_gesture_standard(tilt_x, tilt_y)

    def _read_gesture_standard(self, tilt_x: int, tilt_y: int) -> None:
        if tilt_x != 0 and tilt_x != self._last_tilt_x:
            self._apply_direction(Dir.LEFT if tilt_x < 0 else Dir.RIGHT)
        self._last_tilt_x = tilt_x

        if tilt_y != 0 and tilt_y != self._last_tilt_y:
            self._apply_direction(Dir.UP if tilt_y < 0 else Dir.DOWN)
        self._last_tilt_y = tilt_y

    def _read_gesture_accessible(self, tilt_x: int, tilt_y: int) -> None:
        """Intent-assist: any gesture triggers the optimal turn toward food."""
        now = time.monotonic()

        if tilt_x != 0 and tilt_x != self._last_tilt_x:
            if now >= self._cd_x_until:
                self._apply_direction(self._best_dir_toward_food())
                self._cd_x_until = now + ACCESSIBLE_GESTURE_CD
        self._last_tilt_x = tilt_x

        if tilt_y != 0 and tilt_y != self._last_tilt_y:
            if now >= self._cd_y_until:
                self._apply_direction(self._best_dir_toward_food())
                self._cd_y_until = now + ACCESSIBLE_GESTURE_CD
        self._last_tilt_y = tilt_y

    def _best_dir_toward_food(self) -> "Dir":
        """Return the optimal next direction toward food (wrap-aware, no 180°)."""
        hx, hy = self._body[0]
        fx, fy = self._food

        # Shortest delta considering toroidal wrap
        dx = fx - hx
        if abs(dx - COLS) < abs(dx): dx -= COLS
        elif abs(dx + COLS) < abs(dx): dx += COLS

        dy = fy - hy
        if abs(dy - ROWS) < abs(dy): dy -= ROWS
        elif abs(dy + ROWS) < abs(dy): dy += ROWS

        h_dir = Dir.RIGHT if dx > 0 else Dir.LEFT
        v_dir = Dir.DOWN  if dy > 0 else Dir.UP

        # Prefer the axis with greater absolute delta; both non-zero needed for fallback
        if abs(dx) >= abs(dy):
            candidates = [h_dir] + ([v_dir] if dy != 0 else [])
        else:
            candidates = [v_dir] + ([h_dir] if dx != 0 else [])

        opp = self._direction.opposite()
        for d in candidates:
            if d != opp:
                return d
        return self._direction  # already aligned; keep going

    # ── Event handling ────────────────────────────────────────────────────────

    def _handle_events(self) -> Optional[str]:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
            elif event.type == pygame.KEYDOWN:
                result = self._on_key(event.key)
                if result:
                    return result

        # Handle held arrow keys for keyboard direction control
        keys = pygame.key.get_pressed()
        if keys[pygame.K_UP]:
            self._apply_direction(Dir.UP)
        elif keys[pygame.K_DOWN]:
            self._apply_direction(Dir.DOWN)
        elif keys[pygame.K_LEFT]:
            self._apply_direction(Dir.LEFT)
        elif keys[pygame.K_RIGHT]:
            self._apply_direction(Dir.RIGHT)

        # Feed left/right to KeyboardFallback so paddle_velocity is set
        src = self._gesture_src
        if hasattr(src, "press_left"):
            if keys[pygame.K_LEFT]:
                src.press_left()
            else:
                src.release_left()
            if keys[pygame.K_RIGHT]:
                src.press_right()
            else:
                src.release_right()

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
        elif key == pygame.K_h and self._game_over:
            return "home"
        elif key == pygame.K_f:
            self._toggle_fullscreen()
        return None

    # ── Update ────────────────────────────────────────────────────────────────

    def _update(self, dt: float) -> None:
        self._read_gesture()

        self._move_timer += dt
        if self._move_timer >= self._move_interval:
            self._move_timer -= self._move_interval
            self._step()

    def _step(self) -> None:
        head = self._body[0]
        dx, dy = self._next_dir.value
        new_head = (head[0] + dx, head[1] + dy)
        self._direction = self._next_dir

        if self._mode == "accessible":
            # Wrap around walls (toroidal grid)
            new_head = (new_head[0] % COLS, new_head[1] % ROWS)
            # No self-collision death — just pass through
        else:
            # Wall collision
            if not (0 <= new_head[0] < COLS and 0 <= new_head[1] < ROWS):
                self._game_over = True
                return
            # Self collision
            if new_head in self._body:
                self._game_over = True
                return

        self._body.appendleft(new_head)

        # Food eaten
        if new_head == self._food:
            self._score += 10
            self._food = self._spawn_food()
            self._fruit_type = random.choice(FRUITS)
            if self._audio:
                self._audio.play_collect()
            # Accessible mode: no speed increase
            if self._mode != "accessible":
                self._move_interval = max(MIN_INTERVAL, self._move_interval - SPEED_FACTOR)
        else:
            self._body.pop()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self._screen.fill(BG)
        self._draw_grid()
        self._draw_food()
        self._draw_snake()
        self._draw_hud()
        if self._debug:
            self._draw_debug()
        if self._paused:
            self._draw_overlay("PAUSED", "ESC to resume   X to menu")
        if self._game_over:
            self._draw_overlay(
                "GAME OVER",
                f"Score: {self._score}   R=restart   ESC/H=menu"
            )

    def _draw_grid(self) -> None:
        c = self._cell
        for col in range(COLS + 1):
            x = self._ox + col * c
            pygame.draw.line(self._screen, GRID_CLR,
                             (x, self._oy), (x, self._oy + ROWS * c))
        for row in range(ROWS + 1):
            y = self._oy + row * c
            pygame.draw.line(self._screen, GRID_CLR,
                             (self._ox, y), (self._ox + COLS * c, y))

    def _draw_snake(self) -> None:
        c  = self._cell
        br = max(2, c // 5)
        for i, (cx, cy) in enumerate(self._body):
            rect = pygame.Rect(
                self._ox + cx * c + 1,
                self._oy + cy * c + 1,
                c - 2, c - 2,
            )
            color = SNAKE_HEAD if i == 0 else SNAKE_CLR
            pygame.draw.rect(self._screen, color, rect, border_radius=br)

    def _draw_food(self) -> None:
        fx, fy = self._food
        cx = self._ox + fx * self._cell + self._cell // 2
        cy = self._oy + fy * self._cell + self._cell // 2
        s  = self._screen
        sc = self._fsc
        draw = {
            "apple":       self._draw_apple,
            "orange":      self._draw_orange,
            "cherry":      self._draw_cherry,
            "strawberry":  self._draw_strawberry,
            "grapes":      self._draw_grapes,
            "watermelon":  self._draw_watermelon,
            "lemon":       self._draw_lemon,
        }
        draw.get(self._fruit_type, self._draw_apple)(s, cx, cy, sc)

    @staticmethod
    def _draw_apple(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        r = max(1, p(7))
        pygame.draw.circle(s, (210, 40, 40),  (cx, cy + max(1, p(1))), r)
        pygame.draw.circle(s, (240, 90, 90),  (cx - max(1, p(2)), cy - max(1, p(1))), max(1, p(2)))
        pygame.draw.line(s, (100, 55, 10),
                         (cx, cy - max(1, p(7))), (cx + max(1, p(1)), cy - p(10)), max(1, p(2)))
        pygame.draw.ellipse(s, (55, 175, 50),
                            pygame.Rect(cx + max(1, p(1)), cy - p(11), max(2, p(6)), max(1, p(3))))

    @staticmethod
    def _draw_orange(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        r = max(1, p(7))
        pygame.draw.circle(s, (255, 145, 30), (cx, cy + max(1, p(1))), r)
        pygame.draw.circle(s, (255, 200, 80), (cx - max(1, p(2)), cy - max(1, p(1))), max(1, p(2)))
        pygame.draw.circle(s, (200, 110, 20), (cx, cy + max(1, p(4))), max(1, p(2)))
        pygame.draw.line(s, (100, 55, 10),
                         (cx, cy - max(1, p(7))), (cx, cy - p(10)), max(1, p(2)))
        pygame.draw.ellipse(s, (55, 175, 50),
                            pygame.Rect(cx + max(1, p(1)), cy - p(11), max(2, p(6)), max(1, p(3))))

    @staticmethod
    def _draw_cherry(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        r = max(2, p(5))
        pygame.draw.circle(s, (175, 20, 45), (cx - max(1, p(3)), cy + max(1, p(2))), r)
        pygame.draw.circle(s, (175, 20, 45), (cx + max(1, p(3)), cy + max(1, p(2))), r)
        pygame.draw.circle(s, (220, 80, 80), (cx - max(1, p(4)), cy + max(1, p(1))), max(1, p(1)))
        pygame.draw.circle(s, (220, 80, 80), (cx + max(1, p(2)), cy + max(1, p(1))), max(1, p(1)))
        sw = max(1, p(2))
        pygame.draw.line(s, (55, 155, 35),
                         (cx - max(1, p(2)), cy - max(1, p(2))), (cx, cy - max(1, p(7))), sw)
        pygame.draw.line(s, (55, 155, 35),
                         (cx + max(1, p(2)), cy - max(1, p(2))), (cx, cy - max(1, p(7))), sw)
        pygame.draw.ellipse(s, (55, 175, 50),
                            pygame.Rect(cx - max(1, p(1)), cy - p(10), max(2, p(6)), max(1, p(3))))

    @staticmethod
    def _draw_strawberry(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        pygame.draw.ellipse(s, (220, 35, 60),
                            pygame.Rect(cx - max(1, p(6)), cy - max(1, p(4)), max(2, p(12)), max(2, p(12))))
        pygame.draw.ellipse(s, (250, 80, 100),
                            pygame.Rect(cx - max(1, p(4)), cy - max(1, p(3)), max(2, p(4)), max(2, p(4))))
        seed_r = max(1, p(1))
        for dx, dy in [(-2, -1), (2, -1), (0, 2), (-3, 2), (3, 2), (0, -4)]:
            pygame.draw.circle(s, (255, 240, 100), (cx + p(dx), cy + p(dy)), seed_r)
        sw = max(1, p(2))
        for dx in [-3, 0, 3]:
            pygame.draw.line(s, (55, 160, 50),
                             (cx + p(dx), cy - max(1, p(4))),
                             (cx + p(dx // 2), cy - max(1, p(8))), sw)

    @staticmethod
    def _draw_grapes(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p  = lambda v: int(v * sc)
        gr = max(1, p(3))
        for gx, gy in [(-4, -4), (0, -4), (4, -4), (-2, 0), (2, 0), (0, 4)]:
            pygame.draw.circle(s, (130, 40, 195), (cx + p(gx), cy + p(gy)), gr)
            pygame.draw.circle(s, (170, 90, 220), (cx + p(gx) - 1, cy + p(gy) - 1), max(1, p(1)))
        pygame.draw.line(s, (100, 55, 10),
                         (cx, cy - max(1, p(7))), (cx, cy - max(1, p(9))), max(1, p(1)))
        pygame.draw.ellipse(s, (55, 175, 50),
                            pygame.Rect(cx + max(1, p(1)), cy - p(10), max(2, p(5)), max(1, p(3))))

    @staticmethod
    def _draw_watermelon(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        pygame.draw.circle(s, (45, 155, 45),  (cx, cy), max(2, p(8)))
        pygame.draw.circle(s, (215, 50, 55),  (cx, cy), max(1, p(6)))
        for sx, sy in [(-2, -1), (2, -1), (0, 2), (-1, -4)]:
            pygame.draw.ellipse(s, (25, 20, 20),
                                pygame.Rect(cx + p(sx) - 1, cy + p(sy) - 1, max(1, p(2)), max(1, p(3))))
        pygame.draw.circle(s, (240, 100, 100), (cx - max(1, p(2)), cy - max(1, p(2))), max(1, p(1)))

    @staticmethod
    def _draw_lemon(s: pygame.Surface, cx: int, cy: int, sc: float = 1.0) -> None:
        p = lambda v: int(v * sc)
        pygame.draw.ellipse(s, (250, 225, 40),
                            pygame.Rect(cx - max(1, p(7)), cy - max(1, p(5)), max(2, p(14)), max(2, p(10))))
        pygame.draw.ellipse(s, (255, 255, 130),
                            pygame.Rect(cx - max(1, p(4)), cy - max(1, p(4)), max(2, p(5)), max(2, p(4))))
        pygame.draw.circle(s, (215, 190, 30), (cx - max(1, p(7)), cy), max(1, p(2)))
        pygame.draw.circle(s, (215, 190, 30), (cx + max(1, p(7)), cy), max(1, p(2)))

    def _draw_hud(self) -> None:
        sc = self._fsc
        score_surf = self._font_md.render(f"Score: {self._score}", True, TEXT_CLR)
        self._screen.blit(score_surf, (self._ox + max(4, int(12 * sc)), max(4, int(8 * sc))))
        speed_label = self._font_sm.render(
            f"Speed: {1.0 / self._move_interval:.1f} steps/s", True, DIM_CLR
        )
        self._screen.blit(speed_label, speed_label.get_rect(
            center=(self._W // 2, max(4, int(10 * sc)))
        ))
        len_label = self._font_sm.render(f"Length: {len(self._body)}", True, DIM_CLR)
        self._screen.blit(len_label, len_label.get_rect(
            right=self._W - max(4, int(10 * sc)),
            top=max(4, int(8 * sc)),
        ))
        if self._mode == "accessible":
            badge = self._font_sm.render("ASTRA", True, (80, 220, 100))
            self._screen.blit(badge, badge.get_rect(
                right=self._W - max(4, int(10 * sc)),
                bottom=self._H - max(4, int(8 * sc)),
            ))

    def _draw_debug(self) -> None:
        if self._gesture_src is None:
            return
        gs = self._gesture_src.get_state()
        lines = [
            f"paddle_vel : {gs.paddle_velocity:+.3f}",
            f"tilt_y     : {gs.tilt_y:+.3f}",
            f"calibrated : {gs.calibrated}",
            f"direction  : {self._direction.name}",
            f"next_dir   : {self._next_dir.name}",
        ]
        sc  = self._fsc
        row = max(10, int(16 * sc))
        y0  = self._H - max(60, int(90 * sc))
        for i, line in enumerate(lines):
            surf = self._font_sm.render(line, True, (180, 220, 180))
            self._screen.blit(surf, (10, y0 + i * row))

    def _draw_overlay(self, title: str, subtitle: str = "") -> None:
        dim = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self._screen.blit(dim, (0, 0))
        sc = self._fsc
        t = self._font_lg.render(title, True, TEXT_CLR)
        self._screen.blit(t, t.get_rect(center=(self._W // 2, self._H // 2 - max(15, int(30 * sc)))))
        if subtitle:
            s = self._font_md.render(subtitle, True, DIM_CLR)
            self._screen.blit(s, s.get_rect(center=(self._W // 2, self._H // 2 + max(10, int(20 * sc)))))
