"""The authored path's profiles must equal the ACTIVE backend's: column aliases, financial kinds/categories and work
phases are re-read from the backend source (ast, no import) so any drift fails here."""
import ast
from pathlib import Path

import pytest

from sdgen.authored import events

ENGINE = Path(__file__).resolve().parents[2] / "ai-backend/sdbackend/engine"
needs = pytest.mark.skipif(not ENGINE.exists(), reason="backend source not present")


def literal(path, name):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


@needs
def test_ledger_and_work_column_aliases_match_backend():
    assert literal(ENGINE / "native_ledger.py", "COLUMNS") == events.BACKEND_LEDGER_COLUMNS
    assert literal(ENGINE / "native_work.py", "COLUMNS") == events.BACKEND_WORK_COLUMNS
    req = literal(ENGINE / "native_ledger.py", "REQUIRED")
    roles = {r: events.BACKEND_LEDGER_COLUMNS[r][0] for r in req}
    assert set(roles.values()) <= set(events.PROFILES["financial"]["required"])


@needs
def test_states_categories_phases_are_the_backend_canonical_values():
    kinds = literal(ENGINE / "resolvers.py", "KIND_MAP")
    cats = literal(ENGINE / "resolvers.py", "CATEGORY_MAP")
    assert set(events.FIN_STATES) == set(kinds.values())
    assert set(events.FIN_CATEGORIES) == set(cats.values())
    assert set(events.WORK_PHASES) == literal(ENGINE / "native_work.py", "PHASES") == literal(ENGINE / "resolvers.py", "WORK_PHASES")
    assert {"recorded"} <= literal(ENGINE / "native_work.py", "RECORDED")
    assert {"planned", "estimate"} <= literal(ENGINE / "native_work.py", "ESTIMATE")
