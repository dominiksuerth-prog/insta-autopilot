"""Einheitliche Titelbilder für die Reels (Vorschaubild im Profil-Raster).

Gleicher Aufbau für alle Reels, eine feste Farbe je Format:
  5 JAHRE = Gelb · DUELL = Cyan · MYTHOS-CHECK = Pink
Alles Wichtige liegt im mittleren 3:4-Bereich, weil Instagram das Bild im Raster so zuschneidet.
Kein Ergebnis auf dem Bild – das Titelbild soll neugierig machen, nicht die Pointe verraten.
"""
import io

import numpy as np

from .reel import H, INK, W, Stage, short_name

STYLE = {
    "jahre": ("5 JAHRE", "#ffd60a"),
    "duell": ("DUELL", "#00e5ff"),
    "mythos": ("MYTHOS-CHECK", "#ff4ecd"),
}
# sichtbarer Bereich im Raster (3:4 aus 9:16): y = 0.125 … 0.875 der Bildhöhe
SAFE_TOP, SAFE_BOTTOM = 0.84, 0.16


def _tag(S, label, col, y=0.79):
    S.pill(0.5, y, f"  {label}  ", INK, col, 34, fp=S.ANTON, ha="center", pad=0.35)


def _sticker(S, text, y):
    S.pill(0.5, y, f" {text} ", INK, "#ffffff", 50, fp=S.ANTON, ha="center", pad=0.3)


def _handle(S, handle):
    if handle:
        S.text(0.5, SAFE_BOTTOM + 0.012, handle, S.GROT, 30, ha="center", alpha=0.75)


def _big_name(S, name, y, max_w=0.86, size=190):
    size = S.fit(name, S.ANTON, size, max_w)
    S.text(0.5, y, name, S.ANTON, size, ha="center", va="center")


def _logo(S, sym, rect, use_logo):
    img = S.logo(sym) if use_logo else None
    if img is not None:
        S.logo_tile(img, rect)
        return True
    return False


def _jahre(S, d, col, use_logo):
    name = short_name(d["name"]).upper()
    has_logo = _logo(S, d["symbol"], [0.39, 0.585, 0.22, 0.124], use_logo)
    _big_name(S, name, 0.50 if has_logo else 0.57)
    cur = d["years"][-1]
    _sticker(S, d.get("question") or f"WIE LIEF {cur}?", 0.405 if has_logo else 0.45)
    # Andeutung der Vorjahreslinien – ohne Achsen, Zahlen und ohne das aktuelle Jahr (keine Pointe verraten)
    ax = S.fig.add_axes([0.1, 0.205, 0.8, 0.15])
    ax.axis("off")
    for y in d["years"][:-1]:
        x, v = d["series"][y]
        ax.plot(x, v, color="#ffffff", alpha=0.22, lw=3, solid_capstyle="round")
    ax.set_xlim(0, 366)


def _duell(S, d, col, use_logo):
    lines = d["lines"][:3]
    n = len(lines)
    xs = {2: [0.27, 0.73], 3: [0.18, 0.5, 0.82]}[n]
    tile = 0.2 if n == 2 else 0.17
    any_logo = False
    for l, x in zip(lines, xs):
        any_logo |= _logo(S, l["symbol"], [x - tile / 2, 0.585, tile, tile * W / H], use_logo)
    y_names = 0.53 if any_logo else 0.6
    names = [short_name(l["name"]).upper() for l in lines]
    size = min(S.fit(nm, S.ANTON, 96 if n == 2 else 74, 0.4 if n == 2 else 0.29) for nm in names)
    for nm, x in zip(names, xs):
        S.text(x, y_names, nm, S.ANTON, size, ha="center", va="center")
    if n == 2:
        S.text(0.5, (0.64 if any_logo else 0.6), "VS", S.ANTON, 90, col, ha="center", va="center")
    q = d.get("question") or "WER GEWINNT?"
    _sticker(S, q, y_names - 0.085)
    S.text(0.5, y_names - 0.16, "AUS 10.000 € WURDEN …", S.GROT, 34, "#ffffff",
           ha="center", va="center", alpha=0.8)


def _mythos(S, d, col, use_logo):
    claim = d["meta"]["claim"]
    words, rows, cur = claim.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 13:
            rows.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    rows.append(cur)
    rows = rows[:4]
    size = min(S.fit(r, S.ANTON, 130, 0.86) for r in rows)
    lh = size * 1.3 / H
    top = 0.62 + (len(rows) - 1) * lh / 2
    for k, r in enumerate(rows):
        S.text(0.5, top - k * lh, r, S.ANTON, size, ha="center", va="center")
    y = top - len(rows) * lh - 0.03
    _sticker(S, d.get("question") or "STIMMT DAS?", y)
    S.text(0.5, y - 0.075, f"GEPRÜFT AM {short_name(d['name']).upper()}", S.GROT, 36, "#ffffff",
           ha="center", va="center", alpha=0.8)


