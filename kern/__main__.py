"""
Jarvis V2 Entrypoint — python -m kern
"""
import logging
import sys

from kern.db import init_db, is_configured
from kern.onboarding import run_onboarding
from kern.loop import run_loop


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if "--debug" in sys.argv:
        logging.getLogger("kern").setLevel(logging.DEBUG)

    init_db()

    if not is_configured():
        run_onboarding()

    run_loop()


if __name__ == "__main__":
    main()
