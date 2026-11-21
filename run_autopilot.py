"""Der Autopilot. Läuft stündlich und sofort, wenn du im Cockpit etwas tust.

Ablauf pro Durchgang:
  1. Neues aus dem Repo holen (Aufträge aus dem Cockpit liegen in data/inbox/)
  2. Aufträge abarbeiten: freigeben, verwerfen, verschieben, neu schreiben, vorproduzieren, Kanal anlegen
  3. Telegram-Knöpfe auswerten
  4. Freigegebene Posts veröffentlichen, deren Termin erreicht ist
  5. Nicht freigegebene Posts kurz vor dem Termin per Telegram erinnern
  6. Einmal am Tag: Lücken der nächsten Tage mit Entwürfen füllen
  7. Alles ins Repo schieben

Solange in den nächsten Minuten etwas ansteht (Erinnerung offen, Termin naht), bleibt er wach.

    python run_autopilot.py --minutes 50
"""
import argparse
import json
import os
import subprocess
import time
import traceback

from autopilot import core
from autopilot.core import (ROOT, _tg, apply_decision, create_channel, load_config, load_state, log,
                            migrate_state, now, parse_dt, plan_channel, prune_old, publish_due,
                            regenerate_post, remind_drafts, save_state, send_for_review, tg_text)

INBOX = ROOT / "data" / "inbox"
ALLOWED_CHAT = str(os.getenv("TELEGRAM_CHAT_ID", ""))


# ---------------------------------------------------------------- Git

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT)