def render(fmt: str, d: dict, opts=None) -> bytes:
    """Titelbild als JPEG (1080x1920)."""
    opts = opts or {}
    label, col = STYLE[fmt]
    S = Stage()
    try:
        S.begin(col, 1.6)
        _tag(S, label, col)
        {"jahre": _jahre, "duell": _duell, "mythos": _mythos}[fmt](S, d, col, opts.get("use_logo", True))
        _handle(S, opts.get("handle", ""))
        img = S.rgb_frame()
    finally:
        S.close()
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img)).convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ======================================================================= Variante B: Magazin-Block

def _wrap(s, width):
    words, rows, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            rows.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    rows.append(cur)
    return rows


def _block_text(S, rows, top, bottom, color=INK, max_size=230):
    """Zeilen linksbündig in den Farbblock setzen, so groß wie Breite und Höhe es erlauben."""
    size = min(S.fit(r, S.ANTON, max_size, 0.86) for r in rows)
    lh = size * 1.32 / H
    while len(rows) * lh > (top - bottom) and size > 60:
        size *= 0.92
        lh = size * 1.32 / H
    y = bottom + (len(rows) - 1) * lh + lh * 0.42   # an die Unterkante des Blocks setzen
    for r in rows:
        S.text(0.07, y, r, S.ANTON, size, color, va="center")
        y -= lh


def render_block(fmt: str, d: dict, opts=None) -> bytes:
    opts = opts or {}
    label, col = STYLE[fmt]
    S = Stage()
    try:
        S.begin(col, 0.5)
        y0, y1 = 0.47, SAFE_TOP + 0.005               # Farbblock: obere Hälfte des sichtbaren Bereichs
        S.fig.add_artist(S.mpl.patches.Rectangle((0, y0), 1, y1 - y0, transform=S.fig.transFigure,
                                                 fc=col, ec="none", zorder=0))
        S.text(0.07, y1 - 0.035, label, S.GROT, 34, INK, va="center")
        S.text(0.93, y1 - 0.035, "NACHGERECHNET", S.GROT, 34, INK, ha="right", va="center", alpha=0.6)
        use_logo = opts.get("use_logo", True)
        if fmt == "jahre":
            _block_text(S, [short_name(d["name"]).upper()], y1 - 0.08, y0 + 0.03)
            question = d.get("question") or f"WIE LIEF {d['years'][-1]}?"
            syms = [d["symbol"]]
            sub = f"{len(d['years'])} Jahre übereinander, in Euro"
        elif fmt == "duell":
            names = [short_name(l["name"]).upper() for l in d["lines"][:3]]
            rows = []
            for k, n in enumerate(names):
                rows += _wrap(n, 12) + (["VS."] if k < len(names) - 1 else [])
            _block_text(S, rows, y1 - 0.08, y0 + 0.025, max_size=170)
            question = d.get("question") or "WER GEWINNT?"
            syms = [l["symbol"] for l in d["lines"][:3]]
            sub = "Aus 10.000 € wurden …"
        else:
            _block_text(S, _wrap(d["meta"]["claim"], 13), y1 - 0.08, y0 + 0.025, max_size=150)
            question = d.get("question") or "STIMMT DAS?"
            syms = []
            sub = f"Jahr für Jahr geprüft am {short_name(d['name'])}"
        # dunkler Teil: Logos, große Frage, Unterzeile
        x = 0.07
        if use_logo:
            for s in syms:
                img = S.logo(s)
                if img is not None:
                    S.logo_tile(img, [x, 0.335, 0.15, 0.15 * W / H])
                    x += 0.17
        qsize = S.fit(question, S.ANTON, 120, 0.86)
        S.text(0.07, 0.29 if x > 0.07 else 0.37, question, S.ANTON, qsize, "#ffffff", va="center")
        S.text(0.07, 0.235 if x > 0.07 else 0.31, sub, S.GROT_M, 34, "#ffffff", va="center", alpha=0.7)
        if opts.get("handle"):
            S.text(0.07, SAFE_BOTTOM + 0.025, opts["handle"], S.GROT, 30, col, va="center")
        img = S.rgb_frame()
    finally:
        S.close()
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img)).convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ======================================================================= Variante C: Bühne (großes Logo, Balken, Schlagzeile)

C_LABEL = {"jahre": "5 JAHRE NACHGERECHNET", "duell": "AKTIEN-DUELL", "mythos": "MYTHOS-CHECK"}


