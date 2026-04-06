"""
Jarvis V2 Entrypoint — python -m kern
"""
import logging
import sys


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if "--debug" in sys.argv:
        logging.getLogger("kern").setLevel(logging.DEBUG)

    from kern.db import get_config, init_db, is_configured
    init_db()

    if not is_configured():
        from kern.onboarding import run_onboarding
        run_onboarding()

    telegram_token = get_config("telegram_token")
    if telegram_token:
        from kern.telegram import start as start_telegram
        start_telegram(telegram_token)

    if not sys.stdin.isatty():
        # Headless mode — kein Terminal, nur Telegram
        if not telegram_token:
            print("Kein TTY und kein Telegram-Token. Beende.")
            sys.exit(1)
        print("Headless mode — Telegram bot läuft.")
        import threading
        threading.Event().wait()
        return

    from kern.loop import run_loop
    run_loop()


if __name__ == "__main__":
    main()
