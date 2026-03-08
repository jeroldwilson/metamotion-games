"""
games/calibration/game.py — Sensor Calibration & Orientation Visualizer

Aviation-style four-panel instrument display for the MetaMotion wrist sensor.
Shows live pitch, roll, and yaw using airplane silhouettes and a compass rose.

Only available when a physical sensor is connected (mode ≠ keyboard).

Controls
────────
  ESC / BACKSPACE   → return to home screen
  SPACE / R         → reset yaw accumulator to 0°
  F                 → toggle fullscreen
"""

import math
import sys
import time
from typing import List, Tuple

import pygame


# ── Colors ────────────────────────────────────────────────────────────────────
BG           = (12,  18,  28)
PANEL_BG     = (18,  25,  40)
PANEL_BORDER = (40,  80, 120)
SKY_CLR      = (30, 100, 180)
GROUND_CLR   = (110, 72,  22)
HORIZON_CLR  = (255, 255, 255)
PLANE_CLR    = (220, 230, 240)
PLANE_DARK   = (140, 155, 175)
ACCENT_CLR   = (0,   210, 170)
WARN_CLR     = (255, 160,  30)
TEXT_CLR     = (200, 220, 255)
DIM_CLR      = (100, 130, 170)
GREEN_CLR    = (60,  230, 130)
AMBER_CLR    = (255, 185,  50)
COMPASS_BG   = (14,  22,  42)
CARDINAL_CLR = (255, 220,  60)
TICK_CLR     = (140, 170, 200)
TICK_DIM_CLR = (60,  85, 115)
PITCH_LINE   = (200, 200, 200)


