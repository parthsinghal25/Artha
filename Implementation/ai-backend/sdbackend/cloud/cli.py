"""`python -m sdbackend cloud <command>`: operator commands for the hosted read demo (no model calls, ever).

    export          consistent deployment export of one complete publication (default: the reviewed pilot)
    restore         verified restore of a local export into an empty directory
    upload          create-only upload of an export to private Storage, read back and verified -> upload record
    activate        verify an uploaded record by exact generation, then CAS the Firestore active pointer
    rollback        verify the previous record, then CAS it back to active
    status          print the Firestore active pointer
    verify-remote   re-download a record by exact generation and verify it (no pointer change)

`upload`/`activate`/`rollback`/`status`/`verify-remote` need Google credentials (operator ADC) and
google-cloud-storage/-firestore (requirements/cloud.lock). `export`/`restore` are local only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..config import BACKEND_ROOT

DEFAULT_OUT = BACKEND_ROOT / "var" / "cloud-exports"
BUCKET = "superdashboard-c2a6c144-artifacts"


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _record(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _clients(args):
    from ..api.state import firestore_client
    from .remote import GcsObjectStore
    return firestore_client(), GcsObjectStore(args.bucket)


def main(argv: list[str]) -> int:
    from .publication import PILOT, ExportRefused, RestoreRefused, export_serving_publication, restore_export
    from .remote import ActivationRefused
    p = argparse.ArgumentParser(prog="sdbackend cloud")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--store", default=PILOT["store"])
    e.add_argument("--corpus", default=PILOT["corpus"])
    e.add_argument("--policy", default=PILOT["policy"])
    e.add_argument("--out", default=str(DEFAULT_OUT))
    e.add_argument("--expect-pilot", action="store_true",
                   help="require exactly the reviewed pilot IDs/schema/coverage (default when --store is the pilot)")
    e.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                   help="required binding, e.g. publication_id=IPUB-…; repeatable")
    e.add_argument("--allow-pending", action="store_true",
                   help="export a publication with pending eligible cases as a disclosed partial publication")
    r = sub.add_parser("restore")
    r.add_argument("export_dir")
    r.add_argument("--target", required=True)
    r.add_argument("--manifest-sha256")
    for name in ("upload", "activate", "rollback", "status", "verify-remote"):
        s = sub.add_parser(name)
        s.add_argument("--bucket", default=os.environ.get("ARTHA_BUCKET", BUCKET))
        s.add_argument("--prefix", default=os.environ.get("SDB_FIRESTORE_PREFIX", "artha"))
        if name == "upload":
            s.add_argument("export_dir")
            s.add_argument("--record-out", help="where to write the upload record (default <export_dir>.upload.json)")
        if name in ("activate", "verify-remote"):
            s.add_argument("record", help="upload record JSON written by `upload`")
        if name in ("activate", "rollback"):
            s.add_argument("--expected-generation", type=int, required=True,
                           help="current pointer generation (0 when no pointer exists yet); see `status`")
            s.add_argument("--note", default="")
    a = p.parse_args(argv)
    try:
        if a.cmd == "export":
            expect = {}
            if a.expect_pilot or (Path(a.store).resolve() == (BACKEND_ROOT / PILOT["store"]).resolve() and not a.expect):
                expect = dict(PILOT)
            for kv in a.expect:
                k, v = kv.split("=", 1)
                expect[k] = int(v) if v.isdigit() else v
            _print(export_serving_publication(Path(a.store), Path(a.corpus), Path(a.policy), Path(a.out),
                                              expect=expect, allow_pending=a.allow_pending))
        elif a.cmd == "restore":
            _print(restore_export(Path(a.export_dir), Path(a.target), manifest_sha256=a.manifest_sha256))
        elif a.cmd == "upload":
            from .remote import GcsObjectStore, upload_export
            rec = upload_export(GcsObjectStore(a.bucket), Path(a.export_dir))
            out = Path(a.record_out or f"{Path(a.export_dir).resolve()}.upload.json")
            out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            _print({"record_file": str(out), **rec})
        elif a.cmd == "verify-remote":
            from .remote import GcsObjectStore, verify_remote
            _print(verify_remote(GcsObjectStore(a.bucket), _record(a.record)))
        elif a.cmd == "status":
            from ..api.state import firestore_client
            from .remote import pointer_status
            _print(pointer_status(firestore_client(), a.prefix) or {"active": None, "generation": 0})
        elif a.cmd == "activate":
            from .remote import activate
            fs, store = _clients(a)
            _print(activate(fs, store, _record(a.record), expected_generation=a.expected_generation,
                            prefix=a.prefix, note=a.note))
        elif a.cmd == "rollback":
            from .remote import rollback
            fs, store = _clients(a)
            _print(rollback(fs, store, expected_generation=a.expected_generation, prefix=a.prefix, note=a.note))
    except (ExportRefused, RestoreRefused, ActivationRefused) as exc:
        _print({"refused": type(exc).__name__, "reason": str(exc)})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
