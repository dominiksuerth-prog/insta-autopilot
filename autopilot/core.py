"""Instagram-Autopilot (GitHub-Variante):
Themen -> Claude -> Slides (JPG, über GitHub Pages öffentlich) -> Telegram-Freigabe -> Instagram.
Zugangsschlüssel kommen ausschließlich aus Umgebungsvariablen (GitHub Secrets) – nie aus Dateien im Repo.
"""
import io
import json
import os
import pathlib
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "state.json"
TZ = ZoneInfo("Europe/Berlin")


def now():
    return datetime.now(TZ)


# ---------------------------------------------------------------- Konfiguration & Zustand

def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["channels_by_id"] = {c["id"]: c for c in cfg["channels"]}
    return cfg


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"channels": {}, "posts": {}, "tg_offset": None}


def save_state(state):
    """Achtung: state.json liegt im öffentlichen Repo – hier nie Tokens speichern."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def channel_state(state, ch_id):
    return state["channels"].setdefault(ch_id, {"topic_index": 0})


def get_token(ch):
    tok = os.getenv(ch["token_env"], "")
    if not tok:
        raise RuntimeError(f"Kein Instagram-Token für {ch['id']} (Secret {ch['token_env']} fehlt)")
    return tok


# ---------------------------------------------------------------- Themen

def next_topic(state, ch):
    topics_file = ROOT / "channels" / ch["id"] / "topics.txt"
    topics = [t.strip() for t in topics_file.read_text(encoding="utf-8").splitlines()
              if t.strip() and not t.strip().startswith("#")]
    cs = channel_state(state, ch["id"])
    idx = cs.get("topic_index", 0)
    if idx >= len(topics):
        raise RuntimeError(f"Themenliste für {ch['id']} ist aufgebraucht – neue Themen in topics.txt ergänzen.")
    cs["topic_index"] = idx + 1
    return topics[idx]


# ---------------------------------------------------------------- Text mit Claude

PROMPT = """Du schreibst einen Instagram-Karussell-Post für den deutschen Kanal {handle}.

THEMA: {topic}

STIL DES KANALS:
{style}

FAKTENBASIS (nur diese Zahlen, Beträge, Fristen und Prozentwerte verwenden):
{facts}

REGELN
- Deutsch, du-Form, kurz und konkret. Keine Emojis auf den Slides.
- 5 bis 7 Slides. Slide 1 hat type "cover", die letzte type "cta".
- Dazwischen type "point" (eine Aussage mit Erklärung) oder "list" (2 bis 4 kurze Punkte).
- Bei "point" kannst du "contra" setzen, wenn du einen Mythos/Irrtum widerlegst (contra = der Irrtum in Anführungszeichen).
- Titel max. 60 Zeichen, body max. 220 Zeichen, Listenpunkte max. 70 Zeichen.
- Markiere im Titel genau ein Schlüsselwort mit *Sternchen* (wird farbig hervorgehoben).
- Jede Zahl, die NICHT in der Faktenbasis steht, lässt du weg. Wenn du eine Aussage für unsicher hältst, nimm sie in "check" auf.
- Keine Rechts-, Steuer- oder Anlageberatung im Einzelfall: allgemeine Infos, keine Garantien.
- Caption: 500 bis 1200 Zeichen. Erste Zeile = Hook. Dann Kernaussage. Dann Aufforderung zum Speichern/Teilen.
  {cta_hint}
- 5 bis 8 passende deutsche Hashtags.