class CalibrationGame:
    """
    Real-time sensor orientation visualizer with four aviation-style panels.

    Panel layout (2 × 2):
      [Front View / Roll]     [Side View / Pitch]
      [Top View  / Yaw ]     [Sensor Data       ]
    """

    def __init__(self, screen, clock, debug=False, mode="standard", audio=None):
        self._screen = screen
        self._clock  = clock
        self._debug  = debug
        self._mode   = mode

        self._yaw_deg = 0.0          # integrated from gz
        self._last_gz = 0.0          # for smooth yaw integration

        self._init_layout()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _init_layout(self) -> None:
        sw, sh = self._screen.get_size()
        self._W, self._H = sw, sh
        self._is_fullscreen = not (sw == 800 and sh == 600)
        sc = min(sw / 800, sh / 600)
        self._sc = sc

        pw = sw // 2
        ph = sh // 2
        gap = 2

        # Four equal panels, with a narrow gap at centre
        self._panels = [
            pygame.Rect(gap,      gap,      pw - gap * 2, ph - gap * 2),   # TL
            pygame.Rect(pw + gap, gap,      sw - pw - gap * 2, ph - gap * 2),  # TR
            pygame.Rect(gap,      ph + gap, pw - gap * 2, sh - ph - gap * 2),  # BL
            pygame.Rect(pw + gap, ph + gap, sw - pw - gap * 2, sh - ph - gap * 2),  # BR
        ]

        self._font_title = pygame.font.SysFont("monospace", max(10, int(13 * sc)), bold=True)
        self._font_data  = pygame.font.SysFont("monospace", max(10, int(13 * sc)))
        self._font_label = pygame.font.SysFont("monospace", max(9,  int(11 * sc)))
        self._font_big   = pygame.font.SysFont("monospace", max(13, int(17 * sc)), bold=True)
        self._font_small = pygame.font.SysFont("monospace", max(7,  int(9  * sc)))

    def _toggle_fullscreen(self) -> None:
        self._is_fullscreen = not self._is_fullscreen
        if self._is_fullscreen:
            new_screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            new_screen = pygame.display.set_mode((800, 600))
        self._screen = new_screen
        self._init_layout()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self, gesture_src) -> str:
        pygame.mouse.set_visible(True)

        while True:
            dt = min(self._clock.tick(60) / 1000.0, 0.05)   # cap at 50 ms

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit(0)
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                        return "home"
                    elif event.key in (pygame.K_SPACE, pygame.K_r):
                        self._yaw_deg = 0.0
                    elif event.key == pygame.K_f:
                        self._toggle_fullscreen()

            gs = gesture_src.get_state()

            ax = gs.abs_ax
            ay = gs.abs_ay
            az = gs.abs_az
            gx = gs.abs_gx
            gy = gs.abs_gy
            gz = gs.abs_gz

            # Integrate yaw from gz (yaw-rate °/s × dt s)
            self._yaw_deg = (self._yaw_deg + gz * dt) % 360.0

            # Compute pitch and roll from gravity vector
            #   Pitch: positive = nose up (ax shifts negative when tilted forward)
            #   Roll:  positive = right wing down (ay shifts positive)
            pitch_deg = math.degrees(math.atan2(-ax, math.sqrt(ay ** 2 + az ** 2)))
            roll_deg  = math.degrees(math.atan2(ay, az))

            self._draw(ax, ay, az, gx, gy, gz,
                       pitch_deg, roll_deg, self._yaw_deg, gs.calibrated)
            pygame.display.flip()

        return "home"

    # ── Top-level draw ────────────────────────────────────────────────────────

    def _draw(self, ax, ay, az, gx, gy, gz,
              pitch, roll, yaw, calibrated) -> None:
        self._screen.fill(BG)
        self._draw_dividers()

        # Panel inner areas (below title bar)
        title_h = max(18, int(22 * self._sc))
        inners = [
            pygame.Rect(p.left + 2, p.top + title_h, p.width - 4, p.height - title_h - 2)
            for p in self._panels
        ]

        self._draw_panel_header(self._panels[0], "FRONT VIEW  •  ROLL",  (0, 180, 255))
        self._draw_panel_header(self._panels[1], "SIDE VIEW  •  PITCH",  (100, 255, 160))
        self._draw_panel_header(self._panels[2], "TOP VIEW  •  YAW",     AMBER_CLR)
        self._draw_panel_header(self._panels[3], "SENSOR DATA",          ACCENT_CLR)

        self._draw_front_view(inners[0], roll)
        self._draw_side_view(inners[1], pitch)
        self._draw_top_view(inners[2], yaw)
        self._draw_data_panel(inners[3], ax, ay, az, gx, gy, gz, pitch, roll, yaw)

        if not calibrated:
            self._draw_calibrating_overlay()

    def _draw_dividers(self) -> None:
        sw, sh = self._W, self._H
        pygame.draw.line(self._screen, PANEL_BORDER, (sw // 2, 0), (sw // 2, sh), 2)
        pygame.draw.line(self._screen, PANEL_BORDER, (0, sh // 2), (sw, sh // 2), 2)

    def _draw_panel_header(self, panel: pygame.Rect,
                           title: str, color: tuple) -> None:
        pygame.draw.rect(self._screen, PANEL_BG, panel)
        pygame.draw.rect(self._screen, PANEL_BORDER, panel, 1)
        surf = self._font_title.render(title, True, color)
        self._screen.blit(surf, (panel.left + 6, panel.top + 3))

    # ── Helper: circular attitude indicator background ────────────────────────

    def _draw_ai_circle(self, cx: int, cy: int, r: int,
                        roll_rad: float = 0.0,
                        pitch_offset_px: float = 0.0) -> None:
        """
        Draw a circular attitude-indicator background (sky + ground) onto
        self._screen, centred at (cx, cy) with radius r.

        roll_rad          — rotation of the sky/ground backdrop (roll)
        pitch_offset_px   — vertical shift of the horizon (positive = nose up)
        """
        diam = r * 2 + 4
        scene = pygame.Surface((diam, diam))
        scx, scy = r + 2, r + 2

        # Fill with sky
        scene.fill(SKY_CLR)

        # Ground polygon (large rotated lower-half rectangle, shifted by pitch)
        ground_pts = self._rotated_half_rect(scx, scy - pitch_offset_px,
                                             r * 5, r * 5, roll_rad, upper=False)
        pygame.draw.polygon(scene, GROUND_CLR, ground_pts)

        # Pitch-degree reference lines (drawn on the rotated scene)
        px_per_deg = r / 70.0
        for deg in (-30, -20, -10, 10, 20, 30):
            offset = -deg * px_per_deg - pitch_offset_px
            cos_r  = math.cos(roll_rad)
            sin_r  = math.sin(roll_rad)
            hw = r * (0.35 if abs(deg) == 30 else 0.22)
            lx = scx - hw * cos_r - offset * sin_r
            ly = scy - hw * sin_r + offset * cos_r
            rx = scx + hw * cos_r - offset * sin_r
            ry = scy + hw * sin_r + offset * cos_r
            pygame.draw.line(scene, PITCH_LINE + (180,),
                             (int(lx), int(ly)), (int(rx), int(ry)), 1)
            label_surf = self._font_small.render(f"{deg:+d}", True, PITCH_LINE)
            scene.blit(label_surf, (int(rx) + 2, int(ry) - 5))

        # Horizon line
        cos_r = math.cos(roll_rad)
        sin_r = math.sin(roll_rad)
        hx1 = scx - r * cos_r + pitch_offset_px * sin_r
        hy1 = scy - r * sin_r - pitch_offset_px * cos_r
        hx2 = scx + r * cos_r + pitch_offset_px * sin_r
        hy2 = scy + r * sin_r - pitch_offset_px * cos_r
        pygame.draw.line(scene, HORIZON_CLR,
                         (int(hx1), int(hy1)), (int(hx2), int(hy2)), 2)

        # Circular mask
        scene_alpha = scene.convert_alpha()
        mask = pygame.Surface((diam, diam), pygame.SRCALPHA)
        mask.fill((0, 0, 0, 0))
        pygame.draw.circle(mask, (255, 255, 255, 255), (scx, scy), r)
        scene_alpha.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        self._screen.blit(scene_alpha, (cx - r - 2, cy - r - 2))
        pygame.draw.circle(self._screen, PANEL_BORDER, (cx, cy), r, 2)

    @staticmethod
    def _rotated_half_rect(cx, cy, w, h, angle, upper=True):
        """Upper or lower half of a rectangle, rotated around (cx, cy)."""
        pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, 0), (-w / 2, 0)] if upper \
            else [(-w / 2, 0), (w / 2, 0), (w / 2, h / 2), (-w / 2, h / 2)]
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)
                for (x, y) in pts]

    # ── Panel 1 — Front View (Roll) ───────────────────────────────────────────

    def _draw_front_view(self, area: pygame.Rect, roll_deg: float) -> None:
        cx, cy = area.centerx, area.centery - max(8, int(12 * self._sc))
        r = min(area.width, area.height) // 2 - max(4, int(8 * self._sc))

        self._draw_ai_circle(cx, cy, r, roll_rad=math.radians(roll_deg))

        # Fixed airplane symbol (seen from front — horizontal wings, body)
        wing_w = int(r * 0.55)
        wing_h = max(3, int(r * 0.07))
        body_h = max(6, int(r * 0.30))
        body_w = max(3, int(r * 0.07))

        # Wings
        pygame.draw.rect(self._screen, PLANE_CLR,
                         (cx - wing_w, cy - wing_h // 2, wing_w * 2, wing_h))
        # Wing tips (darker)
        tip_w = max(2, int(r * 0.10))
        pygame.draw.rect(self._screen, PLANE_DARK,
                         (cx - wing_w, cy - wing_h // 2, tip_w, wing_h))
        pygame.draw.rect(self._screen, PLANE_DARK,
                         (cx + wing_w - tip_w, cy - wing_h // 2, tip_w, wing_h))
        # Fuselage stub
        pygame.draw.rect(self._screen, PLANE_CLR,
                         (cx - body_w // 2, cy - body_h // 2, body_w, body_h))
        # Centre dot
        pygame.draw.circle(self._screen, AMBER_CLR, (cx, cy), max(3, int(r * 0.06)))

        # Roll angle label
        lbl = self._font_label.render(f"Roll:  {roll_deg:+.1f}°", True, TEXT_CLR)
        self._screen.blit(lbl, lbl.get_rect(centerx=cx,
                                             top=area.bottom - max(16, int(18 * self._sc))))

    # ── Panel 2 — Side View (Pitch) ───────────────────────────────────────────

    def _draw_side_view(self, area: pygame.Rect, pitch_deg: float) -> None:
        cx, cy = area.centerx, area.centery - max(8, int(12 * self._sc))
        r = min(area.width, area.height) // 2 - max(4, int(8 * self._sc))

        px_per_deg    = r / 70.0
        pitch_offset  = pitch_deg * px_per_deg   # positive pitch → horizon goes down

        self._draw_ai_circle(cx, cy, r, roll_rad=0.0,
                             pitch_offset_px=pitch_offset)

        # Fixed airplane side profile
        self._draw_airplane_side(cx, cy, r)

        lbl = self._font_label.render(f"Pitch: {pitch_deg:+.1f}°", True, TEXT_CLR)
        self._screen.blit(lbl, lbl.get_rect(centerx=cx,
                                             top=area.bottom - max(16, int(18 * self._sc))))

    def _draw_airplane_side(self, cx: int, cy: int, r: int) -> None:
        sc = r / 58.0
        # Fuselage bar
        fl, fr = int(cx - 38 * sc), int(cx + 32 * sc)
        fw = max(3, int(7 * sc))
        pygame.draw.rect(self._screen, PLANE_CLR,
                         (fl, cy - fw // 2, fr - fl, fw), border_radius=max(2, int(3 * sc)))
        # Nose cone
        pygame.draw.polygon(self._screen, PLANE_CLR, [
            (fl, cy - fw // 2), (fl, cy + fw // 2),
            (int(fl - 10 * sc), cy),
        ])
        # Wing
        pygame.draw.polygon(self._screen, PLANE_CLR, [
            (int(cx - 8 * sc),  cy),
            (int(cx - 25 * sc), int(cy + 20 * sc)),
            (int(cx + 12 * sc), cy),
        ])
        # Vertical tail fin
        pygame.draw.polygon(self._screen, PLANE_CLR, [
            (int(cx + 28 * sc), cy),
            (int(cx + 32 * sc), cy),
            (int(cx + 30 * sc), int(cy - 18 * sc)),
        ])
        # Horizontal tail
        pygame.draw.polygon(self._screen, PLANE_CLR, [
            (int(cx + 24 * sc), cy),
            (int(cx + 32 * sc), cy),
            (int(cx + 32 * sc), int(cy + 7 * sc)),
            (int(cx + 18 * sc), int(cy + 6 * sc)),
        ])
        # Cockpit
        pygame.draw.circle(self._screen, (140, 195, 255),
                           (int(fl + 6 * sc), cy), max(4, int(7 * sc)))
        # Centre marker
        pygame.draw.circle(self._screen, AMBER_CLR, (cx, cy), 3)

    # ── Panel 3 — Top View (Yaw / Compass) ───────────────────────────────────

    def _draw_top_view(self, area: pygame.Rect, yaw_deg: float) -> None:
        cx, cy = area.centerx, area.centery - max(6, int(10 * self._sc))
        r = min(area.width, area.height) // 2 - max(4, int(8 * self._sc))

        # Compass background
        pygame.draw.circle(self._screen, COMPASS_BG, (cx, cy), r)
        pygame.draw.circle(self._screen, PANEL_BORDER, (cx, cy), r, 2)

        # Subtle concentric ring
        pygame.draw.circle(self._screen, PANEL_BORDER, (cx, cy), int(r * 0.6), 1)

        # Tick marks (every 10°; labelled at 30°, thick at 90°)
        for i in range(0, 360, 10):
            angle = math.radians(i - 90)   # 0° = North = top
            if i % 90 == 0:
                tick_len, color, width = r * 0.18, CARDINAL_CLR, 2
            elif i % 30 == 0:
                tick_len, color, width = r * 0.11, TICK_CLR, 1
            else:
                tick_len, color, width = r * 0.055, TICK_DIM_CLR, 1
            x1 = cx + (r - tick_len) * math.cos(angle)
            y1 = cy + (r - tick_len) * math.sin(angle)
            x2 = cx + r * math.cos(angle)
            y2 = cy + r * math.sin(angle)
            pygame.draw.line(self._screen, color,
                             (int(x1), int(y1)), (int(x2), int(y2)), width)

        # Cardinal labels
        label_r = r - max(14, int(22 * self._sc))
        for label, deg in [("N", 0), ("E", 90), ("S", 180), ("W", 270)]:
            angle = math.radians(deg - 90)
            lx = cx + label_r * math.cos(angle)
            ly = cy + label_r * math.sin(angle)
            surf = self._font_big.render(label, True, CARDINAL_CLR)
            self._screen.blit(surf, surf.get_rect(center=(int(lx), int(ly))))

        # Intercardinal labels
        for label, deg in [("NE", 45), ("SE", 135), ("SW", 225), ("NW", 315)]:
            angle = math.radians(deg - 90)
            lx = cx + label_r * math.cos(angle)
            ly = cy + label_r * math.sin(angle)
            surf = self._font_small.render(label, True, TICK_CLR)
            self._screen.blit(surf, surf.get_rect(center=(int(lx), int(ly))))

        # Airplane top silhouette (rotates with yaw)
        self._draw_airplane_top(cx, cy, r, yaw_deg)

        lbl = self._font_label.render(
            f"Yaw: {yaw_deg:.1f}°   SPACE=reset", True, TEXT_CLR)
        self._screen.blit(lbl, lbl.get_rect(
            centerx=cx, top=area.bottom - max(16, int(18 * self._sc))))

    def _draw_airplane_top(self, cx: int, cy: int, r: int, yaw_deg: float) -> None:
        """Top-down airplane silhouette, rotated so 0° = pointing North (up)."""
        sc  = r / 58.0
        yaw_rad = math.radians(yaw_deg - 90)   # -90 → 0° aligns to top

        def rot(x, y):
            cos_a = math.cos(yaw_rad)
            sin_a = math.sin(yaw_rad)
            return (int(cx + x * cos_a - y * sin_a),
                    int(cy + x * sin_a + y * cos_a))

        # Fuselage (nose at top / -y direction)
        fuse = [
            rot(0,           -34 * sc),   # nose
            rot(5  * sc,     -18 * sc),
            rot(6  * sc,      12 * sc),
            rot(0,            28 * sc),   # tail tip
            rot(-6 * sc,      12 * sc),
            rot(-5 * sc,     -18 * sc),
        ]
        pygame.draw.polygon(self._screen, PLANE_CLR, fuse)

        # Main wings
        wing_l = [
            rot(-5  * sc,  -6 * sc),
            rot(-34 * sc,   4 * sc),
            rot(-28 * sc,  10 * sc),
            rot(-3  * sc,   2 * sc),
        ]
        wing_r = [
            rot( 5  * sc,  -6 * sc),
            rot( 34 * sc,   4 * sc),
            rot( 28 * sc,  10 * sc),
            rot( 3  * sc,   2 * sc),
        ]
        pygame.draw.polygon(self._screen, PLANE_CLR, wing_l)
        pygame.draw.polygon(self._screen, PLANE_CLR, wing_r)

        # Horizontal tail fins
        tail_l = [
            rot(-3  * sc,  18 * sc),
            rot(-15 * sc,  26 * sc),
            rot(-13 * sc,  30 * sc),
            rot(-2  * sc,  23 * sc),
        ]
        tail_r = [
            rot( 3  * sc,  18 * sc),
            rot( 15 * sc,  26 * sc),
            rot( 13 * sc,  30 * sc),
            rot( 2  * sc,  23 * sc),
        ]
        pygame.draw.polygon(self._screen, PLANE_CLR, tail_l)
        pygame.draw.polygon(self._screen, PLANE_CLR, tail_r)

        # Nose & cockpit dots
        pygame.draw.circle(self._screen, (140, 195, 255), rot(0, -28 * sc), max(3, int(4 * sc)))
        pygame.draw.circle(self._screen, ACCENT_CLR, (cx, cy), max(3, int(4 * sc)))

    # ── Panel 4 — Sensor Data ─────────────────────────────────────────────────

    def _draw_data_panel(self, area: pygame.Rect,
                         ax, ay, az, gx, gy, gz,
                         pitch, roll, yaw) -> None:
        lh = max(14, int(16 * self._sc))
        y = area.top + 4
        x = area.left + 8

        rows = [
            ("ATTITUDE",              None,       None),
            ("  Pitch",   f"{pitch:+7.1f} °",    GREEN_CLR),
            ("  Roll ",   f"{roll:+7.1f} °",     GREEN_CLR),
            ("  Yaw  ",   f"{yaw:+7.1f} °",      GREEN_CLR),
            ("",                      None,       None),
            ("ACCELEROMETER",         None,       None),
            ("  ax",      f"{ax:+7.3f} g",       TEXT_CLR),
            ("  ay",      f"{ay:+7.3f} g",       TEXT_CLR),
            ("  az",      f"{az:+7.3f} g",       TEXT_CLR),
            ("",                      None,       None),
            ("GYROSCOPE",             None,       None),
            ("  gx",      f"{gx:+7.1f} °/s",     TEXT_CLR),
            ("  gy",      f"{gy:+7.1f} °/s",     TEXT_CLR),
            ("  gz",      f"{gz:+7.1f} °/s",     TEXT_CLR),
            ("",                      None,       None),
            ("CONTROLS",              None,       None),
            ("  ESC",     "home",                DIM_CLR),
            ("  SPACE",   "reset yaw",           DIM_CLR),
            ("  F",       "fullscreen",          DIM_CLR),
        ]

        for label, value, color in rows:
            if not label:
                y += lh // 2
                continue
            if color is None:
                surf = self._font_data.render(label, True, ACCENT_CLR)
                self._screen.blit(surf, (x, y))
            else:
                lsurf = self._font_data.render(label, True, DIM_CLR)
                vsurf = self._font_data.render(value, True, color)
                self._screen.blit(lsurf, (x, y))
                self._screen.blit(vsurf, (x + lsurf.get_width() + 4, y))
            y += lh
            if y > area.bottom - lh:
                break

    # ── Calibrating overlay ───────────────────────────────────────────────────

    def _draw_calibrating_overlay(self) -> None:
        overlay = pygame.Surface((self._W, self._H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 170))
        self._screen.blit(overlay, (0, 0))

        cx, cy = self._W // 2, self._H // 2
        t = self._font_big.render("CALIBRATING…", True, WARN_CLR)
        s = self._font_data.render(
            "Hold the sensor still to establish neutral orientation.", True, TEXT_CLR)
        self._screen.blit(t, t.get_rect(center=(cx, cy - 18)))
        self._screen.blit(s, s.get_rect(center=(cx, cy + 16)))
