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

    from kern.db import init_db, is_configured
    init_db()

    if not is_configured():
        from kern.onboarding import run_onboarding
        run_onboarding()

    from kern.loop import run_loop
    run_loop()


if __name__ == "__main__":
    main()
