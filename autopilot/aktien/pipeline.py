"""Aktien-Reels im Autopiloten: Idee wählen (Claude), Video rendern (reel.py), speichern, als Reel veröffentlichen.

- Ideen: Claude wählt Format + Werte aus universe.json (DAX, S&P 500, Indizes). Alle Zahlen in Video und Caption
  rechnet reel.py aus echten Kursen – Claude liefert nur die Auswahl, nie eine Zahl.
- Videos liegen NICHT im Git-Verlauf, sondern als Datei am GitHub-Release "reels" (öffentlich abrufbar).
- Veröffentlichen: Video direkt zu Meta hochladen (rupload), dann als Reel posten.
"""
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from . import indices, reel

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "reels"
REPO = os.getenv("GITHUB_REPOSITORY", "dominiksuerth-prog/insta-autopilot")
UNI = {u["symbol"]: u for u in json.loads((Path(__file__).with_name("universe.json")).read_text(encoding="utf-8"))}

IDEA_PROMPT = """Du planst ein Reel für den Instagram-Kanal {handle} ("Aktien nachgerechnet": echte Kurse, nüchtern,
keine Empfehlungen). Wähle GENAU EIN Reel.

Formate:
- "jahre": EINE Aktie oder ein Index, die letzten 5 Kalenderjahre übereinander (laufendes Jahr in Gelb).
  Gut für bekannte Namen mit Gesprächswert (Nvidia, Tesla, SAP, Rheinmetall, Allianz …).
- "duell": 2 (selten 3) Werte gegeneinander über einen Zeitraum ("1","3","5","10" Jahre). Sinnvolle Paare:
  direkte Konkurrenten (Coca-Cola vs. Pepsi, BMW vs. Mercedes, SAP vs. Microsoft, Visa vs. Mastercard) oder
  Aktie vs. Index (Apple vs. S&P 500).
Alle Kurse werden in Euro umgerechnet (Zielgruppe: Deutschland).
- "mythos": eine Börsenweisheit, geprüft an einem Index (^GSPC, ^GDAXI oder ^NDX).
  Mythen: "sellinmay", "dezember", "januar", "nachminus". Höchstens einmal in 2 Wochen.

Mischung pro Woche: meist "jahre", dazu ein "duell"; "mythos" nur ab und zu.
Abwechslung: Nimm keinen Wert, der in den letzten Reels schon vorkam, und wechsle zwischen DAX und USA.
Erlaubt sind NUR Kürzel aus dieser Liste (Kürzel – Name):
{universe}

Bisherige Reels (neueste zuerst, nicht wiederholen):
{recent}
{avoid}{wish}
Optional "question": kurzer Hook für den gelben Sticker, max. 24 Zeichen, deutsch, als Frage,
ohne Prognose und ohne Kaufaufforderung (z. B. "WER LIEGT VORN?"). Leer lassen ist ok.
Antworte über das Werkzeug "save_reel"."""

IDEA_TOOL = {
    "name": "save_reel",
    "description": "Speichert die Reel-Idee.",
    "input_schema": {"type": "object", "properties": {
        "format": {"type": "string", "enum": ["jahre", "duell", "mythos"]},
        "tickers": {"type": "array", "items": {"type": "string"}, "description": "1 Kürzel (jahre/mythos) oder 2–3 (duell)"},
        "period": {"type": "string", "enum": ["1", "3", "5", "10"]},
        "myth": {"type": "string", "enum": ["sellinmay", "dezember", "januar", "nachminus"]},
        "question": {"type": "string"},
    }, "required": ["format", "tickers"]},
}

VERDICT_TEXT = {"STIMMT MEISTENS": "Stimmt meistens", "STIMMT NICHT": "Stimmt nicht", "EHER MÜNZWURF": "Eher Münzwurf"}


def describe(idea):
    f, t = idea["format"], idea["tickers"]
    names = [reel.short_name(indices.name_of(x)) for x in t]
    if f == "jahre":
        return f"Jahre: {names[0]} – letzte 5 Jahre"
    if f == "duell":
        return f"Duell: {' vs. '.join(names)} – {idea.get('period', '10')} Jahre"
    return f"Mythos: {reel.MYTHS[idea.get('myth', 'sellinmay')]['short']} – {names[0]}"


