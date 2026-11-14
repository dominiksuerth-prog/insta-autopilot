"""Liest deine Telegram-Antworten ab und führt sie aus (läuft nach jedem neuen Post bis zur Freigabe, sonst stündlich).

Knöpfe:  ✅ Posten  |  🔁 Neu (gleiches Thema neu schreiben)  |  ❌ Verwerfen
Befehle: /post heizkosten   -> neuen Post erzeugen
         /status            -> offene Posts anzeigen
"""
import argparse
import os
import subprocess
import time
import traceback

from autopilot.core import (_tg, approve_post, create_post, load_config, load_state,
                            save_state, tg_text)

ALLOWED_CHAT = str(os.getenv("TELEGRAM_CHAT_ID", ""))


def handle_callback(cfg, cb):
    if str(cb["message"]["chat"]["id"]) != ALLOWED_CHAT:
        return
    try:  # Telegram lehnt zu alte Rückfragen ab – egal
        _tg("answerCallbackQuery", data={"callback_query_id": cb["id"]})
    except Exception:
        pass
    action, post_id = cb["data"].split(":", 1)
    state = load_state()
    post = state["posts"].get(post_id)
    if not post or post["status"] != "pending":
        tg_text(f"Dieser Post ist bereits {post['status'] if post else 'unbekannt'}.")
        return
    if action == "ok":
        try:
            tg_text("✅ " + approve_post(cfg, state, post_id))
        except Exception as e:
            tg_text(f"⚠️ Veröffentlichen fehlgeschlagen: {e}\nTipp erneut auf ✅, um es nochmal zu versuchen.")
            traceback.print_exc()
    elif action == "no":
        post["status"] = "rejected"
        save_state(state)
        tg_text("❌ Verworfen.")
    elif action == "re":
        post["status"] = "replaced"
        save_state(state)
        tg_text("🔁 Schreibe das Thema neu …")
        create_post(cfg, state, cfg["channels_by_id"][post["channel"]], post["topic"])


def handle_message(cfg, msg):
    if str(msg["chat"]["id"]) != ALLOWED_CHAT:
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/post"):
        parts = text.split()
        if len(parts) < 2 or parts[1] not in cfg["channels_by_id"]:
            tg_text("Nutzung: /post " + " | ".join(cfg["channels_by_id"]))
            return
        state = load_state()
        create_post(cfg, state, cfg["channels_by_id"][parts[1]])
    elif text.startswith("/status"):
        state = load_state()
        pending = [f"{p['channel']}: {p['topic']}" for p in state["posts"].values() if p["status"] == "pending"]
        tg_text("Offene Posts:\n" + ("\n".join(pending) if pending else "keine"))


def sync():
    """Änderungen (state.json, neue Slides) sofort ins Repo schieben – Bilder sind dann öffentlich."""
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True)
    git("config", "user.name", "autopilot-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "-A", "docs", "data")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return
    git("commit", "-m", "autopilot: Freigaben")
    for _ in range(3):
        git("pull", "--rebase")
        if git("push").returncode == 0:
            return
        time.sleep(5)
    print("WARNUNG: git push fehlgeschlagen")


def has_pending():
    return any(p["status"] == "pending" for p in load_state()["posts"].values())


def poll_once(cfg, wait=0):
    state = load_state()
    params = {"timeout": wait}
    if state.get("tg_offset"):
        params["offset"] = state["tg_offset"]
    updates = _tg("getUpdates", data=params)
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
            tg_text(f"⚠️ Fehler: {e}")
        sync()
    return len(updates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0,
                    help="Minuten lang auf Freigaben warten, solange Posts offen sind")
    args = ap.parse_args()
    cfg = load_config()
    if not cfg.get("enabled") or not ALLOWED_CHAT:
        print("Noch nicht eingeschaltet (enabled: false oder TELEGRAM_CHAT_ID fehlt) – nichts zu tun.")
        return
    deadline = time.time() + args.loop * 60
    total = poll_once(cfg)
    while time.time() < deadline and has_pending():
        total += poll_once(cfg, wait=25)  # Long-Polling: Telegram antwortet sofort bei deinem Tipp
    print(f"{total} Telegram-Updates verarbeitet.")


if __name__ == "__main__":
    main()
