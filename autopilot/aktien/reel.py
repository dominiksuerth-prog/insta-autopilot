"""Reel-Werkstatt: Instagram-Videos (9:16, 1080x1920, 30 fps) in festen Formaten.

Formate (FORMATS):
  jahre   – Jahre einer Aktie/eines Index nacheinander uebereinander, aktuelles Jahr zuletzt in Gelb
  duell   – 2–3 Kurse laufen gegeneinander, am Ende "10.000 € wurden zu ..."
  mythos  – Boersenweisheit Jahr fuer Jahr geprueft (Sell in May, Dezember, Januar-Effekt, Minus-Jahre)
  fonds   – groesste Neukaeufe der Hedgefonds im letzten 13F-Quartal

Gemeinsamer Look: Stage (Hintergrund, Kopf, Story-Balken, Fuss). Jedes Format liefert prepare/total/draw/caption.
Gleiche Bausteine im Video, in der Sofort-Vorschau (ein Standbild) und spaeter im Autopilot auf GitHub.
Braucht matplotlib + imageio-ffmpeg; beides wird erst beim Rendern importiert.
"""
import io
import math
import os
import re
import subprocess
import threading
import time
from datetime import datetime

import numpy as np

from .config import INSTANCE_DIR, FONT_DIR

REEL_DIR = os.path.join(INSTANCE_DIR, "reels")
MUSIC_DIR = os.path.join(INSTANCE_DIR, "reel_musik")
FPS, W, H = 30, 1080, 1920
BG = np.array([7, 8, 13]) / 255
WHITE, DIM, INK = "#ffffff", "#7d8597", "#0b0b10"
UP, DOWN = "#00f5a0", "#ff3d68"
CUR_COL = "#ffd60a"
YEAR_COL = ["#00e5ff", "#b388ff", "#ff4ecd", "#7cff4f", "#ff9f1c", "#4d9fff", "#ff6b6b"]
DUEL_COL = ["#00e5ff", "#ff4ecd", CUR_COL]
MAX_YEARS = 7

JOB = {"running": False, "progress": 0.0, "msg": "", "file": None, "ticker": None, "error": None}

_CORP = re.compile(r"[,.]?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|plc|PLC|N\.V\.|S\.A\.|SE|AG|"
                   r"Holdings?|Group|Class [A-C]|\(The\)|& Co\.?|Com|Cl [A-C]|Del|New)$", re.I)

clamp = lambda u: min(max(u, 0.0), 1.0)
ease = lambda u: 1 - (1 - clamp(u)) ** 3


def back(u):
    """Ueberschwingen fuer 'Pop'-Effekte."""
    u = clamp(u)
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def short_name(name: str) -> str:
    """'NVIDIA Corporation' -> 'NVIDIA', 'Alphabet Inc. Class A' -> 'Alphabet', 'S&P 500 (Kursindex)' -> 'S&P 500'."""
    n = (name or "").split(" (")[0].strip()
    for _ in range(4):
        m = _CORP.sub("", n).strip(" ,.")
        if m == n:
            break
        n = m
    return n


def pct(v, dec=1):
    return f"{v:+.{dec}f}%".replace(".", ",").replace("-", "−")


def euro(v):
    return f"{v:,.0f} €".replace(",", ".")


def is_stock(sym: str) -> bool:
    s = (sym or "").upper()
    return bool(s) and not s.startswith("^") and "=" not in s


def missing_packages() -> list:
    out = []
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        out.append("matplotlib")
    if not _ffmpeg():
        out.append("imageio-ffmpeg")
    return out


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def positions():
    """Kuerzel deiner echten Positionen – im Autopiloten aus dem GitHub-Secret AKTIEN_POSITIONEN
    (kommagetrennt, z. B. "NVDA,SAP.DE"). Nicht gesetzt -> None (dann neutraler Hinweis)."""
    raw = os.getenv("AKTIEN_POSITIONEN")
    if raw is None or not raw.strip():
        return None
    if raw.strip() == "-":
        return set()
    return {t.strip().upper() for t in raw.split(",") if t.strip()}


def disclosure(shown: list) -> str:
    stocks = [s for s in dict.fromkeys(shown) if is_stock(s)]
    if not stocks:
        return ""
    mine = positions()
    if mine is None:
        return "Transparenz: Ich kann in gezeigten Aktien investiert sein."
    held = [s for s in stocks if s.upper() in mine]
    if held:
        return f"Transparenz: Ich halte selbst {', '.join(held)}."
    return "Transparenz: Ich halte keine der gezeigten Aktien."


# ======================================================================= Zeichenflaeche

