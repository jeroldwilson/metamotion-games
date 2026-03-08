"""
home.py — Game selection home screen

Displays two game cards (Bricks, Snake) and lets the player choose with:
  • Sensor tilt left/right — navigate between cards
  • Sensor flick (launch) — confirm selection
  • Mouse hover / click    — highlight and select
  • LEFT / RIGHT arrows    — navigate
  • ENTER / SPACE          — confirm selection
  • ESC                    — quit application
"""

import math
import sys
from typing import List, Optional, Tuple

import pygame


# ── Dimensions ────────────────────────────────────────────────────────────────
W, H       = 800, 600
FPS        = 60
CARD_W     = 340
CARD_H     = 280
CARD_GAP   = 60
CARD_Y     = 128
MARGIN     = (W - 2 * CARD_W - CARD_GAP) // 2  # 30 px each side

# ── Colours ───────────────────────────────────────────────────────────────────
BG         = (15,  15,  25)
TEXT_CLR   = (255, 255, 255)
DIM_CLR    = (165, 165, 180)
CARD_BG    = (30,  30,  52)

# ── Game metadata ─────────────────────────────────────────────────────────────
GAMES = ["bricks", "snake"]

GAME_META = {
    "bricks": {
        "title":   "BRICKS",
        "desc":    ["Break all the bricks!", "Tilt wrist to move paddle.", "Flick to launch ball."],
        "desc_ac": ["Break all the bricks!", "Wider paddle, slower ball.", "Ball bounces — no game over!"],
        "accent":  (110, 200, 255),
    },
    "snake": {
        "title":   "SNAKE",
        "desc":    ["Eat food, grow longer!", "Tilt wrist to steer.", "Avoid walls and yourself."],
        "desc_ac": ["Eat food, grow longer!", "Move wrist — snake finds the way!", "Walls wrap, no game over!"],
        "accent":  (100, 240, 120),
    },
}

MODE_META = {
    "keyboard":   {"label": "KEYBOARD",              "color": (155, 155, 175)},
    "standard":   {"label": "VEERA (Standard)",      "color": (110, 200, 255)},
    "accessible": {"label": "ASTRA (Accessible)",    "color": (100, 240, 120)},
}

# Tilt navigation thresholds / timing
TILT_THRESHOLD     = 0.55   # paddle_velocity magnitude to trigger navigation
TILT_NAV_CD        = 1.20   # cooldown after any navigation (standard / VEERA)
ACC_NAV_CD         = 2.50   # cooldown after any navigation (accessible / ASTRA)
ACC_HOLD_REQUIRED  = 0.35   # ASTRA: gesture must be held this long before navigating


