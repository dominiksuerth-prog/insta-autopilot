"""Hintergrundmusik für die Reels.

1. Liegen eigene Tracks in data/aktien/music (.mp3/.m4a/.wav), wird einer davon genommen (pro Reel wechselnd).
   Nur Musik verwenden, die du kommerziell nutzen darfst (z. B. selbst gekauft/lizenzfrei mit passender Lizenz).
2. Sonst wird ein ruhiger Lo-Fi-Beat synthetisch erzeugt – jedes Reel klingt etwas anders
   (Tempo, Tonart, Akkordfolge, Klangfarbe). Komplett selbst gerechnet, daher keine Lizenzfragen.
"""
import hashlib
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MUSIC_DIR = ROOT / "data" / "aktien" / "music"
SR = 44100

# Akkordfolgen als Stufen (Halbtöne über dem Grundton, Moll-/Dur-Färbung über die Intervalle)
PROGRESSIONS = [
    [(0, "m7"), (8, "maj7"), (3, "maj7"), (10, "7")],     # i – VI – III – VII
    [(0, "m7"), (5, "m7"), (8, "maj7"), (7, "m7")],       # i – iv – VI – v
    [(0, "maj7"), (9, "m7"), (5, "maj7"), (7, "7")],      # I – vi – IV – V
    [(5, "maj7"), (7, "7"), (4, "m7"), (9, "m7")],        # IV – V – iii – vi
    [(0, "m9"), (5, "m7"), (10, "maj7"), (3, "maj7")],    # i – iv – VII – III
]
CHORDS = {"m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11], "7": [0, 4, 7, 10], "m9": [0, 3, 7, 10, 14]}


def _seed(key: str) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)


def own_tracks():
    if not MUSIC_DIR.exists():
        return []
    return sorted(p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac"))


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x, cutoff):
    """Einfacher Tiefpass (2x einpolig) – macht alles weicher, 'Lo-Fi'."""
    from scipy.signal import lfilter
    a = np.exp(-2 * np.pi * cutoff / SR)
    for _ in range(2):
        x = lfilter([1 - a], [1, -a], x)
    return x


def _highpass(x, cutoff):
    return x - _lowpass(x, cutoff)


def _keys(freq, dur, rng, bright):
    """E-Piano-artiger Ton: Grundton + Obertöne, weicher Anschlag, langsames Abklingen, leichtes Tremolo."""
    t = np.arange(int(dur * SR)) / SR
    det = 1 + rng.uniform(-0.002, 0.002)
    sig = (np.sin(2 * np.pi * freq * det * t)
           + 0.35 * bright * np.sin(2 * np.pi * 2 * freq * t + 0.3)
           + 0.12 * bright * np.sin(2 * np.pi * 3 * freq * t + 1.1))
    env = (1 - np.exp(-t / 0.012)) * np.exp(-t / (dur * 0.55))
    trem = 1 + 0.08 * np.sin(2 * np.pi * 4.5 * t)
    return sig * env * trem


def _kick(rng):
    t = np.arange(int(0.45 * SR)) / SR
    f = 45 + 75 * np.exp(-t / 0.04)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t / 0.13)


def _snare(rng):
    n = int(0.25 * SR)
    t = np.arange(n) / SR
    noise = _lowpass(_highpass(rng.standard_normal(n), 900), 5000)
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.04)
    return (0.55 * noise * np.exp(-t / 0.07) + 0.45 * body)


def _hat(rng):
    n = int(0.06 * SR)
    t = np.arange(n) / SR
    return _highpass(rng.standard_normal(n), 7000) * np.exp(-t / 0.018)