class Stage:
    """Eine Figure fuer alle Bilder eines Videos; Hintergrund je Akzentfarbe nur einmal gerechnet."""

    def __init__(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patheffects as pe
        from matplotlib.font_manager import FontProperties
        self.mpl, self.plt, self.pe = matplotlib, plt, pe
        F = lambda n: FontProperties(fname=os.path.join(FONT_DIR, n + ".ttf"))
        self.ANTON, self.GROT, self.GROT_M = F("anton-latin-400-normal"), F("space-grotesk-latin-700-normal"), \
            F("space-grotesk-latin-500-normal")
        self.MONO, self.MONO_M = F("jetbrains-mono-latin-700-normal"), F("jetbrains-mono-latin-500-normal")
        yy, xx = np.mgrid[0:H, 0:W]
        grain = np.random.default_rng(7).normal(0, 1, (H, W, 1)) * 0.012
        vign = np.clip(1 - (((xx - W / 2) / (W * 0.9)) ** 2 + ((yy - H / 2) / (H * 0.75)) ** 2), 0, 1)[..., None]
        self.glow_map = np.exp(-(((xx - W * 0.55) / (W * 0.55)) ** 2 + ((yy - H * 0.47) / (H * 0.22)) ** 2))[..., None]
        glow_top = np.exp(-(((xx - W * 0.1) / (W * 0.6)) ** 2 + ((yy - H * 0.08) / (H * 0.12)) ** 2))[..., None]
        self.base = BG * (0.55 + 0.45 * vign) + glow_top * self.rgb("#5b3dff") * 0.10 + grain
        self.bg_cache, self.logo_cache, self.font_cache = {}, {}, {}
        self.fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)

    @staticmethod
    def rgb(h):
        return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)]) / 255

    def begin(self, accent, strength=1.0):
        self.fig.clf()
        key = (accent, round(strength, 2))
        if key not in self.bg_cache:
            self.bg_cache[key] = (np.clip(self.base + self.glow_map * self.rgb(accent) * 0.20 * strength, 0, 1) * 255).astype(np.uint8)
        self.fig.figimage(self.bg_cache[key], 0, 0, zorder=-10)

    def text(self, x, y, s, fp, size, color=WHITE, **kw):
        return self.fig.text(x, y, s, fontproperties=fp, fontsize=size, color=color, **kw)

    def pill(self, x, y, s, fg, bg, size, fp=None, alpha=1.0, ha="left", pad=0.45, va="center"):
        self.fig.text(x, y, s, color=fg, fontproperties=fp or self.MONO, fontsize=size, ha=ha, va=va, alpha=alpha,
                      bbox=dict(boxstyle=f"round,pad={pad},rounding_size=0.8", fc=bg, ec="none", alpha=alpha))

    def fit(self, s, fp, size, max_w, min_size=40):
        """Schriftgroesse, bei der s hoechstens max_w (Anteil der Breite) breit ist."""
        key = (s, id(fp), size, round(max_w, 3))
        if key not in self.font_cache:
            t = self.fig.text(0, 0.5, s, fontproperties=fp, fontsize=size)
            self.fig.canvas.draw()
            w = t.get_window_extent().width
            t.remove()
            self.font_cache[key] = size if w <= max_w * W else max(min_size, size * max_w * W / w)
        return self.font_cache[key]

    def logo(self, ticker):
        if ticker in self.logo_cache:
            return self.logo_cache[ticker]
        img = None
        try:
            from . import logos
            p = logos.cached(ticker) or logos.fetch(ticker)
            if p:
                img = self.plt.imread(p)
                if img.ndim == 3 and img.shape[2] == 3:
                    img = np.dstack([img, np.ones(img.shape[:2])])
        except Exception:
            img = None
        self.logo_cache[ticker] = img
        return img

    def logo_tile(self, img, rect, pad=0.14):
        ax = self.fig.add_axes(rect)
        ax.axis("off")
        ax.add_patch(self.mpl.patches.FancyBboxPatch((0.02, 0.02), 0.96, 0.96, boxstyle="round,pad=0,rounding_size=0.18",
                                                     transform=ax.transAxes, fc="#ffffff14", ec="#ffffff30", lw=2))
        ins = ax.inset_axes([pad, pad, 1 - 2 * pad, 1 - 2 * pad])
        ins.axis("off")
        ins.imshow(img, interpolation="lanczos")
        ins.set_aspect("equal")

    def story(self, segments, tt):
        """segments: [(start, dauer, farbe)] – Fortschrittsbalken wie bei Instagram-Stories."""
        wseg, gap = 0.9 / len(segments), 0.008
        for k, (st, du, col) in enumerate(segments):
            ax = self.fig.add_axes([0.05 + k * wseg + gap / 2, 0.972, wseg - gap, 0.004])
            ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
            ax.barh(0.5, 1, height=1, color="#ffffff", alpha=0.18)
            f = clamp((tt - st) / du)
            if f > 0:
                ax.barh(0.5, f, height=1, color=col, alpha=0.95)

    def header(self, tag, title_lines, tt, question="", logo=None, title_size=150):
        """Kleines Etikett, grosser Titel (1–2 Zeilen, ggf. mit Logo), Frage als gelber Sticker."""
        self.pill(0.065, 0.943, f"  {tag}  ", INK, "#ffffff", 22, alpha=0.95)
        x0 = 0.06 + (0.19 if logo is not None else 0)
        if logo is not None:
            self.logo_tile(logo, [0.06, 0.822, 0.16, 0.09])
        n = min(len(title_lines), 3)
        base = {1: title_size, 2: 110, 3: 84}[n]
        size = min(self.fit(ln, self.ANTON, base, 0.94 - x0) for ln in title_lines[:3])
        lh = size * 1.39 / H                       # Zeilenhoehe als Anteil der Bildhoehe (Punkt -> Pixel)
        y0 = 0.912 - 0.78 * lh
        ys = [y0]
        for ln in title_lines[1:n]:                # Zeile mit Umlaut: etwas mehr Abstand, sonst kleben die Punkte oben an
            ys.append(ys[-1] - lh * (1.06 if any(c in ln for c in "ÄÖÜ") else 0.92))
        qy = ys[-1] - 0.048
        for ln, y in zip(title_lines[:3], ys):
            self.text(x0, y, ln, self.ANTON, size)
        if question:
            a = back((tt - 0.25) / 0.45)
            if a > 0:
                y = qy
                self.pill(0.07, y, f" {question} ", INK, CUR_COL, 54 * max(a, 0.01), fp=self.ANTON,
                          alpha=min(1, clamp((tt - 0.25) / 0.2)), pad=0.25)

    def footer(self, handle, source="Daten: Yahoo Finance"):
        if handle:
            self.text(0.065, 0.022, handle, self.GROT, 26, alpha=0.85)
        self.text(0.935, 0.045, f"{source} · keine Anlageberatung", self.GROT_M, 17, DIM, ha="right", alpha=0.8)

    def chart_axes(self, rect=(0.14, 0.375, 0.73, 0.35)):
        ax = self.fig.add_axes(list(rect))
        ax.set_facecolor("none")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(colors=DIM, length=0, pad=8)
        ax.grid(axis="y", color="#ffffff", alpha=0.07, lw=1.5, ls=(0, (2, 4)))
        return ax

    def style_ticks(self, ax, size=21):
        for lab in ax.get_xticklabels() + ax.get_yticklabels():
            lab.set_fontproperties(self.MONO_M)
            lab.set_fontsize(size)

    def glow(self, col, lw):
        pe = self.pe
        return [pe.Stroke(linewidth=lw * 4.5, foreground=col, alpha=0.08),
                pe.Stroke(linewidth=lw * 2.4, foreground=col, alpha=0.18), pe.Normal()]

    def rgb_frame(self):
        self.fig.canvas.draw()
        return np.asarray(self.fig.canvas.buffer_rgba())[:, :, :3]

    def close(self):
        self.plt.close(self.fig)


def wrap(s: str, width: int = 14, lines: int = 2) -> list:
    """Titel auf hoechstens zwei Zeilen umbrechen (an Wortgrenzen)."""
    words, out, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width and len(out) < lines - 1:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    out.append(cur)
    return out


class Smooth:
    """Achsengrenzen gleiten weich nach (im Video); in der Vorschau springen sie sofort ans Ziel."""

    def __init__(self, lo, hi):
        self.lo, self.hi = lo, hi

    def to(self, lo, hi, snap):
        k = 1.0 if snap else 0.14
        self.lo += (lo - self.lo) * k
        self.hi += (hi - self.hi) * k
        return self.lo, self.hi


# ======================================================================= Format: Jahre

def headline(final: dict, years: list, readable: bool = False) -> str:
    h = _headline(final, years)
    if not readable:
        return h
    return (h.capitalize().replace("jahre", "Jahre").replace("jahren", "Jahren").replace(" jahr ", " Jahr ")
            .replace("Platz", "Platz"))


def _headline(final: dict, years: list) -> str:
    cur, prev = years[-1], years[:-1]
    c = final[cur]

    def since(better):
        for y in reversed(prev):
            if not better(final[y]):
                return y
    n = len(years)
    if all(final[y] > c for y in prev):
        return f"SCHWÄCHSTES DER LETZTEN {n} JAHRE"
    if all(final[y] < c for y in prev):
        return f"BESTES DER LETZTEN {n} JAHRE"
    if len(prev) >= 2 and final[prev[-1]] > c and final[prev[-2]] > c:
        return f"SCHWÄCHSTES JAHR SEIT {since(lambda v: v > c)}"
    if len(prev) >= 2 and final[prev[-1]] < c and final[prev[-2]] < c:
        return f"BESTES JAHR SEIT {since(lambda v: v < c)}"
    rank = sorted(years, key=lambda y: final[y], reverse=True).index(cur) + 1
    return f"PLATZ {rank} VON {n} JAHREN"


