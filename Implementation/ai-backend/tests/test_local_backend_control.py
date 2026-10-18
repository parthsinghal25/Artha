"""Owned-process safety for the local service helper; no live backend is signalled."""
import importlib.util
import os
from pathlib import Path


def helper():
    spec=importlib.util.spec_from_file_location('local_backend',Path(__file__).resolve().parents[1]/'scripts/local_backend.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_reused_or_foreign_pid_is_never_owned():
    m=helper()
    # This pytest process exists, but it is not the recorded backend command/start identity.
    assert not m.owned({'pid':os.getpid(),'identity':{'command':'python -m sdbackend serve --port 8080','started':'wrong'}})


def test_stop_removes_stale_record_without_signalling_foreign_pid(tmp_path):
    m=helper();m.STATE=tmp_path/'backend.json';m.PIDF=tmp_path/'backend.pid';m.PORTF=tmp_path/'backend.port'
    m.STATE.write_text('{}');m.PIDF.write_text(str(os.getpid()));m.PORTF.write_text('8080')
    m.stop({'pid':os.getpid(),'identity':{'command':'foreign','started':'wrong'}})
    os.kill(os.getpid(),0)
    assert not m.STATE.exists() and not m.PIDF.exists()
