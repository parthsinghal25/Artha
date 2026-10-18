"""Stage -> review material -> verify -> atomic publish. The only path that creates a delivered package with review files."""
import shutil

from .build import Batch
from .checks import run_checks
from .closure import write_closure
from .economics import write_economics
from .paths import AUTHORED
from .review import write_review_material


def release(batch_key, authored_version, package_version, out_root=None, rulings=(), tests_junit=None, snapshots=None,
            author_wall_clock=None):
    batch = Batch(batch_key, authored_version, package_version, out_root, rulings=list(rulings))
    batch._assert_target_free()
    batch.render_assets()
    batch.render_cards()
    batch.write_stage()
    batch.write_reading_copies()
    rev = batch.stage / "restricted/review"
    rev.mkdir(parents=True, exist_ok=True)
    write_review_material(batch.stage, batch.base_dir if batch.repair else None, rulings)
    log = AUTHORED / "case-11" / f"repair-log-v{package_version}.json"
    if log.exists():
        shutil.copy(log, rev / "repair-log.json")
    res = run_checks(batch.stage, write=True, deep=True, base_root=batch.root, label=batch.dir.name)
    hard = sum(v["fail"] for v in res["summary"]["by_layer"].values())
    closure_ok = True
    if batch.repair and tests_junit:
        _, closure_ok = write_closure(batch.stage, batch.base_dir, tests_junit, res["summary"], snapshots, final_name=batch.dir.name)
    write_economics(batch.stage, {"author_wall_clock": author_wall_clock})
    published = False
    if not hard and closure_ok:
        batch.publish()
        published = True
    return batch, res, closure_ok, published