def build_data(symbol: str, years: list, eur: bool = False) -> dict:
    """Jahres-Format: Linien je Jahr (Tag im Jahr, % seit Jahresanfang). Nur Jahre mit Vorjahresschluss."""
    from . import indices
    r = indices.seasonal(symbol, eur)
    if not r:
        raise ValueError(f"Keine Kurse für {symbol}")
    partial_first = {t["year"] for t in r["table"] if t["partial"] and not t["to_date"]}
    ok = [y for y in sorted(set(years)) if y in r["lines"] and y not in partial_first]
    if len(ok) < 2:
        raise ValueError("Mindestens zwei Jahre mit Kursen nötig")
    ok = ok[-MAX_YEARS:]
    series = {}
    for y in ok:
        pts = r["lines"][y]
        doy = np.array([(datetime.strptime(p["time"], "%Y-%m-%d") - datetime(2000, 1, 1)).days for p in pts])
        vals = np.array([p["value"] for p in pts])
        series[y] = (np.concatenate([[0], doy]), np.concatenate([[0.0], vals]))
    last_date = indices.close(symbol, eur).index[-1] if ok[-1] == r["cur_year"] else None
    return {"format": "jahre", "symbol": symbol, "name": r["name"], "years": ok, "series": series,
            "final": {y: float(series[y][1][-1]) for y in ok}, "cur_running": ok[-1] == r["cur_year"],
            "last_date": last_date, "eur": eur}


class Jahre:
    key, label = "jahre", "📅 Jahre übereinander"
    hint = "Die Jahre einer Aktie bauen sich nacheinander auf, das letzte zuletzt in Gelb – mit Pointe am Ende."
    HOOK, PER, LAST, END = 1.5, 2.1, 2.8, 4.0

    def prepare(self, p):
        d = build_data(p["ticker"], p["years"], p.get("eur", False))
        d["title"] = (p.get("title") or short_name(d["name"]) or d["symbol"]).upper()
        d["question"] = (p.get("question") or f"WO STEHT {d['years'][-1]}?").upper()
        plan, t = [], self.HOOK
        for y in d["years"]:
            du = self.LAST if y == d["years"][-1] else self.PER
            plan.append((t, du, y))
            t += du
        d["plan"], d["end_start"] = plan, t + 0.2
        d["shown"] = [d["symbol"]]
        return d

    def total(self, d):
        return d["end_start"] + self.END

    def preview_times(self, d):
        return [d["plan"][-2][0] + d["plan"][-2][1] * 0.9, self.total(d) - 0.2]

    def draw(self, S, d, tt, st, snap=False):
        years, series, final = d["years"], d["series"], d["final"]
        cur = years[-1]
        cols = {y: YEAR_COL[k % len(YEAR_COL)] for k, y in enumerate(years[:-1])}
        cols[cur] = CUR_COL
        prog = {y: (ease((tt - s) / du) if tt >= s else None) for s, du, y in d["plan"]}
        drawn = [y for y in years if prog[y] is not None]
        active = drawn[-1] if drawn else None
        in_end = tt >= d["end_start"]
        acol = (UP if final[cur] >= 0 else DOWN) if in_end else (cols[active] if active else "#5b3dff")
        S.begin(acol, 1.0 if active else 0.4)
        S.story([(s, du, cols[y]) for s, du, y in d["plan"]], tt)
        logo = S.logo(d["symbol"]) if st.get("use_logo") else None
        S.header(f"{d['symbol']}  ·  {len(years)} JAHRE{'  ·  IN €' if d['eur'] else ''}", [d["title"]], tt,
                 d["question"], logo)

        ax = S.chart_axes()
        tlo, thi = -15.0, 35.0
        for y in drawn:
            xs, vs = series[y]
            n = max(2, int(len(xs) * prog[y]))
            tlo, thi = min(tlo, vs[:n].min()), max(thi, vs[:n].max())
        padv = (thi - tlo) * 0.10
        lo, hi = st["lim"].to(tlo - padv, thi + padv, snap)
        ax.set_xlim(-4, 368); ax.set_ylim(lo, hi)
        mpl = S.mpl
        ax.yaxis.set_major_locator(mpl.ticker.MaxNLocator(4))
        ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: pct(v, 0)))
        ax.set_xticks([0, 91, 182, 274]); ax.set_xticklabels(["JAN", "APR", "JUL", "OKT"])
        S.style_ticks(ax)
        ax.axhline(0, color="#ffffff", alpha=0.35, lw=1.6)
        for y in drawn:
            xs, vs = series[y]
            n = max(2, int(len(xs) * prog[y]))
            col = cols[y]
            if (y == active and not in_end) or y == cur:
                ax.fill_between(xs[:n], vs[:n], 0, color=col, alpha=0.10, lw=0)
                ax.plot(xs[:n], vs[:n], color=col, lw=6 if y == cur else 5, solid_capstyle="round",
                        solid_joinstyle="round", zorder=6, path_effects=S.glow(col, 5))
                px, py = xs[n - 1], vs[n - 1]
                pulse = 0.5 + 0.5 * np.sin(tt * 9)
                ax.scatter([px], [py], s=900 + 500 * pulse, color=col, alpha=0.15, zorder=7, lw=0)
                ax.scatter([px], [py], s=170, color="#ffffff", zorder=8, edgecolor=col, linewidth=5)
                ax.annotate(str(y), (px, py), xytext=(16, 0), textcoords="offset points", va="center",
                            color=INK, fontproperties=S.MONO, fontsize=22, zorder=9,
                            bbox=dict(boxstyle="round,pad=0.3,rounding_size=0.6", fc=col, ec="none"))
            else:
                ax.plot(xs[:n], vs[:n], color=col, lw=2.2, alpha=0.30, zorder=3)
                ax.annotate(str(y), (xs[n - 1], vs[n - 1]), xytext=(10, 0), textcoords="offset points",
                            va="center", color=col, alpha=0.6, fontproperties=S.MONO_M, fontsize=18)

        stand = f"STAND {d['last_date']:%d.%m.%Y}" if d.get("cur_running") and d.get("last_date") is not None else ""
        if active and not in_end:
            xs, vs = series[active]
            n = max(2, int(len(xs) * prog[active]))
            v = vs[n - 1]
            s0 = [p[0] for p in d["plan"] if p[2] == active][0]
            S.text(0.065, 0.295, str(active), S.ANTON, 64 * max(back((tt - s0) / 0.35), 0.01), cols[active])
            S.text(0.06, 0.185, pct(v), S.ANTON, 150, UP if v >= 0 else DOWN)
            sub = stand if (active == cur and prog[active] >= 1 and stand) else "SEIT JAHRESBEGINN"
            S.text(0.07, 0.15, sub, S.MONO_M, 22, DIM)
        if in_end:
            u = tt - d["end_start"]
            c = final[cur]
            col = UP if c >= 0 else DOWN
            S.text(0.065, 0.295, f"{cur}", S.ANTON, 64, CUR_COL)
            if stand:
                S.text(0.935, 0.305, stand, S.MONO_M, 22, DIM, ha="right")
            S.text(0.06, 0.185, pct(c), S.ANTON, 150 * max(back(u / 0.45), 0.01), col)
            a2 = ease((u - 0.35) / 0.4)
            if a2 > 0:
                S.pill(0.07, 0.15, f" {headline(final, years)} ", INK, col, 30, fp=S.GROT, alpha=a2, pad=0.35)
            step = min(0.178, 0.86 / len(years))
            fs = 21 if len(years) <= 5 else 17
            for k, y in enumerate(years):
                g = ease((u - 0.7 - k * 0.1) / 0.3)
                if g > 0:
                    hot = y == cur
                    S.pill(0.07 + k * step, 0.09, f"{y}\n{pct(final[y], 0)}", INK if hot else WHITE,
                           CUR_COL if hot else "#262a36", fs, alpha=g, pad=0.55)
        S.footer(st.get("handle"))

    def caption(self, d):
        years, final = d["years"], d["final"]
        cur = years[-1]
        name = short_name(d["name"])
        lines = [f"{name}: {len(years)} Jahre übereinander gelegt 📈", ""]
        lines.append(f"{cur}{' bisher' if d.get('cur_running') else ''}: {pct(final[cur]).replace('−', '-')} "
                     f"– {headline(final, years, readable=True)}.")
        lines += [f"{y}: {pct(final[y]).replace('−', '-')}" for y in years[:-1]]
        lines += ["", "Jede Linie = ein Kalenderjahr, Kurs seit Jahresbeginn (Vorjahresschluss = 0 %)"
                  + (", in Euro." if d["eur"] else ".")]
        if d.get("last_date") is not None:
            lines.append(f"Stand: {d['last_date']:%d.%m.%Y}.")
        lines.append("Saison-Muster einzelner Jahre sind kein Beweis für die Zukunft.")
        return lines