def _particles(S, col, seed):
    """Leuchtende Datenpunkte entlang einer Welle – eigener Hintergrund statt Stockbild."""
    rng = np.random.default_rng(seed)
    ax = S.fig.add_axes([0, 0, 1, 1], zorder=-5)
    ax.axis("off"); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    n = 4200
    x = rng.uniform(-0.05, 1.05, n)
    band = 0.52 + 0.10 * np.sin(x * 5.2 + rng.uniform(0, 6)) + 0.06 * (x - 0.5)
    y = band + rng.normal(0, 0.055, n) * (0.6 + rng.random(n))
    size = rng.uniform(1, 9, n) ** 1.3
    alpha = np.clip(rng.uniform(0.15, 0.85, n) * np.exp(-((y - band) / 0.1) ** 2), 0.05, 0.85)
    rgba = np.zeros((n, 4)); rgba[:, :3] = S.rgb(col); rgba[:, 3] = alpha
    ax.scatter(x, y, s=size, c=rgba, linewidths=0)
    white = rng.random(n) < 0.15
    rgba_w = np.zeros((white.sum(), 4)); rgba_w[:, :3] = 1; rgba_w[:, 3] = alpha[white] * 0.8
    ax.scatter(x[white], y[white], s=size[white] * 0.6, c=rgba_w, linewidths=0)


def _white_tile(S, img, rect, pad=0.13):
    ax = S.fig.add_axes(rect)
    ax.axis("off")
    ax.add_patch(S.mpl.patches.FancyBboxPatch((0.02, 0.02), 0.96, 0.96, boxstyle="round,pad=0,rounding_size=0.16",
                                              transform=ax.transAxes, fc="#ffffff", ec="none"))
    ins = ax.inset_axes([pad, pad, 1 - 2 * pad, 1 - 2 * pad])
    ins.axis("off")
    ins.imshow(img, interpolation="lanczos")
    ins.set_aspect("equal")


def _bar(S, text, col, y):
    S.text(0.5, y, f"  {text}  ", S.ANTON, 46, INK, ha="center", va="center",
           bbox=dict(boxstyle="square,pad=0.28", fc=col, ec="none"))


def render_stage(fmt: str, d: dict, opts=None) -> bytes:
    opts = opts or {}
    _, col = STYLE[fmt]
    use_logo = opts.get("use_logo", True)
    S = Stage()
    try:
        S.begin(col, 1.2)
        _particles(S, col, hash(str(d.get("symbol") or d.get("shown"))) % 2**32)
        # Logos groß in der Mitte
        if fmt == "duell":
            syms = [l["symbol"] for l in d["lines"][:2]]
            imgs = [S.logo(s) if use_logo else None for s in syms]
            for img, x in zip(imgs, (0.17, 0.57)):
                if img is not None:
                    _white_tile(S, img, [x, 0.585, 0.26, 0.26 * W / H])
            S.text(0.5, 0.66, "VS", S.ANTON, 80, col, ha="center", va="center")
        elif fmt == "jahre":
            img = S.logo(d["symbol"]) if use_logo else None
            if img is not None:
                _white_tile(S, img, [0.32, 0.565, 0.36, 0.36 * W / H])
        else:
            S.text(0.5, 0.66, "?!", S.ANTON, 330, col, ha="center", va="center", alpha=0.95)
        _bar(S, C_LABEL[fmt], col, 0.515)
        # Schlagzeile: kleine Zeile + riesiger Name
        if fmt == "jahre":
            small, big = f"WIE LIEF {d['years'][-1]} WIRKLICH BEI", short_name(d["name"]).upper() + "?!"
        elif fmt == "duell":
            names = [short_name(l["name"]).upper() for l in d["lines"][:2]]
            small, big = f"{' VS. '.join(names)}", "WER GEWINNT?!"
        else:
            small, big = d["meta"]["claim"], "STIMMT DAS?!"
        ss = S.fit(small, S.ANTON, 84, 0.86)
        S.text(0.5, 0.445, small, S.ANTON, ss, "#ffffff", ha="center", va="center")
        bs = S.fit(big, S.ANTON, 230, 0.9)
        S.text(0.5, 0.345, big, S.ANTON, bs, "#ffffff", ha="center", va="center")
        # Fußzeile wie ein Sender-Bauchbinde
        S.fig.add_artist(S.mpl.lines.Line2D([0.07, 0.93], [0.215, 0.215], transform=S.fig.transFigure,
                                            color="#ffffff", alpha=0.35, lw=2))
        S.text(0.07, 0.19, "Echte Kurse. Keine Meinung.", S.GROT_M, 23, "#ffffff", va="center", alpha=0.8)
        if opts.get("handle"):
            S.text(0.93, 0.19, opts["handle"], S.GROT, 23, col, ha="right", va="center")
        img = S.rgb_frame()
    finally:
        S.close()
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img)).convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()