def sync(message="autopilot"):
    """Eigene Änderungen hochladen und Neues (z. B. Aufträge aus dem Cockpit) holen."""
    if not (ROOT / ".git").exists():
        return
    git("config", "user.name", "autopilot-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "-A", "docs", "data", "channels")
    if git("diff", "--cached", "--quiet").returncode != 0:
        git("commit", "-m", message)
    for _ in range(4):
        r = git("pull", "--rebase", "-X", "theirs")
        if r.returncode != 0:
            git("rebase", "--abort")
            print("WARNUNG: pull fehlgeschlagen:", r.stderr[-400:])
            time.sleep(5)
            continue
        if git("push").returncode == 0 or git("status", "-sb").stdout.find("ahead") < 0:
            return
        time.sleep(5)
    print("WARNUNG: git push fehlgeschlagen")


# ---------------------------------------------------------------- Aufträge aus dem Cockpit

def notify(text):
    try:
        tg_text(text)
    except Exception:
        traceback.print_exc()


def handle_job(cfg, state, job):
    a = job.get("action")
    if a in ("approve", "unapprove", "reject", "edit"):
        msg = apply_decision(state, job)
        if msg:
            log(state, msg)
        return
    if a == "regenerate":
        new_id = regenerate_post(cfg, state, job["post_id"], job.get("topic") or None, job.get("hint", ""))
        log(state, f"Neu geschrieben: {state['posts'][new_id]['topic']}")
        return
    if a == "plan":
        targets = (list(cfg["channels_by_id"]) if job.get("channel", "all") == "all" else [job["channel"]])
        for cid in targets:
            ch = cfg["channels_by_id"][cid]
            if job.get("channel", "all") == "all" and not ch.get("active"):
                continue
            ids = plan_channel(cfg, state, ch, int(job.get("days", 14)), job.get("limit"))
            log(state, f"{ch['handle']}: {len(ids)} Entwürfe vorproduziert")
            sync(f"autopilot: Entwürfe {cid}")
        return
    if a == "add_post":
        ch = cfg["channels_by_id"][job["channel"]]
        post_id, post = core.build_post(cfg, ch, job["topic"], job.get("hint", ""))
        post["scheduled"] = job.get("scheduled") or now().isoformat()
        state["posts"][post_id] = post
        save_state(state)
        log(state, f"{ch['handle']}: eigener Post erstellt – {job['topic']}")
        return
    if a == "suggest_topics":
        ch = cfg["channels_by_id"][job["channel"]]
        n = core.suggest_topics(cfg, ch, int(job.get("n", 20)))
        log(state, f"{ch['handle']}: {n} neue Themen vorgeschlagen (Cockpit → Kanäle → Themen)")
        return
    if a == "create_channel":
        d = create_channel(cfg, job["id"], job["handle"], job.get("name") or job["handle"],
                           job.get("language", "de"), job.get("brief", ""), job.get("tone", ""),
                           job.get("colors") or [], job.get("days") or ["mo", "di", "do", "fr"],
                           job.get("time") or "18:00", job.get("font") or "Space Grotesk")
        log(state, f"Kanal {job['handle']} angelegt – bitte Fakten prüfen und Instagram-ID eintragen")
        notify(f"🆕 Kanal {job['handle']} angelegt ({d.name}). Bitte im Cockpit die Fakten prüfen.")
        return
    log(state, f"Unbekannter Auftrag: {a}")


def process_inbox(cfg):
    files = sorted(INBOX.glob("*.json")) if INBOX.exists() else []
    if not files:
        return 0
    jobs = []
    for f in files:
        try:
            jobs.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            jobs.append((f, None))
    # schnelle Entscheidungen zuerst, aufwendige (Claude + Rendern) danach
    quick = ("approve", "unapprove", "reject", "edit")
    jobs.sort(key=lambda fj: (0 if fj[1] and fj[1].get("action") in quick else 1, fj[0].name))
    for f, job in jobs:
        f.unlink(missing_ok=True)
        if job is None:
            continue
        state = load_state()
        try:
            handle_job(cfg, state, job)
        except Exception as e:
            traceback.print_exc()
            state = load_state()
            log(state, f"⚠️ Fehler bei '{job.get('action')}': {e}")
            notify(f"⚠️ Cockpit-Auftrag '{job.get('action')}' fehlgeschlagen: {e}")
        if job.get("action") not in quick:
            sync(f"autopilot: {job.get('action')}")
    sync("autopilot: Cockpit-Entscheidungen")
    return len(jobs)


# ---------------------------------------------------------------- Telegram

def handle_callback(cfg, cb):
    if str(cb["message"]["chat"]["id"]) != ALLOWED_CHAT:
        return
    try:
        _tg("answerCallbackQuery", data={"callback_query_id": cb["id"]})
    except Exception:
        pass
    action, post_id = cb["data"].split(":", 1)
    state = load_state()
    post = state["posts"].get(post_id)
    if not post or post["status"] not in ("draft", "approved"):
        tg_text(f"Dieser Post ist bereits {post['status'] if post else 'unbekannt'}.")
        return
    ch = cfg["channels_by_id"][post["channel"]]
    if action == "ok":
        apply_decision(state, {"post_id": post_id, "action": "approve"})
        log(state, f"{post['topic']}: freigegeben (Telegram)")
        when = parse_dt(post.get("scheduled"))
        if when and when > now():
            tg_text(f"✅ Freigegeben – geht am {when.strftime('%d.%m. um %H:%M')} raus.")
    elif action == "no":
        apply_decision(state, {"post_id": post_id, "action": "reject"})
        log(state, f"{post['topic']}: verworfen (Telegram)")
        tg_text("❌ Verworfen.")
    elif action == "re":
        tg_text("🔁 Schreibe das Thema neu …")
        new_id = regenerate_post(cfg, state, post_id)
        state = load_state()
        log(state, f"Neu geschrieben (Telegram): {post['topic']}")
        sync("autopilot: neu geschrieben")
        send_for_review(ch, new_id, state["posts"][new_id])
        state["posts"][new_id]["reminded"] = True
        save_state(state)


def handle_message(cfg, msg):
    if str(msg["chat"]["id"]) != ALLOWED_CHAT:
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/status"):
        state = load_state()
        rows = []
        for p in sorted(state["posts"].values(), key=lambda p: p.get("scheduled") or ""):
            if p["status"] in ("draft", "approved"):
                icon = "✅" if p["status"] == "approved" else "📝"
                when = parse_dt(p.get("scheduled"))
                rows.append(f"{icon} {when.strftime('%d.%m. %H:%M') if when else '?'} {p['channel']}: {p['topic']}")
        tg_text("Geplant (✅ frei, 📝 Entwurf):\n" + ("\n".join(rows[:30]) if rows else "nichts geplant"))
    elif text.startswith("/"):
        tg_text("Planen, freigeben und Kanäle anlegen geht jetzt im Cockpit. Hier: /status")


def poll_telegram(cfg, wait=0):
    if not ALLOWED_CHAT or not os.getenv("TELEGRAM_BOT_TOKEN"):
        if wait:
            time.sleep(wait)
        return 0
    state = load_state()
    params = {"timeout": wait}
    if state.get("tg_offset"):
        params["offset"] = state["tg_offset"]
    try:
        updates = _tg("getUpdates", data=params)
    except Exception:
        traceback.print_exc()
        return 0
    for u in updates:
        state = load_state()
        state["tg_offset"] = u["update_id"] + 1
        save_state(state)  # zuerst merken, damit nichts doppelt ausgeführt wird
        try:
            if "callback_query" in u:
                handle_callback(cfg, u["callback_query"])
            elif "message" in u:
                handle_message(cfg, u["message"])
        except Exception as e:
            traceback.print_exc()
            notify(f"⚠️ Fehler: {e}")
        sync("autopilot: Telegram")
    return len(updates)


# ---------------------------------------------------------------- Hauptschleife

def autoplan(cfg):
    """Einmal pro Tag: freie Termine der nächsten Tage je aktivem Kanal mit Entwürfen füllen."""
    state = load_state()
    today = now().date().isoformat()
    if state.get("last_autoplan") == today:
        return
    state["last_autoplan"] = today
    save_state(state)
    for ch in cfg["channels"]:
        days = int(ch.get("autoplan_days") or 0)
        if not ch.get("active") or days <= 0:
            continue
        state = load_state()
        try:
            ids = plan_channel(cfg, state, ch, days)
            if ids:
                log(load_state(), f"{ch['handle']}: {len(ids)} neue Entwürfe für die nächsten {days} Tage")
                notify(f"📝 {ch['handle']}: {len(ids)} neue Entwürfe – im Cockpit ansehen und freigeben.")
        except Exception as e:
            traceback.print_exc()
            log(load_state(), f"⚠️ {ch['handle']}: Vorproduktion fehlgeschlagen – {e}")
            notify(f"⚠️ {ch['handle']}: Vorproduktion fehlgeschlagen – {e}")
        sync(f"autopilot: Entwürfe {ch['id']}")


def something_soon(cfg, minutes):
    """Lohnt es sich, wach zu bleiben? (offene Erinnerung oder Termin in den nächsten `minutes`)."""
    state = load_state()
    horizon = now().timestamp() + minutes * 60
    for p in state["posts"].values():
        ch = cfg["channels_by_id"].get(p["channel"])
        if not ch or not ch.get("active") or not p.get("scheduled"):
            continue
        t = parse_dt(p["scheduled"]).timestamp()
        if p["status"] == "approved" and t <= horizon:
            return True
        if p["status"] == "draft" and p.get("reminded") and t >= now().timestamp() - 6 * 3600:
            return True  # du könntest gleich in Telegram tippen
    return False


def next_approved(cfg):
    """Zeitpunkt (Unix) des nächsten freigegebenen, noch nicht veröffentlichten Posts eines aktiven Kanals."""
    ts = [parse_dt(p["scheduled"]).timestamp() for p in load_state()["posts"].values()
          if p["status"] == "approved" and p.get("scheduled")
          and cfg["channels_by_id"].get(p["channel"], {}).get("active")]
    return min(ts) if ts else None


def tick(cfg, first):
    state = migrate_state(load_state())
    if first:
        prune_old(state)
    process_inbox(cfg)
    poll_telegram(cfg, wait=0 if first else 25)
    for m in publish_due(cfg, load_state()):
        log(load_state(), m)
        notify(m)
    remind_drafts(cfg, load_state(), hours=int(cfg.get("remind_hours", 20)))
    sync("autopilot: Status")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=0, help="so lange wach bleiben, solange etwas ansteht")
    ap.add_argument("--wait-for-posts", type=int, default=0,
                    help="bis zu so vielen Minuten auf den nächsten freigegebenen Termin warten "
                         "(GitHub startet zeitgesteuerte Läufe oft verspätet oder gar nicht)")
    args = ap.parse_args()
    cfg = load_config()
    if not cfg.get("enabled"):
        print("Autopilot ist ausgeschaltet (config.yaml: enabled: false).")
        return
    sync("autopilot: Start")
    started = time.time()
    deadline = started + args.minutes * 60
    tick(cfg, first=True)
    autoplan(cfg)
    if args.wait_for_posts:
        nxt = next_approved(cfg)
        if nxt and nxt - started < args.wait_for_posts * 60:
            deadline = max(deadline, nxt + 120)
            print(f"Bleibe wach bis zum nächsten Termin ({(nxt - started) / 60:.0f} Min.)")
    while time.time() < deadline:
        cfg = load_config()  # Kanäle können sich im Cockpit geändert haben
        left = (deadline - time.time()) / 60
        if not something_soon(cfg, left) and not any(INBOX.glob("*.json")):
            break
        tick(cfg, first=False)
    sync("autopilot: Ende")
    state = load_state()
    state["last_run"] = now().isoformat(timespec="seconds")
    save_state(state)
    sync("autopilot: Lauf beendet")


if __name__ == "__main__":
    main()
