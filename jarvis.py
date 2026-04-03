#!/usr/bin/env python3
"""
Kern-Jarvis V2 — Haupteinstiegspunkt
"""
import sys
from kern.db import init_db, is_configured


def main():
    init_db()

    if not is_configured():
        from kern.onboarding import run_onboarding
        run_onboarding()

    from kern.loop import run_loop
    run_loop()


if __name__ == "__main__":
    main()
