"""Record process ownership, verified cleanup and exact archived request hashes."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,ACTIVE_BATCH,budget_state,validate_wire_archive
from scripts.ocr_full_textbook import write_json,digest
from runtime.core.events import utc_now


def inspect_processes(root:int, port:int, tracked:list[int]) -> dict:
    script=r"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
$all=@(Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name)
$owned=[System.Collections.Generic.HashSet[int]]::new()
[void]$owned.Add(ROOT_PID)
$changed=$true
while($changed){
 $changed=$false
 foreach($p in $all){if($owned.Contains([int]$p.ParentProcessId) -and $owned.Add([int]$p.ProcessId)){$changed=$true}}
}
$tracked=@(TRACKED_IDS)
$remaining=@($all | Where-Object {$owned.Contains([int]$_.ProcessId) -or $tracked -contains [int]$_.ProcessId})
$listeners=@(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -eq PORT_NUMBER} | Select-Object LocalAddress,LocalPort,OwningProcess)
@{remaining_processes=$remaining;remaining_listeners=$listeners;owned_process_ids=@($owned)} | ConvertTo-Json -Depth 5 -Compress
""".replace('ROOT_PID',str(root)).replace('PORT_NUMBER',str(port)).replace('TRACKED_IDS',','.join(map(str,tracked)))
    completed=subprocess.run(['powershell','-NoProfile','-Command',script],capture_output=True,
        encoding='utf-8',errors='replace',timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if completed.returncode:raise RuntimeError('process_and_port_verification_failed: '+completed.stderr.strip()[:900])
    return json.loads(completed.stdout)


def record(bid:str, mode:str) -> dict:
    raw,docs=RAW_RESEARCH/bid,RESEARCH/bid
    state=json.loads((docs/'batch-state.json').read_text(encoding='utf-8'))
    prior=docs/'service-start-proof.json'
    tracked=json.loads(prior.read_text(encoding='utf-8')).get('owned_process_ids',[]) if prior.exists() else []
    proof=inspect_processes(int(state['gateway_pid']),int(state['gateway_port']),tracked)
    proof.update(batch_id=bid,checked_at=utc_now(),gateway_pid=state['gateway_pid'],gateway_port=state['gateway_port'])
    if mode=='snapshot':
        assert proof['remaining_processes'] and proof['remaining_listeners'],'running service ownership not verified'
        write_json(prior,proof);write_json(raw/'service-start-proof.json',proof)
        return {'batch_id':bid,'owned_process_ids':proof['owned_process_ids'],'port':state['gateway_port']}
    proof['verified']=not proof['remaining_processes'] and not proof['remaining_listeners']
    write_json(docs/'service-cleanup-proof.json',proof);write_json(raw/'service-cleanup-proof.json',proof)
    if not proof['verified']:raise RuntimeError('batch_service_cleanup_incomplete')
    state['cleanup']='gateway_tree_and_port_verified_closed'
    state['cleanup_proof_sha256']=digest(docs/'service-cleanup-proof.json')
    write_json(docs/'batch-state.json',state)
    ledger=budget_state(raw)
    generation=ledger['phase_requests']['preflight']+ledger['phase_requests']['formal']
    validate_wire_archive(raw/'reproduction/requests',generation)
    wire={'status':'verified','wire_request_count':generation,'preflight_requests':ledger['phase_requests']['preflight'],
        'formal_requests':ledger['phase_requests']['formal'],'model':'deepseek-flash',
        'thinking':{'type':'disabled'},'max_tokens':8192,'temperature':.3,'automatic_retries':0,
        'actual_request_archive_sha256':{p.name:digest(p) for p in sorted((raw/'reproduction/requests').glob('*.json'))}}
    write_json(docs/'wire-configuration-validation.json',wire)
    write_json(docs/'provider-request-budget.json',ledger)
    return {'cleanup_verified':True,'wire_requests_verified':generation,'budget':ledger}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-id',default=ACTIVE_BATCH)
    parser.add_argument('--mode',choices=['snapshot','finalize'],required=True)
    args=parser.parse_args();print(json.dumps(record(args.batch_id,args.mode),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
