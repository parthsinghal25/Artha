# Artha dashboard

A browser JavaScript dashboard and Python standard-library gateway. Includes overview, issues and time trends, member experience/backlog, business impact, AFCA context, and case/source/original inspection.

From the repository root: `python3 Implementation/ui/build.py` builds static and gateway artifacts. `python3 Implementation/ui/server/devserver.py` serves the unconfigured UI on port 5173. See [local setup](../../docs/LOCAL-DEVELOPMENT.md) for binding a real backend. No Node package installation is needed.

The hosted demo uses a fixed read-only identity with no login screen. User interactions query published data rather than triggering inference. Tests: `python3 tests/run_tests.py` from this directory, with an installed Chromium browser.
