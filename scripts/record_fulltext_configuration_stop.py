"""Reconcile the stopped batch from local events, wire snapshots and cleanup checks."""
from __future__ import annotations
import hashlib
import json
import socket
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,budget_state
from scripts.ocr_full_textbook import write_json,digest

def main():
    batch_id='m3-fulltext-rag-20261004-v2'
    raw=RAW_RESEARCH/batch_id;docs=RESEARCH/batch_id
    connection=sqlite3.connect(f'file:{raw / "runtime/sessions.sqlite"}?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    reconciled=[]
    for pending in raw.glob('runs/*/pending/*.json'):
        if (pending.parent.parent/'cells'/pending.name).exists():continue
        value=json.loads(pending.read_text(encoding='utf-8'))
        events=[dict(x) for x in connection.execute('select event_id,type,payload from events where trace_id=? order by sequence',
                                                    (value.get('trace_id'),))]
        terminal=[e for e in events if e['type']=='agent.turn.completed']
        requests=[e for e in events if e['type']=='model.requested']
        if not terminal or requests:raise RuntimeError('unresolved_request_requires_manual_reconciliation')
        backup=raw/'reproduction/stop-reconciliation'/pending.name
        write_json(backup,value)
        proof={'pending_sha256_before':digest(backup),'trace_id':value.get('trace_id'),
               'event_ids':[e['event_id'] for e in events],'terminal':terminal[-1],
               'model_requested_events':0,'reason':'terminal local fallback; no provider request was started'}
        value.update(state='reconciled_terminal_no_generation',request_budget_reservation=0,
                     reconciliation=proof)
        write_json(pending,value);reconciled.append(proof)
    connection.close()
    wire=[]
    for path in sorted((raw/'reproduction/requests').glob('*.json')):
        value=json.loads(path.read_text(encoding='utf-8'));body=value['body']
        canonical=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        messages=json.dumps(body.get('messages',[]),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if hashlib.sha256(canonical.encode()).hexdigest()!=value['body_sha256']:raise ValueError('wire_body_hash')
        if hashlib.sha256(messages.encode()).hexdigest()!=value['messages_sha256']:raise ValueError('wire_messages_hash')
        wire.append({'file':path.name,'sha256':digest(path),'trace_id':value['trace_id'],
                     'thinking_disabled':body.get('thinking')=={'type':'disabled'},
                     'model':body.get('model'),'max_tokens':body.get('max_tokens')})
    with socket.socket() as sock:
        sock.settimeout(1)
        port_closed=sock.connect_ex(('127.0.0.1',55159))!=0
    if not port_closed:raise RuntimeError('gateway_cleanup_not_verified')
    budget=budget_state(raw)
    if budget['unresolved_requests']:raise RuntimeError('unresolved_ledger')
    proof={'status':'stopped_configuration_drift','configuration_eligible':False,
           'reason':'Frozen thinking_enabled=False, but actual wire bodies omit thinking.type=disabled. Disabled state cannot be confirmed.',
           'wire_request_count':len(wire),'compliant_wire_requests':sum(x['thinking_disabled'] for x in wire),
           'wire_archive_hashes':wire,'pending_reconciliation':reconciled,'budget':budget,
           'cleanup':{'gateway_port':55159,'loopback_port_closed':port_closed,
                      'verified_stopped_process_ids':[43572,62748,46788]},
           'remaining_new_requests':364-budget['new_requests'],
           'remaining_cumulative_requests':691-budget['cumulative_requests'],
           'all_formal_cells_excluded':True,'annotation_generation_requests':0,
           'next_batch_requires_new_freeze':True,'original_global_provider_unchanged':True}
    write_json(docs/'wire-configuration-validation.json',proof)
    state=json.loads((docs/'batch-state.json').read_text(encoding='utf-8'))
    state.update(status='stopped_configuration_drift',configuration_eligible=False,
                 reason=proof['reason'],preflight_configuration_passed=False,formal_eligible_cells=0,
                 cleanup='gateway_stopped_and_port_closed',budget=budget)
    for row in state['conditions'].values():row.update(configuration_eligible=False,passed=False)
    write_json(docs/'batch-state.json',state)
    write_json(docs/'provider-request-budget.json',budget)
    for run in (raw/'runs').glob('*'):
        path=run/'manifest.json'
        if not path.exists():continue
        manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest.update(configuration_eligible=False,stop_reason=proof['reason'])
        if manifest.get('status')=='running':manifest['status']='stopped_configuration_drift'
        write_json(path,manifest)
    print(json.dumps({k:proof[k] for k in ['wire_request_count','compliant_wire_requests','budget','remaining_new_requests']},indent=2))

if __name__=='__main__':main()