def _add(buf, sig, start, gain=1.0):
    i = int(start * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += gain * sig[: j - i]


def synth(seconds: float, key: str, out_path) -> str:
    """Erzeugt einen ruhigen Beat (Stereo-WAV) von mindestens `seconds` Länge."""
    rng = np.random.default_rng(_seed(key))
    bpm = int(rng.integers(80, 96))
    beat = 60 / bpm
    bar = 4 * beat
    root = int(rng.integers(55, 62))            # Grundton um G3..C#4
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    bright = float(rng.uniform(0.6, 1.0))
    swing = float(rng.uniform(0.04, 0.09)) * beat
    n = int((seconds + 1.0) * SR)
    keys_l, keys_r = np.zeros(n), np.zeros(n)
    bass, drums = np.zeros(n), np.zeros(n)

    nbars = int(np.ceil((seconds + 1.0) / bar))
    for b in range(nbars):
        deg, kind = prog[b % len(prog)]
        t0 = b * bar
        notes = [root + deg + iv for iv in CHORDS[kind]]
        # Akkord leicht "gestrummt", zweiter Anschlag auf Zählzeit 3 (leiser)
        for k, m in enumerate(notes):
            for hit, g in ((0.0, 1.0), (2 * beat + swing, 0.55)):
                tone = _keys(_hz(m), bar * 0.9, rng, bright)
                pan = 0.5 + 0.35 * ((k / max(1, len(notes) - 1)) - 0.5)
                _add(keys_l, tone, t0 + hit + 0.012 * k, g * (1 - pan) * 0.22)
                _add(keys_r, tone, t0 + hit + 0.012 * k, g * pan * 0.22)
        # Bass: Grundton auf 1 und kurz vor 3
        bf = _hz(root + deg - 24)
        for st, ln in ((0, 1.6 * beat), (1.75 * beat + swing, 0.6 * beat), (2.5 * beat, 1.3 * beat)):
            tt = np.arange(int(ln * SR)) / SR
            s = (np.sin(2 * np.pi * bf * tt) + 0.2 * np.sin(4 * np.pi * bf * tt)) * np.exp(-tt / (ln * 0.7))
            s *= 1 - np.exp(-tt / 0.01)
            _add(bass, s, t0 + st, 0.5)
        # Schlagzeug: Kick 1 & 3(+), Snare 2 & 4, Hi-Hats in Achteln mit Swing
        if b > 0 or rng.random() < 0.5:   # manchmal erst ab Takt 2 – sanfter Einstieg
            _add(drums, _kick(rng), t0, 0.9)
            _add(drums, _kick(rng), t0 + 2.5 * beat, 0.7)
            _add(drums, _snare(rng), t0 + beat, 0.32)
            _add(drums, _snare(rng), t0 + 3 * beat, 0.32)
            for e in range(8):
                _add(drums, _hat(rng), t0 + e * beat / 2 + (swing if e % 2 else 0),
                     0.12 if e % 2 else 0.18)

    keys_l, keys_r = _lowpass(keys_l, 2600), _lowpass(keys_r, 2600)
    bass = _lowpass(bass, 400)
    crackle = np.zeros(n)
    idx = rng.integers(0, n, size=int(seconds * 18))
    crackle[idx] = rng.uniform(-1, 1, size=len(idx))
    crackle = _highpass(crackle, 1500) * 0.08 + _lowpass(rng.standard_normal(n), 3000) * 0.004
    left = keys_l + bass + drums + crackle
    right = keys_r + bass + drums + crackle
    st = np.stack([left, right], axis=1)
    st = np.tanh(st * 1.2)                      # sanfte Sättigung
    st *= 0.6 / (np.abs(st).max() + 1e-9)        # ruhige Lautstärke, Platz für den Instagram-Mix
    pcm = (st * 32767).astype("<i2")
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return str(out_path)


def track_for(key: str, seconds: float, tmp_dir) -> str:
    """Pfad zur Musik für ein Reel: eigener Track (falls vorhanden), sonst ein frisch erzeugter Beat."""
    own = own_tracks()
    if own:
        return str(own[_seed(key) % len(own)])
    return synth(seconds, key, Path(tmp_dir) / f"music_{_seed(key):08x}.wav")