Gib das Ergebnis ausschließlich über das Werkzeug "save_post" zurück. Aufbau der Felder:
{{
  "slides": [
    {{"type": "cover", "kicker": "KURZES LABEL", "title": "...", "subtitle": "..."}},
    {{"type": "point", "label": "...", "contra": "optional", "title": "...", "body": "..."}},
    {{"type": "list", "title": "...", "items": ["...", "..."]}},
    {{"type": "cta", "title": "...", "body": "..."}}
  ],
  "caption": "...",
  "hashtags": ["#...", "#..."],
  "check": ["Was vor dem Posten geprüft werden sollte (leer lassen, wenn nichts)"]
}}"""


POST_TOOL = {
    "name": "save_post",
    "description": "Speichert den fertigen Karussell-Post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slides": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["cover", "point", "list", "cta"]},
                    "kicker": {"type": "string"}, "label": {"type": "string"},
                    "contra": {"type": "string"}, "title": {"type": "string"},
                    "subtitle": {"type": "string"}, "body": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["type", "title"],
            }},
            "caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "check": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slides", "caption", "hashtags"],
    },
}


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Claude hat kein JSON geliefert")
    return json.loads(m.group(0))


def generate_post(cfg, ch, topic):
    import anthropic

    chdir = ROOT / "channels" / ch["id"]
    prompt = PROMPT.format(
        handle=ch["handle"],
        topic=topic,
        style=(chdir / "style.md").read_text(encoding="utf-8"),
        facts=(chdir / "facts.md").read_text(encoding="utf-8"),
        cta_hint=ch.get("cta_hint", ""),
    )
    client = anthropic.Anthropic()
    last_err = None
    for _ in range(3):  # bis zu 3 Versuche
        msg = client.messages.create(
            model=cfg.get("claude_model", "claude-sonnet-5-5"),
            max_tokens=4000,
            tools=[POST_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )
        data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if data is None:  # Rückfall: JSON aus Text lesen
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            try:
                data = _extract_json(text)
            except Exception as e:
                last_err = e
                continue
        if isinstance(data, dict) and data.get("slides"):
            break
        last_err = ValueError("Antwort ohne Slides")
    else:
        raise RuntimeError(f"Claude-Antwort nicht lesbar: {last_err}")
    slides = data.get("slides", [])
    if not 2 <= len(slides) <= 10:
        raise ValueError(f"Ungültige Slide-Anzahl: {len(slides)}")
    return data


# ---------------------------------------------------------------- Slides rendern

def _hl(value):
    """*Wort* -> <span class="hl">Wort</span>, alles andere HTML-escaped."""
    safe = str(escape(value or ""))
    return Markup(re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', safe))


def render_post(cfg, ch, data, post_id):
    from PIL import Image
    from playwright.sync_api import sync_playwright

    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html"]))
    env.filters["hl"] = _hl
    tpl = env.get_template("slide.html")
    theme = Markup((ROOT / "channels" / ch["id"] / "theme.css").read_text(encoding="utf-8"))

    outdir = ROOT / "docs" / "insta" / ch["id"] / post_id
    outdir.mkdir(parents=True, exist_ok=True)
    slides = data["slides"]
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, s in enumerate(slides, 1):
            html = tpl.render(s=s, i=i, n=len(slides), handle=ch["handle"], theme=theme)
            page.set_content(html, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            png = page.screenshot(type="png")
            path = outdir / f"{i}.jpg"
            Image.open(io.BytesIO(png)).convert("RGB").save(path, "JPEG", quality=90)
            paths.append(path)
        browser.close()
    base = cfg["public_base_url"].rstrip("/")
    urls = [f"{base}/{ch['id']}/{post_id}/{p.name}" for p in paths]
    return paths, urls


def build_caption(data):
    tags = " ".join(data.get("hashtags", []))
    return f"{data.get('caption', '').strip()}\n\n{tags}".strip()


# ---------------------------------------------------------------- Instagram

def _graph(cfg, path):
    """Instagram API über Facebook Login for Business (Unified Login, Systemnutzer-Token)."""
    host = (cfg.get("graph_host") or "https://graph.facebook.com").rstrip("/")
    ver = cfg.get("graph_version") or ""
    return f"{host}{'/' + ver if ver else ''}{path}"


def _ig_call(method, url, **params):
    r = requests.request(method, url, params=params if method == "GET" else None,
                         data=params if method == "POST" else None, timeout=60)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", data)
        msg = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(f"Instagram-API-Fehler {r.status_code}: {msg}")
    return data


def _wait_ready(cfg, container_id, token):
    for _ in range(30):
        st = _ig_call("GET", _graph(cfg, f"/{container_id}"), fields="status_code", access_token=token)
        code = st.get("status_code")
        if code in ("FINISHED", None):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Container {container_id}: {code}")
        time.sleep(3)
    raise RuntimeError(f"Container {container_id} wurde nicht fertig")


def wait_public(urls, max_wait=420):
    """GitHub Pages braucht nach dem Push 1–3 Minuten. Warten, bis alle Bilder erreichbar sind."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if all(requests.head(u, timeout=20).status_code == 200 for u in urls):
            return
        time.sleep(15)
    raise RuntimeError("Bilder sind auf GitHub Pages noch nicht erreichbar – Freigabe beim nächsten Lauf erneut.")


