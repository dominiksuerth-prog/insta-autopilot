"""Instagram-Autopilot (GitHub-Variante):
Themen -> Claude -> Slides (JPG) -> Freigabe im Cockpit/Telegram -> Instagram zum geplanten Termin.
Zugangsschlüssel kommen ausschließlich aus Umgebungsvariablen (GitHub Secrets) – nie aus Dateien im Repo.
"""
import io
import json
import os
import pathlib
import re
import time
import uuid
from datetime import datetime, timedelta
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

WEEKDAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]
CHANNEL_DEFAULTS = {"active": False, "language": "de", "token_env": "IG_TOKEN_STEHTDIRZU",
                    "days": ["mo", "di", "do", "fr"], "time": "18:00", "autoplan_days": 7, "cta_hint": ""}


def load_config():
    """Globale Einstellungen aus config.yaml, Kanäle aus channels/<id>/settings.json."""
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    channels = []
    for sf in sorted((ROOT / "channels").glob("*/settings.json")):
        ch = dict(CHANNEL_DEFAULTS, **json.loads(sf.read_text(encoding="utf-8")))
        ch["id"] = sf.parent.name
        channels.append(ch)
    cfg["channels"] = channels
    cfg["channels_by_id"] = {c["id"]: c for c in channels}
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

PROMPT = """Du schreibst einen Instagram-Karussell-Post für den Kanal {handle}.

THEMA: {topic}

STIL DES KANALS:
{style}

FAKTENBASIS (nur diese Zahlen, Beträge, Fristen und Prozentwerte verwenden):
{facts}

REGELN
- {lang_rule} Kurz und konkret. Keine Emojis auf den Slides.
- 5 bis 7 Slides. Slide 1 hat type "cover", die letzte type "cta".
- Dazwischen type "point" (eine Aussage mit Erklärung) oder "list" (2 bis 4 kurze Punkte).
- Bei "point" kannst du "contra" setzen, wenn du einen Mythos/Irrtum widerlegst (contra = der Irrtum in Anführungszeichen).
- Titel max. 60 Zeichen, body max. 220 Zeichen, Listenpunkte max. 70 Zeichen.
- Markiere im Titel genau ein Schlüsselwort mit *Sternchen* (wird farbig hervorgehoben).
- Jede Zahl, die NICHT in der Faktenbasis steht, lässt du weg. Wenn du eine Aussage für unsicher hältst, nimm sie in "check" auf.
- Keine Rechts-, Steuer- oder Anlageberatung im Einzelfall: allgemeine Infos, keine Garantien.
- Caption: 500 bis 1200 Zeichen. Erste Zeile = Hook. Dann Kernaussage. Dann Aufforderung zum Speichern/Teilen.
  {cta_hint}
- 5 bis 8 passende Hashtags in der Sprache des Kanals.

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


LANG_RULES = {
    "de": "Alle Texte auf Deutsch, du-Form.",
    "en": "Write ALL slide texts, caption and hashtags in natural, simple English (address the reader as 'you').",
}

LABELS = {
    "de": {"lang": "de", "swipe": "Swipe →", "myth": "Irrtum", "fact": "Fakt", "cta": "Speichern & teilen · Folge"},
    "en": {"lang": "en", "swipe": "Swipe →", "myth": "Myth", "fact": "Study", "cta": "Save & share · Follow"},
}

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


def generate_post(cfg, ch, topic, hint=""):
    import anthropic

    chdir = ROOT / "channels" / ch["id"]
    prompt = PROMPT.format(
        handle=ch["handle"],
        topic=topic,
        style=(chdir / "style.md").read_text(encoding="utf-8"),
        facts=(chdir / "facts.md").read_text(encoding="utf-8"),
        cta_hint=ch.get("cta_hint", ""),
        lang_rule=LANG_RULES.get(ch.get("language", "de"), LANG_RULES["de"]),
    )
    if hint:
        prompt += f"\n\nZUSÄTZLICHER WUNSCH ZU DIESEM POST (hat Vorrang vor dem Stil, nicht vor den Regeln): {hint}"
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
    t = dict(LABELS.get(ch.get("language", "de"), LABELS["de"]), **(ch.get("labels") or {}))
    theme = Markup((ROOT / "channels" / ch["id"] / "theme.css").read_text(encoding="utf-8"))

    outdir = ROOT / "docs" / "insta" / ch["id"] / post_id
    outdir.mkdir(parents=True, exist_ok=True)
    slides = data["slides"]
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1350})
        for i, s in enumerate(slides, 1):
            html = tpl.render(s=s, i=i, n=len(slides), handle=ch["handle"], theme=theme, t=t)
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
    """Nach dem Push kann es kurz dauern, bis die Bilder öffentlich erreichbar sind."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if all(requests.head(u, timeout=20).status_code == 200 for u in urls):
            return
        time.sleep(15)
    raise RuntimeError("Bilder sind noch nicht öffentlich erreichbar – nächster Lauf versucht es erneut.")


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
    if len(paths) == 1:
        with open(paths[0], "rb") as f:
            _tg("sendPhoto", data={"chat_id": os.getenv("TELEGRAM_CHAT_ID")}, files={"photo": f})
        return
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


