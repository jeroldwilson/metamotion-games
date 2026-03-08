"""
game.py — Bricks & Paddle game (pygame)

Controls
────────
  Sensor mode  : Tilt wrist left/right to move paddle.
                 Flick wrist up to launch ball.
  Keyboard mode: ← / → arrow keys move the paddle.
                 SPACE launches the ball.
  Both modes   : ESC = pause / back to menu (when game over), R = restart.

Design decisions
────────────────
- Resolution independent: internal render size 800×600, scaled to window.
- Paddle speed is proportional to GestureState.paddle_velocity [-1 … 1].
- Ball spin (GestureState.spin) adds a lateral velocity nudge on each
  paddle bounce, letting skilled players aim with wrist rotation.
- Bricks have 1–3 hit points mapped to visible colour.
- Power-ups drop occasionally: WIDE (wider paddle), MULTI (extra ball),
  FAST (speed boost/penalty).
"""

import math
import random
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    from shared.gesture import GestureState

# ── Dimensions & constants ────────────────────────────────────────────────────
W, H          = 800, 600
FPS           = 60
PADDLE_W      = 100
PADDLE_H      = 14
PADDLE_Y      = H - 50
PADDLE_SPEED  = 420        # pixels/sec at full velocity

BALL_R        = 9
BALL_SPEED    = 340        # pixels/sec initial

BRICK_COLS    = 12
BRICK_ROWS    = 6
BRICK_W       = W // BRICK_COLS
BRICK_H       = 22
BRICK_TOP     = 60
BRICK_GAP     = 2

LIVES_START   = 3
POWERUP_PROB  = 0.15       # chance a destroyed brick drops a power-up

# Accessible mode
PADDLE_W_ACCESSIBLE    = 150    # 1.5× normal paddle width
BALL_SPEED_ACCESSIBLE  = 240    # pixels/sec (vs 340 standard)
BOUNCE_MSG_DURATION    = 2.0    # seconds to show "Nice try!" message
ACCESSIBLE_INTENT_THRESH   = 0.20   # any tilt above this triggers intent tracking

# ── Colours ───────────────────────────────────────────────────────────────────
BG           = (15,  15,  25)
PADDLE_CLR   = (120, 210, 255)
BALL_CLR     = (255, 255, 255)
TEXT_CLR     = (255, 255, 255)
DIM_CLR      = (165, 165, 180)
POWERUP_CLRS = {
    "WIDE":  (120, 255, 140),
    "MULTI": (255, 235,  80),
    "FAST":  (255, 100, 100),
}
BRICK_PALETTE = [
    (255,  80,  80),   # bright red
    (255, 185,  50),   # bright amber
    ( 70, 230, 115),   # bright green
    ( 80, 165, 255),   # bright blue
    (210,  80, 255),   # bright violet
    (255, 255,  75),   # bright yellow
]

def _brick_colour(row: int, hp: int) -> Tuple[int, int, int]:
    base = BRICK_PALETTE[row % len(BRICK_PALETTE)]
    factor = 0.55 + 0.45 * (hp / 3)
    return tuple(max(0, min(255, int(c * factor))) for c in base)  # type: ignore


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Brick:
    rect: pygame.Rect
    hp:   int
    row:  int

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def hit(self) -> int:
        """Reduce HP by 1; return points scored."""
        self.hp -= 1
        return (3 - self.hp) * 10 + 10

    def draw(self, surf: pygame.Surface) -> None:
        if not self.alive:
            return
        colour = _brick_colour(self.row, self.hp)
        pygame.draw.rect(surf, colour, self.rect, border_radius=3)
        pygame.draw.rect(surf, (0, 0, 0), self.rect, 1, border_radius=3)


@dataclass
class Ball:
    x: float
    y: float
    vx: float
    vy: float
    r: int = BALL_R        # radius — set by BricksGame from scale
    active: bool = True    # False = waiting to be launched from paddle

    def rect(self) -> pygame.Rect:
        return pygame.Rect(int(self.x) - self.r, int(self.y) - self.r,
                           self.r * 2, self.r * 2)

    def draw(self, surf: pygame.Surface) -> None:
        pygame.draw.circle(surf, BALL_CLR, (int(self.x), int(self.y)), self.r)
        # Subtle glow
        pygame.draw.circle(surf, (200, 230, 255), (int(self.x), int(self.y)), self.r, 2)


