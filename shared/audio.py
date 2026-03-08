"""
audio.py — Procedural audio for MetaMotion Arcade

All sounds are generated from numpy arrays at runtime (no external files).
Background melody: gentle C-major pentatonic arpeggio that loops seamlessly.
Collect sound: short ascending chime played each time a fruit/brick is collected.
"""

import numpy as np
import pygame


# ── Note table (Hz) ───────────────────────────────────────────────────────────

NOTES = {
    'C4': 261.63, 'D4': 293.66, 'E4': 329.63, 'G4': 392.00, 'A4': 440.00,
    'C5': 523.25, 'D5': 587.33, 'E5': 659.25, 'G5': 783.99, 'A5': 880.00,
    'C6': 1046.50,
}

# Four distinct phrases in C pentatonic that create a 32-note, ~18 s loop.
#
# Phrase A  — gentle ascending arpeggio (low register)
# Phrase B  — descending walk with a rhythmic pause
# Phrase C  — playful upper-register run
# Phrase D  — slow, wide-interval resolution back to root
#
_MELODY = [
    # ── Phrase A: ascending arpeggio ──────────────────────────────────────
    ('C4', 0.35), ('E4', 0.35), ('G4', 0.35), ('A4', 0.35),
    ('C5', 0.35), ('A4', 0.35), ('G4', 0.35), ('E4', 0.35),

    # ── Phrase B: descending walk with a rhythmic skip ────────────────────
    ('A4', 0.28), ('G4', 0.28), ('E4', 0.28), ('D4', 0.28),
    ('C4', 0.42), ('D4', 0.28), ('E4', 0.28), ('C4', 0.42),

    # ── Phrase C: upper-register playful run ──────────────────────────────
    ('E5', 0.22), ('G5', 0.22), ('A5', 0.22), ('G5', 0.22),
    ('E5', 0.22), ('D5', 0.22), ('C5', 0.44), ('G4', 0.44),

    # ── Phrase D: slow, wide-interval resolution ──────────────────────────
    ('C4', 0.55), ('G4', 0.55), ('E4', 0.55), ('A4', 0.55),
    ('G4', 0.45), ('E4', 0.35), ('D4', 0.35), ('C4', 0.55),
]


# ── Core audio manager ─────────────────────────────────────────────────────────

class AudioManager:
    """
    Creates background music and a collect sound from numpy arrays.
    Call start_background() when a game begins and stop_background() on exit.
    Call play_collect() each time the player scores (fruit eaten, brick broken).
    """

    def __init__(self) -> None:
        info = pygame.mixer.get_init()
        if not info:
            pygame.mixer.init(44100, -16, 2, 512)
            info = pygame.mixer.get_init()
        self._rate  = info[0]   # sample rate (Hz)
        self._chans = info[2]   # 1=mono, 2=stereo

        self._bg_sound      = self._to_sound(self._build_bg_loop())
        self._collect_sound = self._to_sound(self._build_collect())

        self._bg_sound.set_volume(0.22)
        self._collect_sound.set_volume(0.65)

    # ── Sound generation ──────────────────────────────────────────────────────

    def _tone(self, freq: float, dur: float, amp: float = 0.18) -> np.ndarray:
        """Sine + 2nd harmonic with ADSR envelope, returned as int16 mono."""
        n = int(self._rate * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        wave = amp * (np.sin(2 * np.pi * freq * t)
                      + 0.28 * np.sin(4 * np.pi * freq * t))
        env = np.ones(n, dtype=np.float32)
        att = max(1, int(n * 0.08))
        rel = max(1, int(n * 0.30))
        env[:att]  = np.linspace(0.0, 1.0, att)
        env[-rel:] = np.linspace(1.0, 0.0, rel)
        return (wave * env * 32767).clip(-32767, 32767).astype(np.int16)

    def _build_bg_loop(self) -> np.ndarray:
        parts = [self._tone(NOTES[n], d, amp=0.11) for n, d in _MELODY]
        return np.concatenate(parts)

    def _build_collect(self) -> np.ndarray:
        return np.concatenate([
            self._tone(NOTES['E5'], 0.08, amp=0.28),
            self._tone(NOTES['G5'], 0.08, amp=0.28),
            self._tone(NOTES['C6'], 0.18, amp=0.28),
        ])

    def _to_sound(self, mono: np.ndarray) -> "pygame.mixer.Sound":
        """Wrap a mono int16 array into a pygame.Sound (stereo-duplicate if needed)."""
        data = np.column_stack([mono, mono]) if self._chans == 2 else mono
        return pygame.sndarray.make_sound(np.ascontiguousarray(data))

    # ── Public API ────────────────────────────────────────────────────────────

    def start_background(self) -> None:
        self._bg_sound.play(loops=-1)

    def stop_background(self) -> None:
        self._bg_sound.stop()

    def play_collect(self) -> None:
        self._collect_sound.play()


# ── Silent fallback ───────────────────────────────────────────────────────────

class _NullAudio:
    """No-op audio used when mixer is unavailable."""
    def start_background(self) -> None: pass
    def stop_background(self)  -> None: pass
    def play_collect(self)     -> None: pass


def make_audio_manager():
    """Return an AudioManager, or a silent fallback if audio init fails."""
    try:
        return AudioManager()
    except Exception as exc:
        print(f"[audio] Audio unavailable ({exc}). Running silent.")
        return _NullAudio()
