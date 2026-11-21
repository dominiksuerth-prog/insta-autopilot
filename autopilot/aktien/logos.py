"""Firmenlogos: einmal aus dem Netz holen, dann lokal in instance/logos/ (PNG mit transparentem Hintergrund).

Quellen der Reihe nach (alle frei, ohne Schluessel):
  1. github.com/nvstly/icons      – US-Aktien, transparente 250-px-PNGs, wirken auf dunklem Grund am besten
  2. financialmodelingprep.com    – sehr breite Abdeckung, auch Boersen ausserhalb der USA
  3. assets.parqet.com            – nach Kuerzel oder ISIN, gut fuer europaeische Werte und ETFs
Eigene Logos (hochgeladen) werden nie ueberschrieben. Was nirgends zu finden war, wird 30 Tage nicht erneut gesucht.
Logos sind Marken der jeweiligen Unternehmen – zur Kennzeichnung im eigenen Tool gedacht.
"""
import io
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from .config import INSTANCE_DIR

LOGO_DIR = os.path.join(INSTANCE_DIR, "logos")
RETRY_DAYS = 30
_lock = threading.Lock()
PNG = b"\x89PNG\r\n\x1a\n"
EXCH_SUFFIX = re.compile(r"\.(DE|F|L|PA|AS|MI|MC|SW|VI|BR|CO|ST|HE|OL|TO|HK|T|AX|IR|LS)$")
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _safe(t: str) -> str:
    return re.sub(r"[^A-Z0-9.\-_]", "_", (t or "").upper())[:32]


def path_of(t: str) -> str:
    return os.path.join(LOGO_DIR, _safe(t) + ".png")


def _manual_path(t: str) -> str:
    return os.path.join(LOGO_DIR, "eigene", _safe(t) + ".png")


def cached(t: str):
    """Pfad zum Logo, wenn schon vorhanden (eigenes vor geladenem) – sonst None. Holt nichts aus dem Netz."""
    if not t:
        return None
    for p in (_manual_path(t), path_of(t)):
        if os.path.exists(p):
            return p
    return None


def has_logo(t: str) -> bool:
    return cached(t) is not None


def skip(t: str) -> bool:
    """Indizes, Waehrungen, Krypto: gibt es keine Firmenlogos."""
    t = (t or "").upper()
    return not t or t.startswith("^") or "=" in t or t.startswith("KRYPTO:") or t.endswith("-USD") or t.endswith("-EUR")


def _missing_file():
    return os.path.join(LOGO_DIR, "_nicht_gefunden.json")


def _missing() -> dict:
    try:
        with open(_missing_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _set_missing(t: str, on: bool):
    with _lock:
        m = _missing()
        if on:
            m[_safe(t)] = date.today().isoformat()
        else:
            m.pop(_safe(t), None)
        os.makedirs(LOGO_DIR, exist_ok=True)
        with open(_missing_file(), "w", encoding="utf-8") as f:
            json.dump(m, f)


def recently_missing(t: str) -> bool:
    d = _missing().get(_safe(t))
    return bool(d) and date.fromisoformat(d) > date.today() - timedelta(days=RETRY_DAYS)


def _isin_for(t: str):
    if ISIN_RE.match(t):
        return t
    try:
        from ..models import IsinMap
        row = IsinMap.query.filter_by(ticker=t).first()
        return row.isin if row and ISIN_RE.match(row.isin or "") else None
    except Exception:
        return None


def candidates(t: str, isin=None) -> list:
    """Alle URLs, unter denen das Logo liegen koennte – in der Reihenfolge, in der sie probiert werden."""
    t = (t or "").upper()
    base = EXCH_SUFFIX.sub("", t)
    us = []
    for v in (base, base.replace("-", "."), base.replace("-", ""), base.split("-")[0]):
        if v and v not in us:
            us.append(v)
    urls = []
    if not ISIN_RE.match(t):
        urls += [f"https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{v}.png" for v in us]
        urls.append(f"https://financialmodelingprep.com/image-stock/{t}.png")
        if base != t:
            urls.append(f"https://financialmodelingprep.com/image-stock/{base}.png")
        urls.append(f"https://assets.parqet.com/logos/symbol/{t}?format=png")
    if isin:
        urls.append(f"https://assets.parqet.com/logos/isin/{isin}?format=png")
    return urls


def _http_get(url: str):
    from curl_cffi import requests
    r = requests.get(url, timeout=6, impersonate="chrome")
    return r.status_code, r.content


def _to_png(data: bytes):
    """PNG direkt; JPEG/WebP nur, wenn Pillow da ist. Winzige Platzhalter (< 300 Byte) gelten als 'nicht gefunden'."""
    if not data or len(data) < 300:
        return None
    if data.startswith(PNG):
        return data
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        out = io.BytesIO()
        im.convert("RGBA").save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


def fetch(t: str, force: bool = False, isin=None):
    """Logo besorgen (Cache -> Netz). Gibt den Pfad zurueck oder None."""
    if skip(t):
        return None
    p = cached(t)
    if p and not force:
        return p
    if not force and recently_missing(t):
        return None
    for url in candidates(t, isin if isin is not None else _isin_for(t.upper())):
        try:
            code, data = _http_get(url)
        except Exception:
            continue
        png = _to_png(data) if code == 200 else None
        if png:
            os.makedirs(LOGO_DIR, exist_ok=True)
            tmp = path_of(t) + ".tmp"
            with open(tmp, "wb") as f:
                f.write(png)
            os.replace(tmp, path_of(t))
            _set_missing(t, False)
            return path_of(t)
    _set_missing(t, True)
    return None


def save_manual(t: str, data: bytes) -> bool:
    png = _to_png(data)
    if not png:
        return False
    os.makedirs(os.path.dirname(_manual_path(t)), exist_ok=True)
    with open(_manual_path(t), "wb") as f:
        f.write(png)
    _set_missing(t, False)
    return True


def reset(t: str):
    """Geladenes und eigenes Logo entfernen, Sperre aufheben – beim naechsten Aufruf wird neu gesucht."""
    for p in (path_of(t), _manual_path(t)):
        if os.path.exists(p):
            os.remove(p)
    _set_missing(t, False)


def prefetch(tickers, state=None, budget: int = 800, workers: int = 8) -> dict:
    """Fehlende Logos im Hintergrund holen (Taegliches Update). Bereits vorhandene/gesperrte kosten nichts."""
    todo = [t for t in dict.fromkeys(tickers) if not skip(t) and not cached(t) and not recently_missing(t)][:budget]
    isins = {t: (_isin_for(t.upper()) or "") for t in todo}     # DB nur im Haupt-Thread
    found = 0
    if state is not None:
        state.update(total=len(todo), done=0)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, p in enumerate(ex.map(lambda x: fetch(x, isin=isins[x]), todo), 1):
            found += p is not None
            if state is not None:
                state["done"] = i
    return {"gesucht": len(todo), "gefunden": found}
