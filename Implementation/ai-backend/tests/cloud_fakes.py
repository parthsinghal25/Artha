"""In-process doubles for the two Google services the hosted read path uses. EMULATED, not Google: they implement
only the document/object calls sdbackend.api.state and sdbackend.cloud.remote make, with the same semantics that
matter (create-only, update-time preconditions, exact object generations)."""
from __future__ import annotations

import copy
import itertools
import shutil
from pathlib import Path


class AlreadyExists(Exception):
    pass


class FailedPrecondition(Exception):
    pass


class _Snap:
    def __init__(self, data, update_time):
        self.exists = data is not None
        self._data = copy.deepcopy(data)
        self.update_time = update_time

    def to_dict(self):
        return copy.deepcopy(self._data)


class _Doc:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def get(self):
        data, t = self.db.docs.get(self.path, (None, None))
        self.db.reads += 1
        return _Snap(data, t)

    def create(self, data):
        if self.path in self.db.docs:
            raise AlreadyExists(self.path)
        self.db.docs[self.path] = (copy.deepcopy(data), next(self.db.clock))

    def update(self, fields, option=None):
        if self.path not in self.db.docs:
            raise FailedPrecondition("no document")
        data, t = self.db.docs[self.path]
        if option is not None and option != t:
            raise FailedPrecondition(f"update_time {t} != {option}")
        data = copy.deepcopy(data)
        data.update(copy.deepcopy(fields))
        self.db.docs[self.path] = (data, next(self.db.clock))


class _Coll:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def document(self, doc_id):
        return _Doc(self.db, f"{self.name}/{doc_id}")


class FakeFirestore:
    def __init__(self):
        self.docs: dict[str, tuple[dict, int]] = {}
        self.clock = itertools.count(1)
        self.reads = 0

    def collection(self, name):
        return _Coll(self, name)

    def write_option(self, last_update_time):
        return last_update_time

    def all_values(self):
        out = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    out.append(str(k))
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            else:
                out.append(str(x))
        for path, (data, _) in self.docs.items():
            out.append(path)
            walk(data)
        return out


class FakeObjectStore:
    """Generation-numbered, create-only object store on a local directory (a Cloud Storage stand-in)."""

    def __init__(self, root: Path, bucket="test-bucket"):
        self.root, self.bucket_name = Path(root), bucket
        self.gen = itertools.count(1_700_000_000_000_001)
        self.objects: dict[str, list[tuple[int, Path]]] = {}

    def put_new(self, name, path, content_type):
        if self.objects.get(name):
            raise FailedPrecondition(f"{name} exists (if_generation_match=0)")
        g = next(self.gen)
        dest = self.root / f"{g}"
        shutil.copyfile(path, dest)
        self.objects.setdefault(name, []).append((g, dest))
        return g

    def overwrite_for_test(self, name, data: bytes):
        """Simulate a later object generation (e.g. someone re-uploading) for tamper tests."""
        g = next(self.gen)
        dest = self.root / f"{g}"
        dest.write_bytes(data)
        self.objects.setdefault(name, []).append((g, dest))
        return g

    def corrupt_generation_for_test(self, name, generation, data: bytes):
        for g, p in self.objects[name]:
            if g == generation:
                p.chmod(0o644)
                p.write_bytes(data)

    def get(self, name, generation, dest):
        for g, p in self.objects.get(name, []):
            if g == int(generation):
                shutil.copyfile(p, dest)
                return
        raise FileNotFoundError(f"{name}#{generation}")

    def uri(self, name):
        return f"gs://{self.bucket_name}/{name}"