# ======================================================================= Format: Duell

PERIODS = [("1", "1 Jahr", 12), ("3", "3 Jahre", 36), ("5", "5 Jahre", 60), ("10", "10 Jahre", 120),
           ("20", "20 Jahre", 240)]


class Duell:
    key, label = "duell", "⚔️ Duell"
    hint = "2–3 Aktien oder Indizes laufen gegeneinander, alle starten bei 0 %. Am Ende: Was aus 10.000 € wurde."
    HOOK, RUN, END = 1.5, 8.0, 4.2

    def prepare(self, p):
        import pandas as pd
        from . import indices
        syms = [s for s in p.get("tickers", []) if s][:3]
        if len(syms) < 2:
            raise ValueError("Bitte zwei oder drei Kurse wählen")
        months = dict((k, m) for k, _, m in PERIODS).get(p.get("period", "10"), 120)
        eur = p.get("eur", False)
        start = pd.Timestamp.today() - pd.DateOffset(months=months)
        res = indices.compare(syms, start, eur)
        ser = res.get("series") or []
        if len(ser) < 2:
            raise ValueError("Für mindestens zwei der Kurse fehlen Daten")
        lines = []
        for k, s in enumerate(ser):
            v = pd.Series([x["value"] for x in s["data"]], index=pd.to_datetime([x["time"] for x in s["data"]]))
            v = v.resample("W-FRI").last().dropna()          # Wochenpunkte reichen fuer die Animation
            lines.append({"symbol": s["symbol"], "name": short_name(s["name"]), "tr": s.get("tr"),
                          "dates": v.index, "vals": v.values, "col": DUEL_COL[k], "final": float(v.values[-1])})
        t0, t1 = max(l["dates"][0] for l in lines), min(l["dates"][-1] for l in lines)
        span = (t1 - t0).days or 1
        for l in lines:
            l["x"] = np.array([(t - t0).days / span for t in l["dates"]])
        yrs = span / 365.25
        names = [l["name"] for l in lines]
        title = p.get("title") or " VS. ".join(n.upper() for n in names)
        q = p.get("question") or (f"{round(yrs)} JAHRE: WER GEWINNT?" if yrs >= 1.5 else "WER GEWINNT?")
        return {"format": "duell", "lines": lines, "t0": t0, "t1": t1, "years": yrs, "eur": eur,
                "title_lines": wrap(title.upper(), 16), "question": q.upper(), "shown": [l["symbol"] for l in lines],
                "price_only": any(not l["tr"] for l in lines)}

    def total(self, d):
        return self.HOOK + self.RUN + 0.2 + self.END

    def preview_times(self, d):
        return [self.HOOK + self.RUN * 0.6, self.total(d) - 0.2]

    def draw(self, S, d, tt, st, snap=False):
        lines = d["lines"]
        prog = ease((tt - self.HOOK) / self.RUN) if tt >= self.HOOK else 0.0
        end_start = self.HOOK + self.RUN + 0.2
        in_end = tt >= end_start
        cur_vals = []
        for l in lines:
            n = max(1, int(np.searchsorted(l["x"], prog, side="right")))
            cur_vals.append(l["vals"][n - 1])
        lead = int(np.argmax(cur_vals))
        S.begin(lines[lead]["col"], 1.0 if prog > 0 else 0.4)
        S.story([(self.HOOK, self.RUN, lines[lead]["col"]), (end_start, self.END, CUR_COL)], tt)
        tag = f"DUELL  ·  {len(lines)} KURSE{'  ·  IN €' if d['eur'] else ''}"
        S.header(tag, d["title_lines"], tt, d["question"])

        ax = S.chart_axes((0.14, 0.40, 0.73, 0.30))
        tlo, thi = -10.0, 20.0
        for l in lines:
            n = max(1, int(np.searchsorted(l["x"], prog, side="right")))
            tlo, thi = min(tlo, l["vals"][:n].min()), max(thi, l["vals"][:n].max())
        padv = (thi - tlo) * 0.10
        lo, hi = st["lim"].to(tlo - padv, thi + padv, snap)
        ax.set_xlim(-0.01, 1.12); ax.set_ylim(lo, hi)
        mpl = S.mpl
        ax.yaxis.set_major_locator(mpl.ticker.MaxNLocator(4))
        ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: pct(v, 0)))
        # Jahres-Ticks
        span = (d["t1"] - d["t0"]).days or 1
        yrs = list(range(d["t0"].year + 1, d["t1"].year + 1))
        step = max(1, math.ceil(len(yrs) / 5))
        ticks = [y for y in yrs[::step]]
        import pandas as pd
        ax.set_xticks([(pd.Timestamp(y, 1, 1) - d["t0"]).days / span for y in ticks])
        ax.set_xticklabels([str(y) for y in ticks])
        S.style_ticks(ax)
        ax.axhline(0, color="#ffffff", alpha=0.35, lw=1.6)
        order = np.argsort(cur_vals)
        for k in order:
            l = lines[k]
            n = max(1, int(np.searchsorted(l["x"], prog, side="right")))
            if prog <= 0:
                continue
            hot = k == lead
            ax.plot(l["x"][:n], l["vals"][:n], color=l["col"], lw=5 if hot else 4, solid_capstyle="round",
                    zorder=6 if hot else 5, path_effects=S.glow(l["col"], 4))
            px, py = l["x"][n - 1], l["vals"][n - 1]
            ax.scatter([px], [py], s=150, color="#ffffff", zorder=8, edgecolor=l["col"], linewidth=5)
            ax.annotate(l["name"][:10].upper(), (px, py), xytext=(14, 0), textcoords="offset points", va="center",
                        color=INK, fontproperties=S.MONO, fontsize=19, zorder=9,
                        bbox=dict(boxstyle="round,pad=0.3,rounding_size=0.6", fc=l["col"], ec="none"))

        # Tafel unten: aktueller Stand je Kurs, sortiert
        rank = sorted(range(len(lines)), key=lambda k: -cur_vals[k])
        if not in_end:
            for r, k in enumerate(rank):
                l = lines[k]
                y = 0.30 - r * 0.065
                self._row(S, l, y, pct(cur_vals[k]), UP if cur_vals[k] >= 0 else DOWN, st)
            if prog > 0:
                cur_t = d["t0"] + pd.Timedelta(days=prog * span)
                S.text(0.07, 0.30 - len(lines) * 0.065 + 0.01, f"STAND {cur_t:%m/%Y}", S.MONO_M, 22, DIM)
        else:
            u = tt - end_start
            win = lines[rank[0]]
            a = ease(u / 0.4)
            S.text(0.065, 0.335, "AUS 10.000 € WURDEN", S.MONO_M, 26, DIM, alpha=a)
            for r, k in enumerate(rank):
                g = ease((u - 0.2 - r * 0.25) / 0.35)
                if g <= 0:
                    continue
                l = lines[k]
                val = 10000 * (1 + l["final"] / 100)
                self._row(S, l, 0.29 - r * 0.065, euro(val), WHITE if r else CUR_COL, st, alpha=g,
                          size=64 if r == 0 else 50)
            g = ease((u - 1.1) / 0.4)
            if g > 0:
                S.pill(0.07, 0.29 - len(lines) * 0.065 + 0.012, f" SIEGER: {win['name'].upper()}  {pct(win['final'], 0)} ",
                       INK, CUR_COL, 30, fp=S.GROT, alpha=g, pad=0.35)
        note = "ohne Dividenden" if d["price_only"] else "mit Dividenden"
        S.text(0.935, 0.065, f"Kursentwicklung {note}{', in Euro' if d['eur'] else ''}", S.GROT_M, 17, DIM,
               ha="right", alpha=0.8)
        S.footer(st.get("handle"))

    def _row(self, S, l, y, value, vcol, st, alpha=1.0, size=56):
        x = 0.07
        if st.get("use_logo") and is_stock(l["symbol"]):
            img = S.logo(l["symbol"])
            if img is not None:
                S.logo_tile(img, [x, y - 0.024, 0.075, 0.042], pad=0.16)
                x += 0.09
        S.fig.add_artist(S.mpl.patches.Ellipse((x + 0.012, y), 0.02, 0.02 * W / H, transform=S.fig.transFigure,
                                               color=l["col"], alpha=alpha))
        S.text(x + 0.035, y, l["name"].upper(), S.ANTON, 46, WHITE, va="center", alpha=alpha)
        S.text(0.93, y, value, S.ANTON, size, vcol, va="center", ha="right", alpha=alpha)

    def caption(self, d):
        lines = sorted(d["lines"], key=lambda l: -l["final"])
        yrs = round(d["years"])
        out = [f"{' vs. '.join(l['name'] for l in d['lines'])} – {yrs} Jahre im direkten Vergleich ⚔️", ""]
        for l in lines:
            out.append(f"{l['name']}: {pct(l['final']).replace('−', '-')} → aus 10.000 € wurden {euro(10000 * (1 + l['final'] / 100))}")
        out += ["", f"Zeitraum: {d['t0']:%m/%Y} bis {d['t1']:%m/%Y}, Kursentwicklung "
                f"{'ohne' if d['price_only'] else 'mit'} Dividenden{', in Euro' if d['eur'] else ''}.",
                "Vergangene Renditen sind keine Garantie für die Zukunft."]
        return out


