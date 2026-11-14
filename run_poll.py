"""Liest deine Telegram-Antworten ab und führt sie aus (läuft alle 15 Minuten per GitHub Actions).

Knöpfe:  ✅ Posten  |  🔁 Neu (gleiches Thema neu schreiben)  |  ❌ Verwerfen
Befehle: /post heizkosten   -> neuen Post erzeugen
         /status            -> offene Posts anzeigen
"""
import os
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


def main():
    cfg = load_config()
    if not cfg.get("enabled") or not ALLOWED_CHAT:
        print("Noch nicht eingeschaltet (enabled: false oder TELEGRAM_CHAT_ID fehlt) – nichts zu tun.")
        return
    state = load_state()
    params = {"timeout": 0}
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
    print(f"{len(updates)} Telegram-Updates verarbeitet.")


if __name__ == "__main__":
    main()
