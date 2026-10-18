# Canonical examples

`fx-five-members/` is a complete, clean input batch: 5 fictional members (FX-M01..05), 6 cases, 29 originals.
It shows every content mode: written email/TXT/PDF, a supplied finished EML with a supplied PNG attachment, authored
tables and dated snapshots, one shared fee invoice attached twice, one not-held attachment, a reversal, a closed and
reopened case, a repeat case, a lookalike, an uncertain case. It also has private events, expected judgements and
leak terms.

It is the test fixture for `tests/test_authored_*.py` and is **excluded from the corpus** (`purpose: fixture`,
`FX-` identities). Copy its shapes; do not copy its people or stories. If you change it, rerun the tests.

    .venv/bin/python -m sdgen authored-check authoring-input/examples/fx-five-members