# ======================================================================= Format: Mythos

MYTHS = {
    "sellinmay": {"claim": "SELL IN MAY AND GO AWAY", "short": "Sell in May",
                  "what": "Mai bis Oktober läuft schlechter als November bis April",
                  "bar": "Vorsprung Nov–Apr gegenüber Mai–Okt"},
    "dezember": {"claim": "DER DEZEMBER IST IMMER GRÜN", "short": "Dezember-Rally",
                 "what": "Der Dezember endet im Plus", "bar": "Rendite im Dezember"},
    "januar": {"claim": "WIE DER JANUAR, SO DAS JAHR", "short": "Januar-Barometer",
               "what": "Rest des Jahres (Feb–Dez) geht in dieselbe Richtung wie der Januar",
               "bar": "Rendite Feb–Dez (Farbe: Januar)"},
    "nachminus": {"claim": "NACH EINEM MINUS-JAHR KOMMT EIN PLUS-JAHR", "short": "Erholung nach Minus-Jahr",
                  "what": "Das Jahr nach einem Minus-Jahr endet im Plus", "bar": "Rendite im Folgejahr"},
    "september": {"claim": "IM SEPTEMBER FÄLLT DIE BÖRSE", "short": "September-Effekt",
                  "what": "Der September endet im Minus", "bar": "Rendite im September"},
    "rekord": {"claim": "NACH EINEM REKORDHOCH GEHT ES BERGAB", "short": "Nie am Rekordhoch kaufen",
               "what": "Endet ein Jahr auf Rekordhoch, schließt das Folgejahr im Minus",
               "bar": "Rendite im Jahr nach Rekord-Jahresende"},
    "zehnjahre": {"claim": "WER 10 JAHRE HÄLT, VERLIERT NIE", "short": "10 Jahre halten",
                  "what": "Über jeden 10-Jahres-Zeitraum (Jahresende bis Jahresende) steht ein Plus",
                  "bar": "Rendite über 10 Jahre bis Jahresende"},
}


def _month_end(s):
    try:
        return s.resample("ME").last().dropna()
    except ValueError:                      # aeltere pandas-Versionen
        return s.resample("M").last().dropna()


def myth_rows(s, key, n_years):
    """Je Jahr: Wert fuer den Balken und ob die Behauptung stimmte. Nur abgeschlossene Monate zaehlen."""
    m = _month_end(s)
    last = s.index[-1]
    m = m[m.index.to_period("M") < last.to_period("M")]       # laufender Monat ist noch nicht fertig
    rows = []
    at = lambda y, mo: m[(m.index.year == y) & (m.index.month == mo)]
    for y in sorted(set(m.index.year)):
        if key == "sellinmay":
            o0, a, o = at(y - 1, 10), at(y, 4), at(y, 10)
            if o0.empty or a.empty or o.empty:
                continue
            winter = (a.iloc[0] / o0.iloc[0] - 1) * 100
            summer = (o.iloc[0] / a.iloc[0] - 1) * 100
            rows.append({"year": y, "value": winter - summer, "hit": summer < winter, "a": summer, "b": winter})
        elif key == "dezember":
            n, d = at(y, 11), at(y, 12)
            if n.empty or d.empty:
                continue
            r = (d.iloc[0] / n.iloc[0] - 1) * 100
            rows.append({"year": y, "value": r, "hit": r > 0})
        elif key == "januar":
            dp, j, dz = at(y - 1, 12), at(y, 1), at(y, 12)
            if dp.empty or j.empty or dz.empty:
                continue
            jr = (j.iloc[0] / dp.iloc[0] - 1) * 100
            rest = (dz.iloc[0] / j.iloc[0] - 1) * 100
            rows.append({"year": y, "value": rest, "hit": (jr > 0) == (rest > 0), "jan": jr})
        elif key == "nachminus":
            p2, p1, c = at(y - 2, 12), at(y - 1, 12), at(y, 12)
            if p2.empty or p1.empty or c.empty:
                continue
            prev = (p1.iloc[0] / p2.iloc[0] - 1) * 100
            if prev >= 0:
                continue
            r = (c.iloc[0] / p1.iloc[0] - 1) * 100
            rows.append({"year": y, "value": r, "hit": r > 0, "prev": prev})
        elif key == "september":
            a, sp = at(y, 8), at(y, 9)
            if a.empty or sp.empty:
                continue
            r = (sp.iloc[0] / a.iloc[0] - 1) * 100
            rows.append({"year": y, "value": r, "hit": r < 0})
        elif key == "rekord":
            p1, c = at(y - 1, 12), at(y, 12)
            if p1.empty or c.empty:
                continue
            before = m[m.index <= p1.index[0]]
            if len(before) < 24 or p1.iloc[0] < before.max() * 0.999:      # Jahresende nicht auf Rekordhoch
                continue
            r = (c.iloc[0] / p1.iloc[0] - 1) * 100
            rows.append({"year": y, "value": r, "hit": r < 0})
        elif key == "zehnjahre":
            b, c = at(y - 10, 12), at(y, 12)
            if b.empty or c.empty:
                continue
            r = (c.iloc[0] / b.iloc[0] - 1) * 100
            rows.append({"year": y, "value": r, "hit": r > 0})
    if key in ("nachminus", "rekord"):
        return rows[-25:]                 # wenige Faelle: alle nehmen, die es gibt (max. 25 Balken)
    return rows[-n_years:]


