"""Prüft die Einrichtung und zeigt fehlende IDs an. Start über GitHub: Actions -> "Setup prüfen" -> Run workflow."""
import os

import requests

from autopilot.core import _graph, load_config


def ok(msg):
    print(f"[OK]     {msg}")


def bad(msg):
    print(f"[FEHLT]  {msg}")


def main():
    cfg = load_config()

    print("\n== 1) Claude-API")
    ok("ANTHROPIC_API_KEY gesetzt") if os.getenv("ANTHROPIC_API_KEY") else bad("Secret ANTHROPIC_API_KEY anlegen")

    print("\n== 2) Telegram")
    tok = os.getenv("TELEGRAM_BOT_TOKEN")
    if not tok:
        bad("Secret TELEGRAM_BOT_TOKEN anlegen")
    else:
        me = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=20).json()
        ok(f"Bot gefunden: @{me['result']['username']}") if me.get("ok") else bad("Bot-Token ungültig")
        if not os.getenv("TELEGRAM_CHAT_ID"):
            upd = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=20).json()
            chats = {str(u["message"]["chat"]["id"]) for u in upd.get("result", []) if "message" in u}
            if chats:
                bad(f"Secret TELEGRAM_CHAT_ID anlegen mit Wert: {', '.join(chats)}")
            else:
                bad("TELEGRAM_CHAT_ID fehlt: Schreib deinem Bot 'hallo' und starte die Prüfung erneut.")
        else:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data={"chat_id": os.getenv("TELEGRAM_CHAT_ID"), "text": "✅ Setup-Test erfolgreich"},
                              timeout=20).json()
            ok("Testnachricht an Telegram gesendet") if r.get("ok") else bad("Senden an Telegram fehlgeschlagen")

    print("\n== 3) Instagram")
    for ch in cfg["channels"]:
        if not ch.get("active", True):
            print(f"[--]     {ch['id']}: pausiert (active: false)")
            continue
        token = os.getenv(ch["token_env"])
        if not token:
            bad(f"{ch['id']}: Secret {ch['token_env']} anlegen")
            continue
        if not str(ch.get("ig_user_id", "")).isdigit():
            bad(f"{ch['id']}: ig_user_id in config.yaml fehlt")
            continue
        r = requests.get(_graph(cfg, f"/{ch['ig_user_id']}"),
                         params={"fields": "username", "access_token": token}, timeout=20).json()
        if "error" in r:
            bad(f"{ch['id']}: Zugriff fehlgeschlagen – {r['error'].get('message')}")
        else:
            ok(f"{ch['id']}: verbunden mit @{r.get('username')}")

    print("\n== 4) GitHub Pages")
    url = cfg["public_base_url"].rstrip("/") + "/ping.txt"
    try:
        r = requests.get(url, timeout=20)
        ok(f"{url} erreichbar") if r.ok else bad(f"{url} liefert {r.status_code} – Pages aktiviert? public_base_url richtig?")
    except Exception as e:
        bad(f"{url} nicht erreichbar: {e}")

    print("\n== 5) Schalter")
    ok("enabled: true – Autopilot läuft") if cfg.get("enabled") else bad("Wenn alles OK ist: in config.yaml enabled: true setzen")
    print()


if __name__ == "__main__":
    main()
