#!/usr/bin/env python3
"""Manage only this project's recorded local backend process; no inference or publication."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'var/run'
STATE=RUN/'backend.json'
PIDF=RUN/'backend.pid'
PORTF=RUN/'backend.port'


def identity(pid):
    try:
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
        started=subprocess.check_output(['ps','-p',str(pid),'-o','lstart='],text=True).strip()
        return {'command':command,'started':started}
    except (OSError,subprocess.CalledProcessError):
        return None


def owned(state):
    if not state or not isinstance(state.get('pid'),int): return False
    actual=identity(state['pid'])
    return bool(actual and actual==state.get('identity') and
                str(ROOT/'.venv/bin/python') in actual['command'] and
                ' -m sdbackend serve --port ' in actual['command'])


def ready(state):
    if not owned(state): return False
    try:
        with urlopen(state['url']+'/readyz',timeout=2) as response:
            body=json.load(response)
        return body.get('status')=='ready' and not body.get('unreviewed_sources_enabled')
    except (OSError,ValueError): return False


def stop(state):
    if owned(state):
        os.kill(state['pid'],signal.SIGTERM)
        for _ in range(50):
            if not owned(state): break
            time.sleep(.1)
        else: raise SystemExit('Recorded backend did not stop; inspect it before retrying.')
        print(f"Stopped backend pid={state['pid']}")
    else: print('Backend not running; no foreign or reused PID was signalled')
    for path in (STATE,PIDF,PORTF): path.unlink(missing_ok=True)


def start(state):
    if owned(state):
        if not ready(state): raise SystemExit('Recorded backend exists but is unhealthy; inspect its log.')
        print(json.dumps(state,indent=2));return
    port=int(os.environ.get('SDB_PORT','8080'))
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try: probe.bind(('127.0.0.1',port))
        except OSError: raise SystemExit(f'Port {port} occupied; no unrecorded process will be stopped.')
    env=dict(os.environ,SDB_UI_ORIGIN=os.environ.get('SDB_UI_ORIGIN','http://127.0.0.1:15174'))
    for key in ('SDB_ALLOW_UNREVIEWED_SOURCES','SDB_ALLOW_SPECIMEN_ACCEPTANCE'):env.pop(key,None)
    RUN.mkdir(parents=True,exist_ok=True)
    with (RUN/'backend.log').open('ab') as log:
        process=subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','sdbackend','serve','--port',str(port)],
                                 cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    state={'pid':process.pid,'url':f'http://127.0.0.1:{port}','ui_origin':env['SDB_UI_ORIGIN'],
           'identity':identity(process.pid)}
    STATE.write_text(json.dumps(state,indent=2)+'\n');PIDF.write_text(str(process.pid)+'\n');PORTF.write_text(str(port)+'\n')
    for _ in range(60):
        if process.poll() is not None:
            raise SystemExit(f'Backend exited; see {RUN / "backend.log"}')
        if ready(state):print(json.dumps(state,indent=2));return
        time.sleep(.25)
    stop(state);raise SystemExit('Backend readiness timed out')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('start','stop','restart','status'))
    args=parser.parse_args()
    try:state=json.loads(STATE.read_text()) if STATE.exists() else None
    except (OSError,ValueError):state=None
    if args.action in ('stop','restart'):stop(state);state=None
    if args.action in ('start','restart'):start(state)
    elif args.action=='status':
        if not ready(state):raise SystemExit('Backend not running or unhealthy; stale PID is not ownership')
        print(json.dumps(state,indent=2))