class HomeScreen:
    """
    Renders the game selection menu and blocks until a game is chosen.
    Accepts an existing pygame surface and clock (owned by main.py).
    """

    def __init__(
        self,
        screen: pygame.Surface,
        clock: pygame.time.Clock,
        mode: str = "standard",
    ):
        self._clock  = clock
        self.mode    = mode   # mutable; games read this after run() returns

        self._selected_idx: int = 0
        self._hover_idx: Optional[int] = None

        # Tilt navigation state
        self._tilt_dir: int       = 0    # last detected direction (edge detection)
        self._nav_cd: float       = 0.0  # cooldown after any navigation
        self._acc_hold_dir: int   = 0    # ASTRA: direction currently being held
        self._acc_hold: float     = 0.0  # ASTRA: seconds gesture held in current dir

        # Glow animation
        self._glow_phase: float = 0.0

        self._init_layout(screen)

    def _init_layout(self, screen: pygame.Surface) -> None:
        """Compute all screen-size-dependent layout variables."""
        self._screen = screen
        sw, sh = screen.get_size()
        self._layout_size = (sw, sh)   # snapshot — surface mutated in-place by pygame
        self._W  = sw
        self._H  = sh
        self._is_fullscreen = not (sw == 800 and sh == 600)
        sc = min(sw / W, sh / H)   # W, H are the module-level 800×600 base
        self._sc = sc

        card_w   = int(CARD_W  * sc)
        card_h   = int(CARD_H  * sc)
        card_gap = int(CARD_GAP * sc)
        card_y   = int(CARD_Y  * sc)
        margin   = (sw - len(GAMES) * card_w - (len(GAMES) - 1) * card_gap) // 2

        self._card_w   = card_w
        self._card_h   = card_h
        self._card_y   = card_y

        self._font_title = pygame.font.SysFont("monospace", max(20, int(42 * sc)), bold=True)
        self._font_sub   = pygame.font.SysFont("monospace", max( 8, int(14 * sc)))
        self._font_card  = pygame.font.SysFont("monospace", max(14, int(28 * sc)), bold=True)
        self._font_desc  = pygame.font.SysFont("monospace", max( 8, int(14 * sc)))

        self._card_rects = []
        for i in range(len(GAMES)):
            x = margin + i * (card_w + card_gap)
            self._card_rects.append(pygame.Rect(x, card_y, card_w, card_h))

    def _toggle_fullscreen(self) -> None:
        """Switch between fullscreen (native res) and windowed (800×600)."""
        self._is_fullscreen = not self._is_fullscreen
        if self._is_fullscreen:
            new_screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            new_screen = pygame.display.set_mode((800, 600))
        self._init_layout(new_screen)

    # ── Public entry point ─────────────────────────────────────────────────────

    def run(self, gesture_src) -> str:
        """Block until a game is selected. Returns game name string."""
        pygame.mouse.set_visible(True)
        while True:
            dt = self._clock.tick(FPS) / 1000.0
            self._glow_phase = (self._glow_phase + dt * 2.5) % (2 * math.pi)
            self._nav_cd = max(0.0, self._nav_cd - dt)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit(0)
                result = self._handle_event(event)
                if result:
                    return result

            self._update_hover()
            result = self._handle_gesture(gesture_src, dt)
            if result:
                return result

            self._draw()
            pygame.display.flip()

    # ── Input handling ────────────────────────────────────────────────────────

    def _handle_event(self, event: pygame.event.Event) -> Optional[str]:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_LEFT:
                self._navigate(-1)
            elif event.key == pygame.K_RIGHT:
                self._navigate(1)
            elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                return GAMES[self._selected_idx]
            elif event.key == pygame.K_m:
                self._cycle_mode()
            elif event.key == pygame.K_f:
                self._toggle_fullscreen()
            elif event.key == pygame.K_ESCAPE:
                pygame.quit()
                sys.exit(0)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self._hover_idx is not None:
                return GAMES[self._hover_idx]
        return None

    def _update_hover(self) -> None:
        mx, my = pygame.mouse.get_pos()
        self._hover_idx = None
        for i, rect in enumerate(self._card_rects):
            if rect.collidepoint(mx, my):
                self._hover_idx = i
                self._selected_idx = i
                break

    def _handle_gesture(self, gesture_src, dt: float) -> Optional[str]:
        if gesture_src is None:
            return None
        gs = gesture_src.get_state()
        if not gs.calibrated:
            return None

        # Flick (launch gesture) confirms selection
        if gs.launch:
            return GAMES[self._selected_idx]

        # Determine current tilt direction
        direction = 0
        if gs.paddle_velocity < -TILT_THRESHOLD:
            direction = -1
        elif gs.paddle_velocity > TILT_THRESHOLD:
            direction = 1

        if self.mode == "accessible":
            # ASTRA: hold gesture for ACC_HOLD_REQUIRED seconds before navigating,
            # then enforce a long cooldown so brief/involuntary moves are ignored.
            if direction != 0 and self._nav_cd <= 0:
                if direction == self._acc_hold_dir:
                    self._acc_hold += dt
                    if self._acc_hold >= ACC_HOLD_REQUIRED:
                        self._navigate(direction)
                        self._nav_cd    = ACC_NAV_CD
                        self._acc_hold  = 0.0
                else:
                    # Direction changed — restart hold timer
                    self._acc_hold_dir = direction
                    self._acc_hold     = 0.0
            elif direction == 0:
                self._acc_hold_dir = 0
                self._acc_hold     = 0.0
        else:
            # VEERA / keyboard: edge trigger only — navigate once when a new
            # direction appears; no repeat while held, cooldown prevents bouncing.
            if direction != 0 and direction != self._tilt_dir and self._nav_cd <= 0:
                self._navigate(direction)
                self._nav_cd = TILT_NAV_CD

        self._tilt_dir = direction
        return None

    def _navigate(self, direction: int) -> None:
        self._selected_idx = (self._selected_idx + direction) % len(GAMES)

    def _cycle_mode(self) -> None:
        """Cycle standard ↔ accessible (keyboard mode is fixed at launch)."""
        if self.mode == "keyboard":
            return
        self.mode = "accessible" if self.mode == "standard" else "standard"

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        self._screen.fill(BG)
        self._draw_title()
        for i, game_id in enumerate(GAMES):
            self._draw_card(i, game_id)
        self._draw_hint()

    def _draw_title(self) -> None:
        sc = self._sc
        cx = self._W // 2
        title = self._font_title.render("SELECT YOUR GAME", True, TEXT_CLR)
        self._screen.blit(title, title.get_rect(center=(cx, int(52 * sc))))
        sub = self._font_sub.render(
            "tilt or arrow keys to choose   •   flick / enter / click to play",
            True, DIM_CLR
        )
        self._screen.blit(sub, sub.get_rect(center=(cx, int(88 * sc))))
        self._draw_mode_badge()

    def _draw_mode_badge(self) -> None:
        sc    = self._sc
        meta  = MODE_META[self.mode]
        color = meta["color"]
        label = f"MODE: {meta['label']}"
        if self.mode != "keyboard":
            label += "   (M = switch)"
        surf = self._font_sub.render(label, True, color)
        rect = surf.get_rect(center=(self._W // 2, int(108 * sc)))
        # Pill background
        pad  = pygame.Rect(rect.left - int(8 * sc), rect.top - max(2, int(3 * sc)),
                           rect.width + int(16 * sc), rect.height + max(4, int(6 * sc)))
        bg   = pygame.Surface((pad.width, pad.height), pygame.SRCALPHA)
        bg.fill((*color, 35))
        self._screen.blit(bg, pad.topleft)
        pygame.draw.rect(self._screen, (*color, 120), pad, 1, border_radius=max(4, int(8 * sc)))
        self._screen.blit(surf, rect)

    def _draw_card(self, idx: int, game_id: str) -> None:
        rect   = self._card_rects[idx]
        meta   = GAME_META[game_id]
        accent = meta["accent"]
        is_sel = (idx == self._selected_idx)

        # Card background
        pygame.draw.rect(self._screen, CARD_BG, rect, border_radius=10)

        if is_sel:
            self._draw_glow(rect, accent)
            border_clr = accent
            border_w   = 3
        else:
            border_clr = tuple(max(0, c - 120) for c in accent)  # type: ignore
            border_w   = 2

        pygame.draw.rect(self._screen, border_clr, rect, border_w, border_radius=10)

        # Game title
        sc = self._sc
        title_surf = self._font_card.render(meta["title"], True,
                                            accent if is_sel else DIM_CLR)
        self._screen.blit(title_surf, title_surf.get_rect(
            centerx=rect.centerx, top=rect.top + max(8, int(18 * sc))
        ))

        # Thumbnail preview
        pad_h = max(8, int(20 * sc))
        prev_h = int(130 * sc)
        preview_rect = pygame.Rect(
            rect.left + pad_h, rect.top + max(30, int(65 * sc)),
            rect.width - 2 * pad_h, prev_h,
        )
        self._draw_preview(game_id, preview_rect, accent, is_sel)

        # Description lines — use accessible copy when in that mode
        sc       = self._sc
        desc_key = "desc_ac" if self.mode == "accessible" else "desc"
        for j, line in enumerate(meta[desc_key]):
            clr  = TEXT_CLR if is_sel else DIM_CLR
            surf = self._font_desc.render(line, True, clr)
            self._screen.blit(surf, surf.get_rect(
                centerx=rect.centerx,
                top=rect.top + int(210 * sc) + j * max(16, int(20 * sc))
            ))

    def _draw_glow(self, rect: pygame.Rect, accent: Tuple[int, int, int]) -> None:
        sc = self._sc
        brightness = int(180 + 60 * math.sin(self._glow_phase))
        for extra, alpha in ((int(14*sc), 35), (int(9*sc), 22), (int(4*sc), 12)):
            gw = rect.width  + extra * 2
            gh = rect.height + extra * 2
            gsurf = pygame.Surface((gw, gh), pygame.SRCALPHA)
            gclr  = (accent[0], accent[1], accent[2], alpha)
            pygame.draw.rect(gsurf, gclr, gsurf.get_rect(), border_radius=14)
            self._screen.blit(gsurf, (rect.left - extra, rect.top - extra))

    def _draw_preview(
        self,
        game_id: str,
        area: pygame.Rect,
        accent: Tuple[int, int, int],
        active: bool,
    ) -> None:
        # Clip preview drawing to the area rect
        prev_clip = self._screen.get_clip()
        self._screen.set_clip(area)

        pygame.draw.rect(self._screen, (10, 10, 20), area, border_radius=6)

        dim = 180 if active else 100

        if game_id == "bricks":
            self._draw_bricks_preview(area, dim)
        elif game_id == "snake":
            self._draw_snake_preview(area, dim)

        self._screen.set_clip(prev_clip)

    def _draw_bricks_preview(self, area: pygame.Rect, dim: int) -> None:
        sc = self._sc
        PALETTE = [
            (220, 60, 60), (240, 160, 40), (60, 200, 100),
            (60, 140, 240), (180, 60, 220), (240, 240, 60),
        ]
        bw   = max(8,  int(36 * sc))
        bh   = max(4,  int(14 * sc))
        cols, rows = 7, 4
        pad_x = (area.width  - cols * bw) // 2
        pad_y = max(4, int(8 * sc))
        for row in range(rows):
            for col in range(cols):
                base = PALETTE[row % len(PALETTE)]
                clr  = tuple(min(255, int(c * dim / 255)) for c in base)
                bx = area.left + pad_x + col * bw + 2
                by = area.top  + pad_y + row * (bh + 2)
                pygame.draw.rect(self._screen, clr, (bx, by, max(2, bw - 2), bh), border_radius=2)

        # Paddle
        pw = max(20, int(60 * sc))
        ph = max(3,  int(8 * sc))
        px = area.centerx - pw // 2
        py = area.bottom - max(8, int(18 * sc))
        pygame.draw.rect(self._screen, (min(255, dim), min(255, int(dim * 0.7)), 255),
                         (px, py, pw, ph), border_radius=max(2, int(4 * sc)))

        # Ball
        ball_r = max(2, int(5 * sc))
        bx2 = area.centerx + max(4, int(10 * sc))
        by2 = area.bottom - max(12, int(32 * sc))
        pygame.draw.circle(self._screen, (dim, dim, dim), (bx2, by2), ball_r)

    def _draw_snake_preview(self, area: pygame.Rect, dim: int) -> None:
        sc   = self._sc
        CELL = max(6, int(14 * sc))
        # Subtle grid
        for x in range(area.left, area.right, CELL):
            pygame.draw.line(self._screen, (30, 30, 50), (x, area.top), (x, area.bottom))
        for y in range(area.top, area.bottom, CELL):
            pygame.draw.line(self._screen, (30, 30, 50), (area.left, y), (area.right, y))

        # Snake body — an L-shaped path
        snake_cells = [
            (8, 4), (7, 4), (6, 4), (5, 4), (4, 4), (4, 5), (4, 6),
        ]
        ox = area.left + max(4, int(10 * sc))
        oy = area.top  + max(4, int(10 * sc))
        br = max(1, int(3 * sc))
        for i, (cx, cy) in enumerate(snake_cells):
            r = pygame.Rect(ox + cx * CELL + 1, oy + cy * CELL + 1, CELL - 2, CELL - 2)
            if i == 0:
                clr = (min(255, int(140 * dim / 255)),
                       255,
                       min(255, int(140 * dim / 255)))
            else:
                clr = (min(255, int(80 * dim / 255)),
                       min(255, int(220 * dim / 255)),
                       min(255, int(100 * dim / 255)))
            pygame.draw.rect(self._screen, clr, r, border_radius=br)

        # Food — mini apple (scaled)
        fcx = ox + 11 * CELL + CELL // 2
        fcy = oy + 4  * CELL + CELL // 2
        alpha = dim
        clr = (min(255, int(210 * alpha / 255)), min(255, int(40 * alpha / 255)), min(255, int(40 * alpha / 255)))
        ar = max(2, int(5 * sc))
        pygame.draw.circle(self._screen, clr, (fcx, fcy + max(1, int(1 * sc))), ar)
        pygame.draw.line(self._screen, (min(255, int(100 * alpha / 255)), 40, 10),
                         (fcx, fcy - max(2, int(5 * sc))),
                         (fcx + max(1, int(1 * sc)), fcy - max(3, int(7 * sc))), 1)
        pygame.draw.ellipse(self._screen, (min(255, int(55 * alpha / 255)), min(255, int(175 * alpha / 255)), 50),
                            pygame.Rect(fcx + max(1, int(1 * sc)), fcy - max(4, int(8 * sc)),
                                        max(2, int(4 * sc)), max(1, int(2 * sc))))

    def _draw_hint(self) -> None:
        sc = self._sc
        controls = [
            ("Sensor", "tilt to choose • flick to play"),
            ("Mouse",  "hover to choose • click to play"),
            ("Keys",   "← → to choose • Enter to play  •  F = fullscreen"),
        ]
        y = self._card_y + self._card_h + max(10, int(20 * sc))
        for label, text in controls:
            line = f"{label}: {text}"
            surf = self._font_sub.render(line, True, DIM_CLR)
            self._screen.blit(surf, surf.get_rect(center=(self._W // 2, y)))
            y += max(12, int(18 * sc))