def _validate(idea):
    f = idea.get("format")
    t = [x.strip().upper() for x in idea.get("tickers") or [] if x.strip().upper() in UNI]
    if f == "jahre" and len(t) >= 1:
        return {"format": f, "tickers": t[:1], "question": idea.get("question", "")}
    if f == "duell" and len(t) >= 2:
        return {"format": f, "tickers": t[:3], "period": idea.get("period") or "10", "question": idea.get("question", "")}
    if f == "mythos":
        sym = t[0] if t and t[0] in ("^GSPC", "^GDAXI", "^NDX") else "^GSPC"
        return {"format": f, "tickers": [sym], "myth": idea.get("myth") or "sellinmay", "question": idea.get("question", "")}
    return None


def recent_ideas(state, ch_id, n=30):
    posts = [p for p in state["posts"].values() if p["channel"] == ch_id and p.get("idea")]
    posts.sort(key=lambda p: p.get("created", ""), reverse=True)
    return [p["topic"] for p in posts[:n]]


def plan_idea(cfg, ch, state, wish="", avoid=()):
    import anthropic
    uni = "\n".join(f"{s} – {u['name']}" for s, u in UNI.items())
    rec = "\n".join("- " + r for r in recent_ideas(state, ch["id"])) or "- (noch keine)"
    wish_txt = f"\nWUNSCH FÜR DIESES REEL (hat Vorrang): {wish}\n" if wish else ""
    avoid_txt = ("\nDiese Werte NICHT nehmen (Kursdaten gerade unbrauchbar): " + ", ".join(avoid) + "\n") if avoid else ""
    prompt = IDEA_PROMPT.format(handle=ch["handle"], universe=uni, recent=rec, wish=wish_txt, avoid=avoid_txt)
    client = anthropic.Anthropic()
    for _ in range(3):
        msg = client.messages.create(model=cfg.get("claude_model", "claude-sonnet-5-5"), max_tokens=600,
                                     tools=[IDEA_TOOL], messages=[{"role": "user", "content": prompt}])
        raw = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if raw is None:
            try:
                from ..core import _extract_json
                raw = _extract_json("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))
            except Exception:
                raw = None
        idea = _validate(raw or {})
        if idea:
            return idea
    raise RuntimeError("Claude hat keine gültige Reel-Idee geliefert")


def _params(idea):
    f, t = idea["format"], idea["tickers"]
    q = (idea.get("question") or "").strip()
    if len(q) > 24 or not q.endswith("?"):
        q = ""                      # zu lang/abgeschnitten -> Standardfrage des Formats
    if f == "jahre":
        s = indices.close(t[0])
        if s.empty:
            raise ValueError(f"Keine Kurse für {t[0]}")
        last = s.index[-1].year
        return {"ticker": t[0], "years": list(range(last - 4, last + 1)), "eur": True, "question": q}
    if f == "duell":
        return {"tickers": t, "period": idea.get("period", "10"), "eur": True, "question": q}
    return {"ticker": t[0], "myth": idea.get("myth", "sellinmay"), "eur": False, "question": q}


def comment_question(fmt, d, key):
    """Eine Frage, die man in Sekunden beantworten kann – Kommentare zählen für Instagram am meisten."""
    import hashlib
    if fmt == "jahre":
        name = reel.short_name(d["name"])
        opts = [f"Hättest du {name} {d['years'][-1]} durchgehalten? Ja oder nein? 👇",
                f"Überrascht dich das bei {name}? 👇",
                "Welche Aktie soll ich als Nächstes nachrechnen? Schreib sie in die Kommentare 👇"]
    elif fmt == "duell":
        names = [l["name"] for l in d["lines"]]
        best = max(d["lines"], key=lambda l: l["final"])["name"]
        opts = [f"Team {' oder Team '.join(names)}? 👇",
                f"Hättest du vorher auf {best} getippt? 👇",
                "Welches Duell soll ich als Nächstes nachrechnen? Schreib es in die Kommentare 👇"]
    else:
        opts = ["Hast du an diese Börsenregel geglaubt? Ja oder nein? 👇",
                "Welche Börsenweisheit soll ich als Nächstes prüfen? 👇"]
    return opts[int(hashlib.sha1(key.encode()).hexdigest()[:6], 16) % len(opts)]


def _caption(fmt, d, key=""):
    lines = reel.FORMATS[fmt].caption(d)
    lines += ["", comment_question(fmt, d, key)]
    disc = reel.disclosure(d.get("shown", []))
    lines += ["", disc] if disc else [""]
    lines += ["Keine Anlageberatung.", "", reel.HASHTAGS]
    text = "\n".join(lines).replace("\n\n\n", "\n\n").strip()
    for k, v in VERDICT_TEXT.items():
        text = text.replace(f"Urteil: {k.capitalize()}.", f"Urteil: {v}.")
    return text


def _gh(*args, check=True):
    r = subprocess.run(["gh", *args, "-R", REPO], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def video_url(name):
    return f"https://github.com/{REPO}/releases/download/{RELEASE}/{name}"


def store_video(path):
    """Video als Datei am Release 'reels' ablegen (nicht im Git-Verlauf)."""
    if not os.getenv("GH_TOKEN"):
        print("GH_TOKEN fehlt – Video bleibt lokal:", path)
        return
    if _gh("release", "view", RELEASE, check=False).returncode != 0:
        _gh("release", "create", RELEASE, "--title", "Reels (automatisch)", "--latest=false",
            "--notes", "Videodateien des Autopiloten. Werden 30 Tage nach dem Posten gelöscht.")
    _gh("release", "upload", RELEASE, str(path), "--clobber")


def delete_video(name):
    if os.getenv("GH_TOKEN"):
        _gh("release", "delete-asset", RELEASE, name, "-y", check=False)


def build_reel(cfg, ch, post_id, idea):
    """Rendert Video + Titelbild. Gibt die Felder für den Post zurück."""
    from .assets import setup_fonts
    setup_fonts()
    fmt = idea["format"]
    d = reel.FORMATS[fmt].prepare(_params(idea))
    opts = {"handle": ch["handle"], "use_logo": ch.get("logos", True)}
    out_dir = ROOT / "docs" / "insta" / ch["id"] / post_id
    out_dir.mkdir(parents=True, exist_ok=True)
    # Einheitliches Titelbild (Format-Farbe, Name, Frage – ohne Ergebnis) für Profil-Raster und Cockpit
    from . import cover
    (out_dir / "cover.jpg").write_bytes(cover.render_stage(fmt, d, opts))   # Variante C "Bühne"
    name = f"{ch['id']}_{post_id}.mp4"
    tmp = Path(tempfile.gettempdir()) / name
    t0 = time.time()
    from . import music
    track = music.track_for(post_id, reel.FORMATS[fmt].total(d) + 2, tempfile.gettempdir())
    reel.render_format(fmt, d, str(tmp), opts=opts, music=track)
    print(f"Reel gerendert in {time.time() - t0:.0f} s: {name}")
    store_video(tmp)
    base = cfg["public_base_url"].rstrip("/")
    return {"urls": [f"{base}/{ch['id']}/{post_id}/cover.jpg"], "video": name, "video_url": video_url(name),
            "caption": _caption(fmt, d, post_id), "idea": idea, "topic": describe(idea), "check": [], "format": "reel"}


# ---------------------------------------------------------------- Veröffentlichen

def publish_reel(cfg, ch, post, token, graph, ig_call, wait_ready):
    ig = ch["ig_user_id"]
    local = Path(tempfile.gettempdir()) / post["video"]
    if not local.exists():
        req = urllib.request.Request(post.get("video_url") or video_url(post["video"]),
                                     headers={"User-Agent": "insta-autopilot"})
        local.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    data = local.read_bytes()
    res = ig_call("POST", graph(cfg, f"/{ig}/media"), media_type="REELS", upload_type="resumable",
                  caption=post["caption"], share_to_feed="true", cover_url=post["urls"][0], access_token=token)
    cid = res["id"]
    ver = cfg.get("graph_version") or "v25.0"
    import requests
    r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{ver}/{cid}", data=data, timeout=300,
                      headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))})
    if r.status_code >= 400:
        raise RuntimeError(f"Video-Upload fehlgeschlagen {r.status_code}: {r.text[:200]}")
    for _ in range(60):                     # Meta verarbeitet das Video (meist 20–90 s)
        st = ig_call("GET", graph(cfg, f"/{cid}"), fields="status_code,status", access_token=token)
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram konnte das Video nicht verarbeiten: {st.get('status', code)}")
        time.sleep(5)
    else:
        raise RuntimeError("Instagram hat das Video nicht rechtzeitig verarbeitet")
    out = ig_call("POST", graph(cfg, f"/{ig}/media_publish"), creation_id=cid, access_token=token)
    return out["id"]