@dataclass
class PowerUp:
    rect:  pygame.Rect
    kind:  str
    speed: float = 130.0
    alive: bool  = True

    def update(self, dt: float, h: int = H) -> None:
        self.rect.y += int(self.speed * dt)
        if self.rect.y > h:
            self.alive = False

    def draw(self, surf: pygame.Surface, font_size: int = 11) -> None:
        colour = POWERUP_CLRS.get(self.kind, (200, 200, 200))
        pygame.draw.rect(surf, colour, self.rect, border_radius=4)
        font = pygame.font.SysFont("monospace", font_size, bold=True)
        label = font.render(self.kind, True, (0, 0, 0))
        surf.blit(label, label.get_rect(center=self.rect.center))


# ── Game class ────────────────────────────────────────────────────────────────

class BricksGame:
    """
    Self-contained pygame game loop.

    Accepts an existing pygame display surface and clock (owned by main.py).
    Call `run(gesture_source)` where `gesture_source` has a
    `get_state() -> GestureState` method (either GestureInterpreter
    or KeyboardFallback).

    Returns "home" when the player exits back to the selection screen.
    """

    def __init__(
        self,
        screen: pygame.Surface,
        clock: pygame.time.Clock,
        debug: bool = False,
        mode: str = "standard",
        audio=None,
    ):
        self._clock         = clock
        self._mode          = mode
        self._audio         = audio
        self._gesture_source = None
        self._debug_hud     = debug
        self._init_layout(screen)
        self._reset()

    def _init_layout(self, screen: pygame.Surface) -> None:
        """Compute all screen-size-dependent layout variables."""
        self._screen = screen
        self._W, self._H = screen.get_size()
        self._is_fullscreen = not (self._W == 800 and self._H == 600)
        sc = min(self._W / 800, self._H / 600)
        self._scale       = sc

        self._paddle_h    = max(6,  int(PADDLE_H * sc))
        self._paddle_y    = self._H - max(30, int(50 * sc))
        self._paddle_spd  = PADDLE_SPEED * sc
        self._ball_r      = max(4,  int(BALL_R * sc))
        self._ball_spd    = BALL_SPEED * sc
        self._brick_w     = self._W // BRICK_COLS
        self._brick_h     = max(8,  int(BRICK_H * sc))
        self._brick_top   = max(20, int(BRICK_TOP * sc))
        self._brick_gap   = max(1,  int(BRICK_GAP * sc))
        self._pu_font_sz  = max(8,  int(11 * sc))

        self._paddle_w_std = max(40, int(PADDLE_W * sc))
        self._paddle_w_acc = max(60, int(PADDLE_W_ACCESSIBLE * sc))

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
        # Ball/paddle positions are in pixel space — simplest to reset the game.
        self._init_layout(new_screen)
        self._reset()

    # ── Public entry point ─────────────────────────────────────────────────────

    def run(self, gesture_source) -> str:
        """Run the game loop. Returns 'home' when player exits to menu."""
        self._gesture_source = gesture_source
        self._reset()
        pygame.mouse.set_visible(False)
        if self._audio:
            self._audio.start_background()
        while True:
            dt = self._clock.tick(FPS) / 1000.0
            result = self._handle_events()
            if result:
                if self._audio:
                    self._audio.stop_background()
                return result
            if not self._paused and not self._game_over and not self._you_win:
                self._update(dt)
            self._draw()
            pygame.display.flip()

    # ── State management ───────────────────────────────────────────────────────

    def _reset(self) -> None:
        self._score     = 0
        self._lives     = LIVES_START
        self._level     = 1
        self._paused    = False
        self._game_over = False
        self._you_win   = False

        base_w = self._paddle_w_acc if self._mode == "accessible" else self._paddle_w_std
        self._paddle    = pygame.Rect(
            self._W // 2 - base_w // 2, self._paddle_y, base_w, self._paddle_h
        )
        self._paddle_w_timer = 0.0   # seconds remaining for WIDE power-up
        self._prev_mouse_x   = pygame.mouse.get_pos()[0]
        self._log_tick       = 0
        self._was_left       = False
        self._was_right      = False
        self._bounce_msg_timer: float = 0.0   # accessible: "Nice try!" display
        self._acc_ball_was_falling: bool  = False  # accessible: was ball falling last frame
        self._acc_hit_frac: float         = 0.5    # accessible: paddle fraction to aim ball at
        self._acc_drift_dir: float        = 1.0    # accessible: current random drift direction
        self._acc_drift_timer: float      = 0.0    # accessible: time until next drift flip

        self._balls: List[Ball]    = []
        self._powerups: List[PowerUp] = []
        self._bricks: List[Brick]  = []

        self._spawn_ball()
        self._build_bricks(self._level)

    def _next_level(self) -> None:
        self._level += 1
        self._powerups.clear()
        self._balls.clear()
        self._spawn_ball()
        self._build_bricks(self._level)

    def _spawn_ball(self) -> None:
        """Create a ball sitting on the paddle, not yet launched."""
        cx = self._paddle.centerx
        self._balls.append(Ball(
            x=cx, y=self._paddle_y - self._ball_r - 1,
            vx=0, vy=0, r=self._ball_r, active=False
        ))

    def _build_bricks(self, level: int) -> None:
        self._bricks.clear()
        # Higher levels → more 2/3-hp bricks
        for row in range(BRICK_ROWS):
            for col in range(BRICK_COLS):
                x = col * self._brick_w + self._brick_gap
                y = self._brick_top + row * (self._brick_h + self._brick_gap)
                hp_roll = random.random()
                if level >= 3 and hp_roll < 0.25:
                    hp = 3
                elif level >= 2 and hp_roll < 0.40:
                    hp = 2
                else:
                    hp = 1
                self._bricks.append(Brick(
                    rect=pygame.Rect(x, y, self._brick_w - self._brick_gap, self._brick_h),
                    hp=hp, row=row
                ))

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

        # Propagate keyboard to KeyboardFallback if that's what's attached
        keys = pygame.key.get_pressed()
        src  = self._gesture_source
        if hasattr(src, "press_left"):
            left_now  = bool(keys[pygame.K_LEFT])
            right_now = bool(keys[pygame.K_RIGHT])
            if left_now != self._was_left:
                self._was_left = left_now
            if right_now != self._was_right:
                self._was_right = right_now
            if left_now:  src.press_left()
            else:         src.release_left()
            if right_now: src.press_right()
            else:         src.release_right()

        return None

    def _on_key(self, key: int) -> Optional[str]:
        if key == pygame.K_ESCAPE:
            if self._game_over or self._you_win:
                return "home"
            else:
                self._paused = not self._paused
        elif key == pygame.K_x and self._paused:
            return "home"
        elif key == pygame.K_r and (self._game_over or self._you_win):
            self._reset()
        elif key == pygame.K_d:
            self._debug_hud = not self._debug_hud
        elif key == pygame.K_SPACE:
            self._launch_all_inactive()
        elif key == pygame.K_f:
            self._toggle_fullscreen()
        # Keyboard fallback: SPACE is also wired through trigger_launch
        src = self._gesture_source
        if key == pygame.K_SPACE and hasattr(src, "trigger_launch"):
            src.trigger_launch()
        return None

    # ── Update ────────────────────────────────────────────────────────────────

    def _update(self, dt: float) -> None:
        gs = self._gesture_source.get_state() if self._gesture_source else None

        self._update_paddle(dt, gs)
        self._update_balls(dt, gs)
        self._update_powerups(dt)
        self._check_win()
        if self._bounce_msg_timer > 0:
            self._bounce_msg_timer = max(0.0, self._bounce_msg_timer - dt)

    def _update_paddle(self, dt: float, gs) -> None:
        # Apply WIDE power-up timer
        base_w = self._paddle_w_acc if self._mode == "accessible" else self._paddle_w_std
        if self._paddle_w_timer > 0:
            self._paddle_w_timer -= dt
            target_w = base_w * 2
        else:
            target_w = base_w
        # Smoothly resize
        self._paddle.width += int((target_w - self._paddle.width) * 0.15)

        # Mouse control: if the cursor moved this frame, follow it directly
        mx = pygame.mouse.get_pos()[0]
        if mx != self._prev_mouse_x:
            self._paddle.centerx = mx
            self._prev_mouse_x = mx
        elif self._mode == "accessible":
            self._update_paddle_intent(dt, gs)
        else:
            velocity = gs.paddle_velocity if gs else 0.0
            dx = velocity * self._paddle_spd * dt
            self._paddle.x += int(dx)
        self._paddle.x = max(0, min(self._W - self._paddle.width, self._paddle.x))

        self._log_tick += 1

        # Launch gesture
        if gs and gs.launch:
            self._launch_all_inactive()

    # ── Accessible intent-assist helpers ──────────────────────────────────────

    def _pick_target_ball(self) -> Optional[Ball]:
        """Return the most urgent active ball (downward-moving, closest to paddle)."""
        candidates = [b for b in self._balls if b.active]
        if not candidates:
            return None
        downward = [b for b in candidates if b.vy > 0]
        pool = downward if downward else candidates
        return max(pool, key=lambda b: b.y)

    def _update_paddle_intent(self, dt: float, gs) -> None:
        """Accessible: drift randomly when ball rises; aim at random paddle spot when falling."""
        if gs is None:
            return
        has_gesture = (
            abs(gs.paddle_velocity) > ACCESSIBLE_INTENT_THRESH
            or abs(gs.tilt_y) > ACCESSIBLE_INTENT_THRESH
            or gs.launch
        )
        if not has_gesture:
            return

        ball = self._pick_target_ball()
        if ball is None:
            return

        ball_falling = ball.vy > 0

        # Transition rising→falling: pick a fresh random non-center hit position
        if ball_falling and not self._acc_ball_was_falling:
            self._acc_hit_frac = random.choice([
                random.uniform(0.15, 0.40),   # left-of-center: ball bounces right
                random.uniform(0.60, 0.85),   # right-of-center: ball bounces left
            ])
        self._acc_ball_was_falling = ball_falling

        if not ball_falling:
            # Ball rising: drift paddle randomly to mimic user trying to position
            self._acc_drift_timer -= dt
            if self._acc_drift_timer <= 0:
                self._acc_drift_dir = random.choice([-1.0, 1.0])
                self._acc_drift_timer = random.uniform(0.3, 0.7)
            self._paddle.x += int(self._acc_drift_dir * self._paddle_spd * dt)
        else:
            # Ball falling: smoothly move so ball lands at acc_hit_frac of paddle width
            target_left = int(ball.x - self._acc_hit_frac * self._paddle.width)
            dx = target_left - self._paddle.x
            if abs(dx) >= 1:
                step = min(abs(dx), max(1, int(self._paddle_spd * dt)))
                self._paddle.x += int(math.copysign(step, dx))

    def _launch_all_inactive(self) -> None:
        for ball in self._balls:
            if not ball.active:
                angle = random.uniform(-65, -115)   # upward, slightly randomised
                rad   = math.radians(angle)
                speed = self._ball_spd + (self._level - 1) * int(20 * self._scale)
                if self._mode == "accessible":
                    speed = min(speed, BALL_SPEED_ACCESSIBLE * self._scale)
                ball.vx = speed * math.cos(rad)
                ball.vy = speed * math.sin(rad)
                ball.active = True

    def _update_balls(self, dt: float, gs) -> None:
        spin = gs.spin if gs else 0.0
        dead_balls = []

        for ball in self._balls:
            if not ball.active:
                # Ride along the paddle
                ball.x = self._paddle.centerx
                ball.y = self._paddle_y - self._ball_r - 1
                continue

            ball.x += ball.vx * dt
            ball.y += ball.vy * dt

            # Wall collisions
            if ball.x - ball.r < 0:
                ball.x  = ball.r
                ball.vx = abs(ball.vx)
            elif ball.x + ball.r > self._W:
                ball.x  = self._W - ball.r
                ball.vx = -abs(ball.vx)

            if ball.y - ball.r < 0:
                ball.y  = ball.r
                ball.vy = abs(ball.vy)

            # Bottom — lose ball (standard) or bounce (accessible)
            if ball.y + ball.r > self._H:
                if self._mode == "accessible":
                    ball.y  = self._H - ball.r
                    ball.vy = -abs(ball.vy)
                    self._bounce_msg_timer = BOUNCE_MSG_DURATION
                else:
                    dead_balls.append(ball)
                continue

            # Paddle collision
            if ball.rect().colliderect(self._paddle) and ball.vy > 0:
                ball.y  = self._paddle.top - ball.r
                # Reflect with angle based on hit position
                rel = (ball.x - self._paddle.left) / self._paddle.width  # 0…1
                angle = 150 + rel * (-120)   # 150° left edge → 30° right edge
                speed = math.hypot(ball.vx, ball.vy)
                speed = max(speed, self._ball_spd)  # never slow down
                rad   = math.radians(angle)
                ball.vx = speed * math.cos(rad) + spin * 80
                ball.vy = -abs(speed * math.sin(rad))

            # Brick collisions
            for brick in self._bricks:
                if not brick.alive:
                    continue
                br = brick.rect
                if not ball.rect().colliderect(br):
                    continue
                pts = brick.hit()
                self._score += pts
                if self._audio:
                    self._audio.play_collect()
                # Determine bounce axis
                overlap_x = min(
                    abs(ball.x - br.left), abs(ball.x - br.right)
                )
                overlap_y = min(
                    abs(ball.y - br.top), abs(ball.y - br.bottom)
                )
                if overlap_x < overlap_y:
                    ball.vx = -ball.vx
                else:
                    ball.vy = -ball.vy
                # Power-up drop
                if not brick.alive and random.random() < POWERUP_PROB:
                    kind = random.choice(["WIDE", "MULTI", "FAST"])
                    pu = PowerUp(
                        rect=pygame.Rect(br.x, br.y, self._brick_w - self._brick_gap,
                                         max(8, int(14 * self._scale))),
                        kind=kind,
                    )
                    self._powerups.append(pu)

        # Remove dead balls
        for b in dead_balls:
            self._balls.remove(b)

        if not self._balls:
            self._lives -= 1
            if self._lives <= 0:
                self._game_over = True
            else:
                self._spawn_ball()

    def _update_powerups(self, dt: float) -> None:
        dead = []
        for pu in self._powerups:
            pu.update(dt, self._H)
            if not pu.alive:
                dead.append(pu)
                continue
            if pu.rect.colliderect(self._paddle):
                self._apply_powerup(pu.kind)
                dead.append(pu)
        for pu in dead:
            self._powerups.remove(pu)

    def _apply_powerup(self, kind: str) -> None:
        if kind == "WIDE":
            self._paddle_w_timer = 10.0
        elif kind == "MULTI":
            # Clone every active ball
            new_balls = []
            for ball in self._balls:
                if ball.active:
                    angle_offset = random.choice([-25, 25])
                    rad = math.atan2(ball.vy, ball.vx) + math.radians(angle_offset)
                    speed = math.hypot(ball.vx, ball.vy)
                    new_balls.append(Ball(
                        x=ball.x, y=ball.y,
                        vx=speed * math.cos(rad),
                        vy=speed * math.sin(rad),
                        r=self._ball_r,
                        active=True
                    ))
            self._balls.extend(new_balls)
        elif kind == "FAST":
            for ball in self._balls:
                if ball.active:
                    speed = math.hypot(ball.vx, ball.vy)
                    factor = 0.75  # slow down as a deliberate challenge
                    ball.vx = ball.vx / speed * speed * factor
                    ball.vy = ball.vy / speed * speed * factor

    def _check_win(self) -> None:
        if all(not b.alive for b in self._bricks):
            # Check if more levels exist
            if self._level < 5:
                self._next_level()
            else:
                self._you_win = True

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self._screen.fill(BG)
        self._draw_bricks()
        self._draw_powerups()
        self._draw_balls()
        self._draw_paddle()
        self._draw_hud()
        if self._debug_hud:
            self._draw_debug()
        if self._bounce_msg_timer > 0:
            self._draw_bounce_msg()
        if self._paused:
            self._draw_overlay("PAUSED", "ESC to resume   X to menu")
        if self._game_over:
            self._draw_overlay("GAME OVER", f"Score: {self._score}   R=restart   ESC=menu")
        if self._you_win:
            self._draw_overlay("YOU WIN!", f"Final score: {self._score}   R=restart   ESC=menu")

    def _draw_bricks(self) -> None:
        for brick in self._bricks:
            brick.draw(self._screen)

    def _draw_powerups(self) -> None:
        for pu in self._powerups:
            pu.draw(self._screen, self._pu_font_sz)

    def _draw_balls(self) -> None:
        for ball in self._balls:
            ball.draw(self._screen)

    def _draw_paddle(self) -> None:
        # Gradient-ish: draw two rects
        pygame.draw.rect(self._screen, PADDLE_CLR, self._paddle, border_radius=6)
        inner = self._paddle.inflate(-4, -4)
        light = tuple(min(255, c + 60) for c in PADDLE_CLR)
        pygame.draw.rect(self._screen, light, inner, border_radius=4)  # type: ignore

    def _draw_hud(self) -> None:
        sc = self._scale
        # Score
        score_surf = self._font_md.render(f"Score: {self._score}", True, TEXT_CLR)
        self._screen.blit(score_surf, (max(4, int(12 * sc)), max(4, int(8 * sc))))
        # Lives (use ball_r for visual consistency)
        life_r = self._ball_r - max(1, int(2 * sc))
        gap    = max(life_r * 2 + 2, int(22 * sc))
        hud_y  = max(life_r + 2, int(18 * sc))
        for i in range(self._lives):
            pygame.draw.circle(
                self._screen, BALL_CLR,
                (self._W - max(life_r + 2, int(20 * sc)) - i * gap, hud_y),
                life_r,
            )
        # Level
        lvl_surf = self._font_sm.render(f"Level {self._level}", True, DIM_CLR)
        self._screen.blit(lvl_surf, lvl_surf.get_rect(
            center=(self._W // 2, max(4, int(10 * sc)))
        ))
        # WIDE timer bar
        if self._paddle_w_timer > 0:
            bar_w = int((self._paddle_w_timer / 10.0) * int(120 * sc))
            bar_h = max(2, int(4 * sc))
            bar_r = pygame.Rect(self._paddle.x,
                                self._paddle_y + self._paddle_h + max(2, int(4 * sc)),
                                bar_w, bar_h)
            pygame.draw.rect(self._screen, POWERUP_CLRS["WIDE"], bar_r)
        if self._mode == "accessible":
            self._draw_hud_mode_badge()

    def _draw_debug(self) -> None:
        gs = self._gesture_source.get_state() if self._gesture_source else None
        if gs is None:
            return
        lines = [
            f"ax (smooth) : {gs.raw_ax:+.3f} g",
            f"gz (smooth) : {gs.raw_gz:+.3f} °/s",
            f"paddle vel  : {gs.paddle_velocity:+.3f}",
            f"spin        : {gs.spin:+.3f}",
            f"tilt_y      : {gs.tilt_y:+.3f}",
            f"launch      : {gs.launch}",
        ]
        sc  = self._scale
        row = max(10, int(16 * sc))
        y0  = self._H - max(70, int(110 * sc))
        for i, line in enumerate(lines):
            surf = self._font_sm.render(line, True, (180, 220, 180))
            self._screen.blit(surf, (10, y0 + i * row))

    def _draw_bounce_msg(self) -> None:
        alpha = min(255, int(self._bounce_msg_timer / BOUNCE_MSG_DURATION * 400))
        surf  = self._font_md.render("Nice try!  Keep going!", True, (80, 220, 100))
        surf.set_alpha(alpha)
        self._screen.blit(surf, surf.get_rect(
            center=(self._W // 2, self._H - max(40, int(70 * self._scale)))
        ))

    def _draw_hud_mode_badge(self) -> None:
        sc = self._scale
        badge = self._font_sm.render("ASTRA", True, (80, 220, 100))
        self._screen.blit(badge, badge.get_rect(
            right=self._W - max(4, int(10 * sc)),
            bottom=self._H - max(4, int(8 * sc)),
        ))

    def _draw_overlay(self, title: str, subtitle: str = "") -> None:
        sc = self._scale
        # Dim background
        dim = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 160))
        self._screen.blit(dim, (0, 0))
        # Title
        t = self._font_lg.render(title, True, TEXT_CLR)
        self._screen.blit(t, t.get_rect(
            center=(self._W // 2, self._H // 2 - max(15, int(30 * sc)))
        ))
        # Subtitle
        if subtitle:
            s = self._font_md.render(subtitle, True, DIM_CLR)
            self._screen.blit(s, s.get_rect(
                center=(self._W // 2, self._H // 2 + max(10, int(20 * sc)))
            ))