def verdict(hits, n):
    q = hits / n if n else 0
    if q >= 0.75:
        return "STIMMT MEISTENS", UP
    if q <= 0.4:
        return "STIMMT NICHT", DOWN
    return "EHER MÜNZWURF", CUR_COL


class Mythos:
    key, label = "mythos", "🔎 Mythos-Check"
    hint = "Eine Börsenweisheit, Jahr für Jahr mit echten Kursen geprüft. Balken grün = stimmte, rot = stimmte nicht."
    HOOK, RUN, END = 2.0, 7.0, 4.2

    def prepare(self, p):
        from . import indices
        key = p.get("myth") if p.get("myth") in MYTHS else "sellinmay"
        sym = (p.get("ticker") or "^GSPC").upper()
        eur = p.get("eur", False)
        s = indices.close(sym, eur)
        if s.empty:
            raise ValueError(f"Keine Kurse für {sym}")
        n_years = int(p.get("n_years") or 20)
        rows = myth_rows(s, key, n_years)
        if len(rows) < 4:
            raise ValueError("Zu wenige abgeschlossene Jahre für diesen Test")
        hits = sum(r["hit"] for r in rows)
        meta = MYTHS[key]
        name = short_name(indices.name_of(sym))
        return {"format": "mythos", "myth": key, "meta": meta, "symbol": sym, "name": name, "rows": rows, "hits": hits,
                "eur": eur, "title_lines": wrap(p.get("title", "").upper() or meta["claim"], 16, 3),
                "question": (p.get("question") or "STIMMT DAS?").upper(), "shown": [sym],
                "tr": (indices.META.get(sym, {}) or {}).get("tr")}

    def total(self, d):
        return self.HOOK + self.RUN + 0.2 + self.END

    def preview_times(self, d):
        return [self.HOOK + self.RUN * 0.6, self.total(d) - 0.2]

    def draw(self, S, d, tt, st, snap=False):
        rows = d["rows"]
        n = len(rows)
        per = self.RUN / n
        shown = sum(1 for k in range(n) if tt >= self.HOOK + k * per)
        end_start = self.HOOK + self.RUN + 0.2
        in_end = tt >= end_start
        hits_now = sum(r["hit"] for r in rows[:shown])
        v_text, v_col = verdict(d["hits"], n)
        S.begin(v_col if in_end else ("#5b3dff" if not shown else (UP if rows[shown - 1]["hit"] else DOWN)),
                1.0 if shown else 0.4)
        S.story([(self.HOOK, self.RUN, CUR_COL), (end_start, self.END, v_col)], tt)
        S.header(f"MYTHOS-CHECK  ·  {d['name'].upper()}{'  ·  IN €' if d['eur'] else ''}", d["title_lines"], tt,
                 d["question"])

        ax = S.chart_axes((0.12, 0.40, 0.80, 0.24))
        vals = np.array([r["value"] for r in rows])
        lim = max(5.0, float(np.abs(vals).max()) * 1.15)
        ax.set_xlim(-0.7, n - 0.3); ax.set_ylim(-lim, lim)
        if d["myth"] == "zehnjahre":          # fast nur Plus: Platz nicht an leere Minus-Haelfte verschenken
            ax.set_ylim(min(-0.12 * lim, float(vals.min()) * 1.15), lim)
        ax.yaxis.set_major_locator(S.mpl.ticker.MaxNLocator(4))
        ax.yaxis.set_major_formatter(S.mpl.ticker.FuncFormatter(lambda v, _: pct(v, 0)))
        step = max(1, math.ceil(n / 6))
        ax.set_xticks(list(range(0, n, step)))
        ax.set_xticklabels([str(rows[k]["year"]) for k in range(0, n, step)])
        S.style_ticks(ax, 19)
        ax.axhline(0, color="#ffffff", alpha=0.4, lw=1.6)
        for k in range(shown):
            r = rows[k]
            g = back((tt - self.HOOK - k * per) / 0.3) if not snap else 1.0
            col = UP if r["hit"] else DOWN
            ax.bar(k, r["value"] * g, width=0.7, color=col, alpha=0.95 if k == shown - 1 and not in_end else 0.75,
                   zorder=5)
        S.text(0.12, 0.352, MYTHS[d["myth"]]["bar"].upper(), S.MONO_M, 19, DIM)

        if not in_end:
            if shown:
                r = rows[shown - 1]
                S.text(0.065, 0.305, str(r["year"]), S.ANTON, 64, UP if r["hit"] else DOWN)
                S.text(0.935, 0.305, "STIMMT" if r["hit"] else "STIMMT NICHT", S.ANTON, 64,
                       UP if r["hit"] else DOWN, ha="right")
            S.text(0.065, 0.19, f"{hits_now}", S.ANTON, 140, UP)
            S.text(0.30, 0.205, f"VON {shown} JAHREN\nSTIMMT ES", S.MONO, 30, WHITE, linespacing=1.3)
        else:
            u = tt - end_start
            S.text(0.065, 0.24, f"{d['hits']} VON {n}", S.ANTON, 150 * max(back(u / 0.45), 0.01), v_col)
            S.text(0.07, 0.205, "JAHREN STIMMTE ES", S.MONO, 30, WHITE)
            a2 = ease((u - 0.4) / 0.4)
            if a2 > 0:
                S.pill(0.07, 0.16, f" URTEIL: {v_text} ", INK, v_col, 34, fp=S.GROT, alpha=a2, pad=0.35)
            a3 = ease((u - 0.9) / 0.4)
            if a3 > 0:
                S.text(0.07, 0.128, self._fact(d), S.MONO_M, 22, DIM, alpha=a3, linespacing=1.4, va="top")
        first, last = rows[0]["year"], rows[-1]["year"]
        S.text(0.935, 0.065, f"{d['name']} {first}–{last}, Monatsschlusskurse"
               f"{', ohne Dividenden' if not d['tr'] else ''}{', in Euro' if d['eur'] else ''}",
               S.GROT_M, 17, DIM, ha="right", alpha=0.8)
        S.footer(st.get("handle"))

    def _fact(self, d):
        rows = d["rows"]
        avg = lambda xs: sum(xs) / len(xs) if xs else 0
        if d["myth"] == "sellinmay":
            return (f"Ø Mai–Okt {pct(avg([r['a'] for r in rows]))}\n"
                    f"Ø Nov–Apr {pct(avg([r['b'] for r in rows]))}")
        if d["myth"] == "nachminus":
            return f"Ø Rendite im Folgejahr {pct(avg([r['value'] for r in rows]))}\n{len(rows)} Minus-Jahre seit {rows[0]['year'] - 1}"
        if d["myth"] == "rekord":
            return f"Ø Rendite im Folgejahr {pct(avg([r['value'] for r in rows]))}"
        if d["myth"] == "zehnjahre":
            worst = min(rows, key=lambda r: r["value"])
            return f"Schlechteste 10 Jahre: {worst['year'] - 10}–{worst['year']} {pct(worst['value'], 0)}"
        if d["myth"] == "september":
            return f"Ø September {pct(avg([r['value'] for r in rows]))}"
        return f"Ø {pct(avg([r['value'] for r in rows]))} je Jahr"

    def caption(self, d):
        n, h = len(d["rows"]), d["hits"]
        v_text, _ = verdict(h, n)
        rows = d["rows"]
        out = [f"„{d['meta']['short']}“ – nachgerechnet 🔎", "",
               f"Behauptung: {d['meta']['what']}.",
               f"Geprüft mit dem {d['name']}, {rows[0]['year']}–{rows[-1]['year']}: stimmte in {h} von {n} Jahren "
               f"({round(h / n * 100)} %).", f"Urteil: {v_text.capitalize()}.", "",
               self._fact(d).replace("\n", " · ") + ".",
               f"Grundlage: Monatsschlusskurse{', ohne Dividenden' if not d['tr'] else ''}{', in Euro' if d['eur'] else ''}.",
               "Eine Regel, die in der Vergangenheit oft stimmte, muss es in Zukunft nicht."]
        return out


