"""Kursdaten für die Reels – Ersatz für indices.py + store.py des Aktientools.

Gleiche Funktionen (close, compare, seasonal, name_of, ccy_of, META), aber ohne Datenbank:
Kurse kommen bei Bedarf von Yahoo Finance und bleiben nur während des Laufs im Speicher.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

#  Kuerzel, Name, Region, Waehrung, mit Dividenden?
INDICES = [
    ("^GSPC", "S&P 500", "USA", "USD", False),
    ("^NDX", "Nasdaq 100", "USA", "USD", False),
    ("^IXIC", "Nasdaq Composite", "USA", "USD", False),
    ("^DJI", "Dow Jones", "USA", "USD", False),
    ("^RUT", "Russell 2000 (kleine US-Firmen)", "USA", "USD", False),
    ("URTH", "MSCI World (iShares-ETF, USD)", "Welt", "USD", True),
    ("ACWI", "MSCI ACWI – Welt inkl. Schwellenländer (iShares-ETF, USD)", "Welt", "USD", True),
    ("EUNL.DE", "MSCI World (iShares UCITS, Xetra, EUR)", "Welt", "EUR", True),
    ("IUSQ.DE", "MSCI ACWI (iShares UCITS, Xetra, EUR)", "Welt", "EUR", True),
    ("EEM", "MSCI Emerging Markets (iShares-ETF, USD)", "Schwellenländer", "USD", True),
    ("^GDAXI", "DAX", "Deutschland", "EUR", True),
    ("^MDAXI", "MDAX", "Deutschland", "EUR", True),
    ("^STOXX50E", "Euro Stoxx 50", "Europa", "EUR", False),
    ("^STOXX", "Stoxx Europe 600", "Europa", "EUR", False),
    ("^FTSE", "FTSE 100", "Großbritannien", "GBP", False),
    ("^N225", "Nikkei 225", "Japan", "JPY", False),
    ("^HSI", "Hang Seng", "Hongkong", "HKD", False),
]
META = {s: {"symbol": s, "name": n, "region": r, "ccy": c, "tr": tr} for s, n, r, c, tr in INDICES}
FX = {"USD": "EURUSD=X", "GBP": "EURGBP=X", "JPY": "EURJPY=X", "HKD": "EURHKD=X"}   # Waehrung je Euro

_CACHE = {}
_UNI = json.loads((Path(__file__).with_name("universe.json")).read_text(encoding="utf-8"))
NAMES = {u["symbol"]: u["name"] for u in _UNI}


def _ohlc_close(symbol: str) -> pd.Series:
    if symbol not in _CACHE:
        import yfinance as yf
        s = pd.Series(dtype=float)
        for _ in range(3):
            try:
                df = yf.Ticker(symbol).history(period="max", auto_adjust=True)
                if df is not None and not df.empty:
                    s = df["Close"].dropna()
                    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
                    break
            except Exception as e:  # Netz/Rate-Limit: kurz warten und erneut
                print("yfinance", symbol, e)
            import time
            time.sleep(3)
        _CACHE[symbol] = s
    return _CACHE[symbol]


class DataError(ValueError):
    """Kursdaten sehen unplausibel aus – lieber kein Reel als ein falsches."""


def check_quality(symbol: str, s: pd.Series, kind: str = "aktie") -> None:
    """Prüft die letzten 11 Jahre: aktuell, keine Lücken, keine unmöglichen Sprünge, keine Null-/Minuskurse.

    Yahoo liefert gelegentlich Fehler (falsche Splits, fehlende Wochen, Ausreißer). Ein Kanal namens
    'nachgerechnet' lebt davon, dass die Zahlen stimmen – deshalb wird im Zweifel abgebrochen.
    """
    if s is None or s.empty:
        raise DataError(f"{symbol}: keine Kurse von Yahoo")
    recent = s[s.index >= s.index[-1] - pd.DateOffset(years=11)]
    age = (pd.Timestamp.today().normalize() - s.index[-1]).days
    if age > 6:
        raise DataError(f"{symbol}: letzter Kurs ist {age} Tage alt ({s.index[-1]:%d.%m.%Y})")
    if (recent <= 0).any():
        raise DataError(f"{symbol}: Kurs von 0 oder darunter in den Daten")
    gaps = recent.index.to_series().diff().dt.days
    if len(gaps) and gaps.max() > 10:
        g = gaps.idxmax()
        raise DataError(f"{symbol}: {int(gaps.max())} Tage ohne Kurs vor dem {g:%d.%m.%Y}")
    limit = {"index": 0.15, "fx": 0.10}.get(kind, 0.45)
    jumps = recent.pct_change().abs()
    if len(jumps.dropna()) and jumps.max() > limit:
        j = jumps.idxmax()
        # Ein echter Absturz/Sprung bleibt meist bestehen; ein Datenfehler springt am Folgetag zurück.
        pos = recent.index.get_loc(j)
        back = recent.iloc[pos + 1] / recent.iloc[pos] - 1 if pos + 1 < len(recent) else 0.0
        if abs(back) > limit * 0.6 or kind != "aktie":
            raise DataError(f"{symbol}: unplausibler Tagessprung von {jumps.max():.0%} am {j:%d.%m.%Y}")
        ratio = recent.iloc[pos] / recent.iloc[pos - 1]
        split_like = any(abs(ratio * n - 1) < 0.02 or abs(ratio / n - 1) < 0.02 for n in (2, 3, 4, 5, 8, 10, 20))
        if split_like or jumps.max() > 0.85:
            raise DataError(f"{symbol}: Tagessprung von {jumps.max():.0%} am {j:%d.%m.%Y} (vermutlich Split-Fehler)")


def ccy_of(symbol: str) -> str:
    if symbol in META:
        return META[symbol]["ccy"]
    return "EUR" if symbol.upper().endswith(".DE") else "USD"


def name_of(symbol: str) -> str:
    if symbol in META:
        return META[symbol]["name"]
    return NAMES.get(symbol, symbol)


def close(symbol: str, eur: bool = False) -> pd.Series:
    """Schlusskurse, auf Wunsch in Euro."""
    key = (symbol, bool(eur))
    if key in _CACHE:
        return _CACHE[key]
    s = _ohlc_close(symbol)
    check_quality(symbol, s, "index" if symbol.startswith("^") else "aktie")
    if eur and ccy_of(symbol) != "EUR":
        fx_sym = FX.get(ccy_of(symbol), "EURUSD=X")
        fx = _ohlc_close(fx_sym)
        check_quality(fx_sym, fx, "fx")
        if fx.empty:
            s = pd.Series(dtype=float)
        else:
            fx = fx.reindex(fx.index.union(s.index)).ffill().reindex(s.index)
            s = (s / fx).dropna()
    _CACHE[key] = s
    return s


def compare(symbols_: list, start=None, eur: bool = False) -> dict:
    """Mehrere Kurse auf dieselbe Einheit: Prozent seit dem gemeinsamen Startdatum (= 0 %)."""
    series = {}
    for s in symbols_:
        c = close(s, eur)
        if not c.empty:
            series[s] = c
    if not series:
        return {}
    common = max(c.index[0] for c in series.values())        # alle haben ab hier Kurse
    st = max(pd.Timestamp(start), common) if start is not None else common
    out, table = [], []
    first = None
    rets = {}
    for s, c in series.items():
        c = c[c.index >= st]
        if len(c) < 2:
            continue
        base = float(c.iloc[0])
        pct = (c / base - 1) * 100
        out.append({"symbol": s, "name": name_of(s), "ccy": "EUR" if eur else ccy_of(s),
                    "tr": META.get(s, {}).get("tr"),
                    "data": [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 3)} for t, v in pct.items()]})
        r = c.pct_change().dropna()
        rets[s] = r
        yrs = (c.index[-1] - c.index[0]).days / 365.25
        tot = (float(c.iloc[-1]) / base - 1) * 100
        table.append({"symbol": s, "name": name_of(s), "total": tot,
                      "pa": ((1 + tot / 100) ** (1 / yrs) - 1) * 100 if yrs >= 1 else None,
                      "vol": float(r.std() * math.sqrt(252) * 100) if len(r) > 20 else None,
                      "dd": float((c / c.cummax() - 1).min() * 100),
                      "best": float(r.max() * 100) if len(r) else None, "worst": float(r.min() * 100) if len(r) else None})
        if first is None:
            first = s
    # Korrelation der Tagesrenditen (Wochenrenditen bei verschiedenen Boersen-Zeitzonen robuster)
    corr = None
    if len(rets) >= 2:
        W = pd.DataFrame({k: (1 + v).resample("W-FRI").prod() - 1 for k, v in rets.items()}).dropna()
        if len(W) > 10:
            corr = W.corr().round(2)
    for t in table:
        t["sharpe_like"] = t["pa"] / t["vol"] if t.get("pa") is not None and t.get("vol") else None
    return {"series": out, "table": table, "start": st.date(), "common": common.date(),
            "corr": {"cols": list(corr.columns), "rows": [[corr.loc[a, b] for b in corr.columns] for a in corr.index]} if corr is not None else None}


def seasonal(symbol: str, eur: bool = False) -> dict:
    """Jedes Kalenderjahr als eigene Linie: Prozent seit Jahresanfang (Schluss des Vorjahres = 0 %)."""
    s = close(symbol, eur)
    if s.empty:
        return {}
    years = sorted(set(s.index.year))
    lines, table = {}, []
    for y in years:
        cur = s[s.index.year == y]
        prev = s[s.index.year == y - 1]
        if cur.empty:
            continue
        base = float(prev.iloc[-1]) if len(prev) else float(cur.iloc[0])
        partial = prev.empty
        pct = (cur / base - 1) * 100
        lines[y] = [{"time": f"2000-{t.month:02d}-{t.day:02d}", "value": round(float(v), 3)} for t, v in pct.items()]
        table.append({"year": y, "ret": float(pct.iloc[-1]), "max": float(pct.max()), "min": float(pct.min()),
                      "partial": partial or (y == s.index[-1].year), "to_date": y == s.index[-1].year})
    # Durchschnittsverlauf aller vollstaendigen Jahre (je Kalendertag)
    full = [r["year"] for r in table if not r["partial"]]
    avg = []
    if full:
        df = pd.DataFrame({y: pd.Series({p["time"]: p["value"] for p in lines[y]}) for y in full})
        df = df.sort_index().ffill()
        m = df.mean(axis=1)
        avg = [{"time": t, "value": round(float(v), 3)} for t, v in m.items()]
        # Wie lief der bisherige Teil des laufenden Jahres im Schnitt?
    cur_y = s.index[-1].year
    md = f"2000-{s.index[-1].month:02d}-{s.index[-1].day:02d}"
    same_point = []
    for y in full:
        pts = [p for p in lines[y] if p["time"] <= md]
        if pts:
            same_point.append(pts[-1]["value"])
    return {"symbol": symbol, "name": name_of(symbol), "years": years, "lines": lines, "avg": avg, "table": table,
            "full": full, "cur_year": cur_y, "same_point": float(np.mean(same_point)) if same_point else None,
            "pos_years": sum(1 for r in table if not r["partial"] and r["ret"] > 0), "n_full": len(full)}
