"""Erzeugt den nächsten Post eines Kanals und schickt ihn zur Freigabe.

    python run_create.py --channel heizkosten
    python run_create.py --channel all
    python run_create.py --channel stehtdirzu --topic "Eigenes Thema"
"""
import argparse
import sys
import traceback

from autopilot.core import create_post, load_config, load_state, prune_old, tg_text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--topic", default=None)
    args = ap.parse_args()

    cfg = load_config()
    if not cfg.get("enabled"):
        print("Autopilot ist in config.yaml noch nicht eingeschaltet (enabled: false) – nichts zu tun.")
        return
    prune_old(load_state())
    ids = ([c["id"] for c in cfg["channels"] if c.get("active", True)]
           if args.channel == "all" else [args.channel])
    failed = False
    for cid in ids:
        ch = cfg["channels_by_id"][cid]
        state = load_state()
        try:
            post_id = create_post(cfg, state, ch, args.topic)
            print(f"OK {cid}: {post_id}")
        except Exception as e:
            failed = True
            traceback.print_exc()
            try:
                tg_text(f"⚠️ Fehler bei {ch['handle']}: {e}")
            except Exception:
                pass
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