# ---------------------------------------------------------------------------- Planung

def parse_dt(v):
    return datetime.fromisoformat(v) if v else None


def slots(ch, start, days_ahead):
    """Posting-Termine eines Kanals ab `start` für `days_ahead` Tage."""
    hh, mm = (int(x) for x in str(ch.get("time", "18:00")).split(":"))
    days = {WEEKDAYS.index(d) for d in ch.get("days", []) if d in WEEKDAYS}
    out = []
    for n in range(days_ahead + 1):
        d = (start + timedelta(days=n)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if d.weekday() in days and d > start:
            out.append(d)
    return out


def taken_slots(state, ch_id):
    return {p["scheduled"][:16] for p in state["posts"].values()
            if p["channel"] == ch_id and p.get("scheduled") and p["status"] in ("draft", "approved", "published")}


def is_reel_channel(ch):
    return ch.get("format") == "reel_aktien"


def build_post(cfg, ch, topic, hint="", state=None):
    """Text mit Claude schreiben und Slides rendern (bzw. Aktien-Reel). Gibt (post_id, Datensatz) zurück."""
    post_id = now().strftime("%Y%m%d-%H%M") + "-" + uuid.uuid4().hex[:6]
    if is_reel_channel(ch):
        from .aktien import pipeline
        wish = " – ".join(x for x in (topic, hint) if x)
        idea = pipeline.plan_idea(cfg, ch, state or load_state(), wish)
        fields = pipeline.build_reel(cfg, ch, post_id, idea)
        return post_id, {"channel": ch["id"], "status": "draft", "created": now().isoformat(), **fields}
    data = generate_post(cfg, ch, topic, hint)
    _paths, urls = render_post(cfg, ch, data, post_id)
    return post_id, {"channel": ch["id"], "topic": topic, "status": "draft", "urls": urls,
                     "caption": build_caption(data), "check": data.get("check") or [],
                     "created": now().isoformat()}


def plan_channel(cfg, state, ch, days_ahead=14, limit=None):
    """Füllt freie Termine der nächsten `days_ahead` Tage mit Entwürfen. Gibt neue Post-IDs zurück."""
    new_ids = []
    taken = taken_slots(state, ch["id"])
    for slot in slots(ch, now(), days_ahead):
        if limit is not None and len(new_ids) >= limit:
            break
        if slot.isoformat()[:16] in taken:
            continue
        if is_reel_channel(ch):
            try:
                topic = next_topic(state, ch)   # optionale Wunschliste in topics.txt
            except RuntimeError:
                topic = ""
        else:
            topic = next_topic(state, ch)
        post_id, post = build_post(cfg, ch, topic, state=state)
        post["scheduled"] = slot.isoformat()
        state["posts"][post_id] = post
        save_state(state)  # das Thema gilt erst jetzt als verbraucht
        new_ids.append(post_id)
    return new_ids


def regenerate_post(cfg, state, post_id, topic=None, hint=""):
    """Schreibt einen Entwurf neu (gleicher Termin). Der alte bekommt den Status 'replaced'."""
    old = state["posts"][post_id]
    if old["status"] not in ("draft", "approved"):
        raise ValueError(f"Post ist bereits {old['status']}")
    ch = cfg["channels_by_id"][old["channel"]]
    if is_reel_channel(ch):
        new_id, post = build_post(cfg, ch, topic if topic and topic != old["topic"] else "", hint, state=state)
    else:
        new_id, post = build_post(cfg, ch, topic or old["topic"], hint)
    post["scheduled"] = old.get("scheduled")
    old["status"] = "replaced"
    old["replaced_by"] = new_id
    state["posts"][new_id] = post
    save_state(state)
    return new_id


# ---------------------------------------------------------------------------- Entscheidungen

def log(state, msg):
    """Kurzes Protokoll für das Cockpit (letzte 80 Einträge)."""
    state.setdefault("log", []).insert(0, {"t": now().isoformat(timespec="seconds"), "msg": msg})
    del state["log"][80:]
    save_state(state)


def apply_decision(state, d):
    """Freigeben / zurücknehmen / verwerfen / Text oder Termin ändern. Gibt eine Meldung zurück."""
    post = state["posts"].get(d.get("post_id", ""))
    if not post:
        return None
    if post["status"] not in ("draft", "approved"):
        return f"{post['topic']}: bereits {post['status']} – nichts geändert"
    a = d.get("action")
    if d.get("caption"):
        post["caption"] = d["caption"]
    if d.get("scheduled"):
        post["scheduled"] = d["scheduled"]
        post.pop("reminded", None)
    if a == "approve":
        post["status"] = "approved"
    elif a == "unapprove":
        post["status"] = "draft"
    elif a == "reject":
        post["status"] = "rejected"
    post.pop("error", None)
    save_state(state)
    word = {"approve": "freigegeben", "unapprove": "zurück auf Entwurf", "reject": "verworfen"}.get(a, "geändert")
    return f"{post['topic']}: {word}"


# ---------------------------------------------------------------------------- Veröffentlichen

def publish_post(cfg, state, post_id):
    post = state["posts"][post_id]
    ch = cfg["channels_by_id"][post["channel"]]
    wait_public(post["urls"])
    if post.get("video"):
        from .aktien import pipeline
        media_id = pipeline.publish_reel(cfg, ch, post, get_token(ch), _graph, _ig_call, _wait_ready)
    else:
        media_id = publish_carousel(cfg, ch, post["urls"], post["caption"])
    post.update(status="published", media_id=media_id, published=now().isoformat())
    post.pop("error", None)
    save_state(state)
    return f"Veröffentlicht auf {ch['handle']}: {post['topic']}"


def publish_due(cfg, state):
    """Veröffentlicht alle freigegebenen Posts, deren Termin erreicht ist."""
    msgs = []
    for pid, p in sorted(state["posts"].items(), key=lambda kv: kv[1].get("scheduled") or ""):
        if p["status"] != "approved" or not p.get("scheduled") or parse_dt(p["scheduled"]) > now():
            continue
        ch = cfg["channels_by_id"].get(p["channel"])
        if not ch or not ch.get("active"):
            continue
        try:
            msgs.append("✅ " + publish_post(cfg, state, pid))
        except Exception as e:
            if p.get("error") != str(e):
                p["error"] = str(e)
                save_state(state)
                msgs.append(f"⚠️ {ch['handle']} – {p['topic']}: {e}")
    return msgs


def remind_drafts(cfg, state, hours=20):
    """Schickt nicht freigegebene Entwürfe, deren Termin in den nächsten `hours` Stunden liegt, per Telegram."""
    limit = now() + timedelta(hours=hours)
    for pid, p in sorted(state["posts"].items(), key=lambda kv: kv[1].get("scheduled") or ""):
        if p["status"] != "draft" or p.get("reminded") or not p.get("scheduled"):
            continue
        if parse_dt(p["scheduled"]) > limit:
            continue
        ch = cfg["channels_by_id"].get(p["channel"])
        if not ch or not ch.get("active"):
            continue
        send_for_review(ch, pid, p)
        p["reminded"] = True
        save_state(state)


def send_for_review(ch, post_id, post):
    paths = [ROOT / "docs" / "insta" / post["channel"] / post_id / u.rsplit("/", 1)[-1] for u in post["urls"]]
    if paths and all(p.exists() for p in paths):
        tg_album(paths)
    when = parse_dt(post["scheduled"]).strftime("%d.%m. %H:%M") if post.get("scheduled") else "sofort"
    checks = post.get("check") or []
    check_txt = ("\n\nBITTE PRÜFEN:\n- " + "\n- ".join(checks)) if checks else ""
    if post.get("video_url"):
        check_txt += f"\n\n🎬 Video: {post['video_url']}"
    tg_text(f"{ch['handle']} – noch nicht freigegeben, Termin {when}\n{post['topic']}\n\n{post['caption']}{check_txt}",
            buttons=[[{"text": "✅ Freigeben", "callback_data": f"ok:{post_id}"},
                      {"text": "🔁 Neu", "callback_data": f"re:{post_id}"},
                      {"text": "❌ Verwerfen", "callback_data": f"no:{post_id}"}]])


def prune_old(state, days=30):
    """Löscht Bilder erledigter Posts, die älter als `days` Tage sind (Instagram hat sie längst kopiert)."""
    import shutil
    cutoff = now().timestamp() - days * 86400
    for pid, p in state["posts"].items():
        if p["status"] in ("draft", "approved") or p.get("pruned"):
            continue
        if datetime.fromisoformat(p["created"]).timestamp() < cutoff:
            shutil.rmtree(ROOT / "docs" / "insta" / p["channel"] / pid, ignore_errors=True)
            if p.get("video"):
                from .aktien import pipeline
                pipeline.delete_video(p["video"])
            p["pruned"] = True
    save_state(state)


def migrate_state(state):
    """Alte Telegram-Entwürfe (Status 'pending') ins neue Modell übernehmen."""
    changed = False
    for p in state["posts"].values():
        if p["status"] == "pending":
            p["status"] = "draft"
            p.setdefault("scheduled", p.get("created"))
            p["reminded"] = True
            changed = True
    if changed:
        save_state(state)
    return state


# ---------------------------------------------------------------------------- Neuer Kanal

CHANNEL_PROMPT = """Du richtest einen neuen, faktenbasierten Instagram-Karussell-Kanal ein.

Handle: {handle}
Name/Idee: {name}
Sprache der Posts: {language}
Nische, Zielgruppe, Ziel: {brief}
Gewünschter Ton: {tone}

Liefere das Ergebnis über das Werkzeug "save_channel":
- style: Stil-Leitfaden für die Texte (in der Sprache der Posts, 8 bis 14 Zeilen): Zielgruppe, Ton, Aufbau eines Posts,
  was immer gilt, was nie vorkommen darf. Keine Rechts-, Steuer-, Medizin- oder Anlageberatung im Einzelfall.
- topics: 30 konkrete Post-Themen in der Sprache der Posts, jedes eine Zeile, abwechslungsreich, mit starkem Hook.
- facts: Vorschlag einer Faktenbasis in der Sprache der Posts als Stichpunkte – nur Aussagen, bei denen du dir sehr
  sicher bist, jeweils mit Quelle (Gesetz, Studie, Behörde). Beginne JEDE Zeile mit [PRÜFEN].
"""

CHANNEL_TOOL = {
    "name": "save_channel",
    "description": "Speichert die Grunddateien eines neuen Kanals.",
    "input_schema": {"type": "object", "properties": {
        "style": {"type": "string"},
        "topics": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": {"type": "string"}},
    }, "required": ["style", "topics", "facts"]},
}


def theme_css(bg, fg, accent, font="Space Grotesk"):
    return f"""/* automatisch erzeugt */
:root {{
  --bg: {bg}; --fg: {fg}; --muted: color-mix(in srgb, {fg} 65%, {bg});
  --head: '{font}'; --body: 'Inter';
  --hl-fg: {accent}; --hl-bg: transparent;
  --accent: {fg}; --accent-fg: {bg};
  --card: color-mix(in srgb, {fg} 6%, {bg}); --card-fg: {fg}; --card-border: none;
  --card2: {fg}; --card2-fg: {bg}; --card2-border: none;
  --num: {accent}; --bad: #C0392B; --good: {accent};
}}
.card.alt .hl {{ color: {accent}; }}
"""


def create_channel(cfg, cid, handle, name, language, brief, tone, colors, days, time_, font="Space Grotesk"):
    """Legt channels/<cid>/ mit Claude-Entwürfen an. Der Kanal bleibt inaktiv, bis Fakten geprüft
    und das Instagram-Konto verknüpft sind."""
    import anthropic
    if not re.fullmatch(r"[a-z0-9_]{3,30}", cid):
        raise ValueError("Kanal-ID: nur a-z, 0-9 und _ (3 bis 30 Zeichen)")
    d = ROOT / "channels" / cid
    if d.exists():
        raise ValueError(f"Den Kanal {cid} gibt es schon")
    prompt = CHANNEL_PROMPT.format(handle=handle, name=name, language=language, brief=brief, tone=tone)
    msg = anthropic.Anthropic().messages.create(
        model=cfg.get("claude_model", "claude-sonnet-5-5"), max_tokens=6000,
        tools=[CHANNEL_TOOL], messages=[{"role": "user", "content": prompt}])
    data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if not data:
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = _extract_json(text)
    bg, fg, accent = (list(colors) + ["#F5F1EA", "#14161A", "#C8553D"])[:3]
    d.mkdir(parents=True)
    (d / "style.md").write_text(data["style"].strip() + "\n", encoding="utf-8")
    (d / "topics.txt").write_text("# Eine Zeile = ein Post.\n" + "\n".join(t.strip() for t in data["topics"]) + "\n",
                                  encoding="utf-8")
    (d / "facts.md").write_text(
        "# Faktenbasis – UNGEPRÜFT. Jede Zeile mit [PRÜFEN] an der Quelle prüfen, dann die Markierung löschen.\n"
        "# Nur was hier steht, darf als Zahl in Posts auftauchen.\n\n"
        + "\n".join("- " + f.strip().lstrip("- ") for f in data["facts"]) + "\n", encoding="utf-8")
    (d / "theme.css").write_text(theme_css(bg, fg, accent, font), encoding="utf-8")
    settings = {"name": name, "handle": handle, "active": False, "language": language,
                "ig_user_id": "", "token_env": "IG_TOKEN_STEHTDIRZU", "days": days, "time": time_,
                "autoplan_days": 7, "cta_hint": ""}
    (d / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return d


TOPICS_PROMPT = """Schlage {n} neue Themen für Instagram-Karussell-Posts des Kanals {handle} vor.

STIL DES KANALS:
{style}

FAKTENBASIS (jedes Thema muss sich vollständig mit diesen Fakten belegen lassen – keine Themen, für die Zahlen fehlen):
{facts}

BISHERIGE THEMEN (nicht wiederholen, auch nicht sinngemäß):
{used}

Regeln: Sprache der Posts wie im Stil. Jede Zeile ein Thema mit starkem Hook, max. 90 Zeichen, abwechslungsreich
(Mythos, Liste, Rechenbeispiel, Frage, Fehler). Gib die Themen über das Werkzeug "save_topics" zurück."""

TOPICS_TOOL = {
    "name": "save_topics",
    "description": "Speichert neue Themen.",
    "input_schema": {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "string"}}},
                     "required": ["topics"]},
}