# ======================================================================= Format: Fonds (13F)

def _funds_line(funds, max_len=44):
    """Fondsnamen bis zur Laenge; Rest als '+N' (nie mitten im Namen abschneiden)."""
    names = [f.split(" (")[0] for f in funds]
    out = ""
    for k, n in enumerate(names):
        nxt = (out + ", " if out else "") + n
        if len(nxt) > max_len and out:
            return f"{out} +{len(names) - k}"
        out = nxt
    return out


class Fonds:
    key, label = "fonds", "🏦 Hedgefonds-Käufe"
    hint = "Die größten Neukäufe der Hedgefonds aus deiner 13F-Liste im letzten Quartal, mit Logos."
    HOOK, ROW, HOLD, END = 1.8, 1.0, 0.8, 4.0

    def prepare(self, p):
        from . import f13
        mv = f13.market_moves()
        if not mv or not mv.get("new"):
            raise ValueError("Keine 13F-Daten – erst unter Smart Money › Fonds laden")
        agg = {}
        for it in mv["new"]:
            k = it["ticker"] or it["name"]
            a = agg.setdefault(k, {"ticker": it["ticker"], "name": it["name"], "funds": [], "weight": 0.0})
            if it["fund"] not in a["funds"]:
                a["funds"].append(it["fund"])
            a["weight"] += it["weight"]
        rows = sorted(agg.values(), key=lambda r: (-len(r["funds"]), -r["weight"]))[:int(p.get("n_rows") or 6)]
        for r in rows:
            r["short"] = short_name((r["name"] or r["ticker"] or "").title() if r["name"] and r["name"].isupper()
                                    else (r["name"] or r["ticker"]))
        per = mv["period"]
        q = (per.month - 1) // 3 + 1
        filed = max(f for _, f in mv["reported"]) if mv.get("reported") else None
        return {"format": "fonds", "rows": rows, "period": per, "q_label": f"Q{q} {per.year}", "filed": filed,
                "n_funds": len(mv["reported"]), "eur": False,
                "title_lines": wrap((p.get("title") or "DAS HABEN HEDGEFONDS GEKAUFT").upper(), 16),
                "question": (p.get("question") or f"NEU IM DEPOT · Q{q} {per.year}").upper(),
                "shown": [r["ticker"] for r in rows if r["ticker"]]}

    def total(self, d):
        return self.HOOK + len(d["rows"]) * self.ROW + self.HOLD + self.END

    def preview_times(self, d):
        return [self.HOOK + len(d["rows"]) * self.ROW + 0.3, self.total(d) - 0.2]

    def draw(self, S, d, tt, st, snap=False):
        rows = d["rows"]
        end_start = self.HOOK + len(rows) * self.ROW + self.HOLD
        in_end = tt >= end_start
        S.begin(CUR_COL if in_end else "#00e5ff", 1.0)
        S.story([(self.HOOK, len(rows) * self.ROW + self.HOLD, "#00e5ff"), (end_start, self.END, CUR_COL)], tt)
        S.header(f"13F  ·  {d['q_label']}  ·  {d['n_funds']} FONDS", d["title_lines"], tt, d["question"])
        top = 0.665
        for k, r in enumerate(rows):
            g = 1.0 if snap else back((tt - self.HOOK - k * self.ROW) / 0.4)
            if tt < self.HOOK + k * self.ROW and not snap:
                continue
            y = top - k * 0.078
            a = 0.35 if in_end else 1.0
            x = 0.07
            img = S.logo(r["ticker"]) if (st.get("use_logo") and r["ticker"]) else None
            if img is not None:
                S.logo_tile(img, [x, y - 0.03, 0.09, 0.055], pad=0.15)
            else:
                lab = r["ticker"] or "".join(w[0] for w in r["short"].split()[:2]).upper()
                S.pill(x + 0.045, y, lab[:5], INK, "#ffffff", 20, alpha=a, ha="center", pad=0.5)
            nm = r["short"].upper()
            S.text(0.19, y + 0.008, nm, S.ANTON, S.fit(nm, S.ANTON, 50, 0.56, 30) * max(g, 0.01), WHITE, va="center",
                   alpha=a)
            S.text(0.19, y - 0.022, _funds_line(r["funds"]), S.GROT_M, 19, DIM, va="center", alpha=a)
            nf = len(r["funds"])
            S.pill(0.93, y, f"{nf} FONDS" if nf > 1 else "1 FONDS", INK, CUR_COL if nf > 1 else "#ffffff", 22,
                   alpha=a, ha="right", pad=0.4)
        if in_end:
            u = tt - end_start
            g = ease(u / 0.4)
            S.pill(0.07, 0.205, " ACHTUNG ", INK, DOWN, 40, fp=S.ANTON, alpha=g, pad=0.3)
            g2 = ease((u - 0.4) / 0.4)
            S.text(0.07, 0.178, "Gemeldet bis zu 45 Tage nach\nQuartalsende – die Fonds können\nlängst wieder verkauft haben.",
                   S.GROT, 32, WHITE, alpha=g2, linespacing=1.25, va="top")
        S.text(0.935, 0.065, f"Quelle: SEC-13F-Meldungen{', zuletzt ' + d['filed'].strftime('%d.%m.%Y') if d['filed'] else ''}",
               S.GROT_M, 17, DIM, ha="right", alpha=0.8)
        S.footer(st.get("handle"), "Daten: SEC")

    def caption(self, d):
        out = [f"Das haben Hedgefonds im {d['q_label']} neu gekauft 🏦", ""]
        for r in d["rows"]:
            out.append(f"{r['short']}{' (' + r['ticker'] + ')' if r['ticker'] else ''}: {', '.join(r['funds'])}")
        out += ["", f"Quelle: 13F-Meldungen von {d['n_funds']} Fonds an die US-Börsenaufsicht SEC.",
                "Wichtig: Die Meldungen kommen bis zu 45 Tage nach Quartalsende – die Fonds können längst wieder "
                "verkauft haben. Erfasst sind nur US-Aktienpositionen, keine Leerverkäufe."]
        return out