def publish_carousel(cfg, ch, image_urls, caption):
    token = get_token(ch)
    ig = ch["ig_user_id"]
    children = []
    for u in image_urls:
        res = _ig_call("POST", _graph(cfg, f"/{ig}/media"), image_url=u,
                       is_carousel_item="true", access_token=token)
        children.append(res["id"])
    for c in children:
        _wait_ready(cfg, c, token)
    container = _ig_call("POST", _graph(cfg, f"/{ig}/media"), media_type="CAROUSEL",
                         children=",".join(children), caption=caption, access_token=token)["id"]
    _wait_ready(cfg, container, token)
    res = _ig_call("POST", _graph(cfg, f"/{ig}/media_publish"), creation_id=container, access_token=token)
    return res["id"]




# ---------------------------------------------------------------- Telegram

def _tg(method, **kwargs):
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
    r = requests.post(f"https://api.telegram.org/bot{tok}/{method}", timeout=70, **kwargs)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram-Fehler: {data.get('description', data)}")
    return data["result"]


def tg_text(text, buttons=None):
    payload = {"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": text[:4000]}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    return _tg("sendMessage", data=payload)


def tg_album(paths):
    files, media = {}, []
    for i, p in enumerate(paths[:10]):
        files[f"p{i}"] = open(p, "rb")
        media.append({"type": "photo", "media": f"attach://p{i}"})
    try:
        _tg("sendMediaGroup", data={"chat_id": os.getenv("TELEGRAM_CHAT_ID"),
                                    "media": json.dumps(media)}, files=files)
    finally:
        for f in files.values():
            f.close()


# ---------------------------------------------------------------- Ablauf

def prune_old(state, days=30):
    """Löscht Bilder von Posts, die älter als `days` Tage und nicht mehr offen sind
    (Instagram hat sie längst kopiert). Hält Repo und Website klein."""
    import shutil
    cutoff = now().timestamp() - days * 86400
    for pid, p in state["posts"].items():
        if p["status"] == "pending" or p.get("pruned"):
            continue
        if datetime.fromisoformat(p["created"]).timestamp() < cutoff:
            shutil.rmtree(ROOT / "docs" / "insta" / p["channel"] / pid, ignore_errors=True)
            p["pruned"] = True
    save_state(state)


def create_post(cfg, state, ch, topic=None):
    """Erzeugt einen Post und schickt ihn zur Freigabe per Telegram."""
    topic = topic or next_topic(state, ch)  # wird erst nach Erfolg gespeichert
    data = generate_post(cfg, ch, topic)
    post_id = now().strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:6]
    paths, urls = render_post(cfg, ch, data, post_id)
    caption = build_caption(data)
    state["posts"][post_id] = {"channel": ch["id"], "topic": topic, "status": "pending",
                               "urls": urls, "caption": caption, "created": now().isoformat()}
    save_state(state)

    tg_album(paths)
    checks = data.get("check") or []
    check_txt = ("\n\nBITTE PRÜFEN:\n- " + "\n- ".join(checks)) if checks else ""
    tg_text(f"{ch['handle']} – {topic}\n\n{caption}{check_txt}\n\n"
            f"(Nach deinem Tipp wird der Post beim nächsten Lauf veröffentlicht – max. ca. 15 Minuten.)",
            buttons=[[{"text": "✅ Posten", "callback_data": f"ok:{post_id}"},
                      {"text": "🔁 Neu", "callback_data": f"re:{post_id}"},
                      {"text": "❌ Verwerfen", "callback_data": f"no:{post_id}"}]])
    return post_id


def approve_post(cfg, state, post_id):
    post = state["posts"][post_id]
    if post["status"] != "pending":
        return f"Post ist bereits {post['status']}."
    ch = cfg["channels_by_id"][post["channel"]]
    wait_public(post["urls"])
    media_id = publish_carousel(cfg, ch, post["urls"], post["caption"])
    post.update(status="published", media_id=media_id, published=now().isoformat())
    save_state(state)
    return f"Veröffentlicht auf {ch['handle']}."