def suggest_topics(cfg, ch, n=20):
    """Hängt n neue, von der Faktenbasis gedeckte Themen an topics.txt an. Gibt die Anzahl zurück."""
    import anthropic
    chdir = ROOT / "channels" / ch["id"]
    tf = chdir / "topics.txt"
    lines = tf.read_text(encoding="utf-8").splitlines()
    used = [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]
    prompt = TOPICS_PROMPT.format(n=n, handle=ch["handle"], used="\n".join(used) or "-",
                                  style=(chdir / "style.md").read_text(encoding="utf-8"),
                                  facts=(chdir / "facts.md").read_text(encoding="utf-8"))
    msg = anthropic.Anthropic().messages.create(
        model=cfg.get("claude_model", "claude-sonnet-5-5"), max_tokens=3000,
        tools=[TOPICS_TOOL], messages=[{"role": "user", "content": prompt}])
    data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if not data:
        data = _extract_json("".join(b.text for b in msg.content if getattr(b, "type", "") == "text"))
    seen = {u.lower() for u in used}
    new = [t.strip() for t in data.get("topics", []) if t.strip() and t.strip().lower() not in seen]
    with tf.open("a", encoding="utf-8") as f:
        f.write(("" if not lines or lines[-1] == "" else "\n") + "\n".join(new) + "\n")
    return len(new)