FORMATS = {f.key: f for f in (Jahre(), Duell(), Mythos(), Fonds())}
HASHTAGS = "#aktien #börse #investieren #etf #finanzen #aktienmarkt #nachgerechnet"


def caption(fmt_key: str, d: dict) -> str:
    lines = FORMATS[fmt_key].caption(d)
    disc = disclosure(d.get("shown", []))
    lines += ["", disc] if disc else [""]
    lines += ["Keine Anlageberatung.", "", HASHTAGS]
    return "\n".join(l for l in lines).replace("\n\n\n", "\n\n").strip()


# ======================================================================= Rendern

def _state(opts):
    return {"lim": Smooth(-15.0, 35.0), "handle": opts.get("handle", ""), "use_logo": opts.get("use_logo", True)}


def preview_png(fmt_key: str, d: dict, which: str = "end", opts=None, width: int = 540) -> bytes:
    """Ein Standbild (Mitte oder Ende) – in ~1 s, damit man vor dem langen Rendern sieht, ob alles passt."""
    fmt = FORMATS[fmt_key]
    S = Stage()
    try:
        times = fmt.preview_times(d)
        tt = times[0] if which == "mitte" else times[-1]
        st = _state(opts or {})
        fmt.draw(S, d, tt, st, snap=True)
        img = S.rgb_frame()
    finally:
        S.close()
    from PIL import Image
    im = Image.fromarray(img)
    if width and width < W:
        im = im.resize((width, int(H * width / W)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def render_format(fmt_key: str, d: dict, out_path: str, *, opts=None, progress=None, fps: int = FPS,
                  music=None) -> str:
    fmt = FORMATS[fmt_key]
    opts = opts or {}
    exe = _ffmpeg()
    if not exe:
        raise RuntimeError("ffmpeg fehlt – bitte start.bat neu starten (installiert imageio-ffmpeg)")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    total = fmt.total(d)
    nf = int(total * fps)
    S = Stage()
    st = _state(opts)
    tmp = out_path[:-4] + ".tmp.mp4"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    p = subprocess.Popen([exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                          "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        for i in range(nf):
            fmt.draw(S, d, i / fps, st)
            p.stdin.write(S.rgb_frame().tobytes())
            if progress:
                progress((i + 1) / nf * (0.95 if music else 1.0))
        p.stdin.close()
        err = p.stderr.read().decode(errors="replace")
        p.stderr.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg: {err[-300:]}")
    except Exception:
        p.kill()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        S.close()
    if music and os.path.exists(music):
        mixed = out_path[:-4] + ".mix.mp4"
        r = subprocess.run([exe, "-y", "-loglevel", "error", "-i", tmp, "-stream_loop", "-1", "-i", music,
                            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                            "-af", f"afade=t=in:d=0.4,afade=t=out:st={max(0, total - 1.5):.2f}:d=1.5",
                            "-shortest", "-movflags", "+faststart", mixed],
                           capture_output=True, creationflags=flags)
        if r.returncode == 0:
            os.replace(mixed, tmp)
        elif os.path.exists(mixed):
            os.remove(mixed)
    os.replace(tmp, out_path)
    return out_path


def render(data: dict, out_path: str, *, title: str = "", question: str = "", handle: str = "",
           logo_path=None, progress=None, fps: int = FPS, eur: bool = False) -> str:
    """Kurzform fuer das Jahres-Format (aeltere Aufrufe)."""
    p = {"ticker": data["symbol"], "years": data["years"], "eur": data.get("eur", eur), "title": title,
         "question": question}
    d = FORMATS["jahre"].prepare(p)
    return render_format("jahre", d, out_path, opts={"handle": handle, "use_logo": bool(logo_path)},
                         progress=progress, fps=fps)


# ======================================================================= Dateien & Hintergrund-Job

def file_name(fmt_key: str, d: dict) -> str:
    if fmt_key == "jahre":
        base = f"{d['symbol']}_{d['years'][0]}-{d['years'][-1]}"
    elif fmt_key == "duell":
        base = "duell_" + "-".join(l["symbol"] for l in d["lines"])
    elif fmt_key == "mythos":
        base = f"mythos_{d['myth']}_{d['symbol']}"
    else:
        base = f"fonds_{d['q_label'].replace(' ', '')}"
    safe = re.sub(r"[^A-Za-z0-9.\-_]", "", base.replace("^", ""))
    return f"{safe}_{datetime.now():%Y%m%d-%H%M%S}.mp4"


def list_reels() -> list:
    if not os.path.isdir(REEL_DIR):
        return []
    out = []
    for f in os.listdir(REEL_DIR):
        if f.endswith(".mp4") and not (f.endswith(".tmp.mp4") or f.endswith(".mix.mp4")):
            p = os.path.join(REEL_DIR, f)
            cap = p[:-4] + ".txt"
            out.append({"name": f, "size_mb": os.path.getsize(p) / 1e6, "time": datetime.fromtimestamp(os.path.getmtime(p)),
                        "caption": open(cap, encoding="utf-8").read() if os.path.exists(cap) else ""})
    return sorted(out, key=lambda r: r["time"], reverse=True)


def list_music() -> list:
    if not os.path.isdir(MUSIC_DIR):
        return []
    return sorted(f for f in os.listdir(MUSIC_DIR) if f.lower().endswith((".mp3", ".m4a", ".wav", ".aac")))


def start(app, fmt_key: str, params: dict, opts: dict, music=None) -> bool:
    """Rendern im Hintergrund (je nach Rechner 1–2 Minuten). Fortschritt in JOB."""
    if JOB["running"]:
        return False
    JOB.update(running=True, progress=0.0, msg="Bereite vor …", file=None, ticker=params.get("ticker"), error=None)

    def run():
        with app.app_context():
            t0 = time.time()
            try:
                d = FORMATS[fmt_key].prepare(params)
                out = os.path.join(REEL_DIR, file_name(fmt_key, d))
                JOB["msg"] = "Zeichne Video …"
                mp = os.path.join(MUSIC_DIR, music) if music else None
                render_format(fmt_key, d, out, opts=opts, music=mp, progress=lambda f: JOB.update(progress=f))
                with open(out[:-4] + ".txt", "w", encoding="utf-8") as f:
                    f.write(caption(fmt_key, d))
                JOB.update(file=os.path.basename(out), msg=f"Fertig in {time.time() - t0:.0f} s")
            except Exception as e:
                JOB.update(error=str(e), msg=f"Fehler: {e}")
            finally:
                JOB["running"] = False
    threading.Thread(target=run, daemon=True).start()
    return True
