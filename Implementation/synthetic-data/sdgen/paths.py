from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../Artha
TOOL_ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "Data/synthesis/pilot-v2-planning-2026-10-13"
RELEASE_ROOT = ROOT / "Data/synthesis/pilot-v2-releases"
CONTRACT_MANIFEST = ROOT / "Shared/contracts/v1/MANIFEST.json"
AUTHORED = TOOL_ROOT / "authored"
