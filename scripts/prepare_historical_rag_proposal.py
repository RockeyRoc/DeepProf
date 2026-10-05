"""Prepare a reviewable associated 160-cell proposal, without activating a run."""
from __future__ import annotations
import csv,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evaluation.m3_acceptance import PROMPT_FILES,_prompt_fingerprint
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,ACTIVE_BATCH,budget_state,runtime_fingerprint
from scripts.run_m3_rag_repair import _validate_frozen_source_index,BASE_CAMPAIGN,REPAIR_PREFLIGHT_CASES
from scripts.run_m3_abc_research import _selected_course_parameters,RAG_CONDITIONS
from scripts.run_m3_live_pilot import live_cases
from scripts.ocr_full_textbook import write_json,digest
ID='historical-rag-160-recovery-20261004'
def main():
 docs=RESEARCH/ID;private=RAW_RESEARCH/ID/'proposed-reproduction'
 audit=json.loads((docs/'audit-summary.json').read_text(encoding='utf-8'))
 assert not audit['matched_source_snapshots'] and audit['provider_requests_sent']==0
 index=_validate_frozen_source_index('4712bd0cec0e3a687b815d4b8c935236cc49a6c153ee8fc2068835474fab9bff')
 source,cases=live_cases(40);case_path=ROOT/'evaluation/dev_cases.json'
 assert digest(case_path)=='b833e457533e1e517a89d017ac42f7a27351bc44f0e6162aae82b32da130abc6'
 parameters=_selected_course_parameters()
 assert parameters['config_hash']=='51340c493ab91337ea65b92535dc82237a6e1209ad23a985506e5090f4e8126f'
 private.mkdir(parents=True,exist_ok=True)
 (private/'source-cases.json').write_bytes(case_path.read_bytes());write_json(private/'bkt-parameters.json',parameters)
 fingerprints={}
 for name in PROMPT_FILES:
  dest=private/'prompt-source'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes());fingerprints[name]=digest(dest)
 assert _prompt_fingerprint(private/'prompt-source')==_prompt_fingerprint()
 rows=[{'cell_id':f'{condition}/{case["case_id"]}/C','case_id':case['case_id'],'group':'C',
        'condition':condition,'retrieval_enabled':opts['retrieval_enabled'],'evidence_constraint':opts['evidence_constraint'],
        'max_generation_requests':0 if not opts['retrieval_enabled'] and opts['evidence_constraint'] else 1,
        'status':'planned_not_executed'} for condition,opts in RAG_CONDITIONS for case in cases]
 assert len(rows)==160 and len({r['cell_id'] for r in rows})==160
 assert sum(r['max_generation_requests'] for r in rows)==120
 with (docs/'proposed-matrix.csv').open('w',encoding='utf-8-sig',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 ledger=budget_state(RAW_RESEARCH/ACTIVE_BATCH)
 proposal={'schema_version':'historical-rag-associated-160-proposal-v1','status':'draft_requires_explicit_source_version_and_budget_authorization',
  'base_campaign_id':BASE_CAMPAIGN,'historical_stop_preserved':True,'comparison_scope':'new associated batch; not exact original-prompt reproduction; never pool with stopped historical batches',
  'original_prompt_sha256':audit['target_prompt_sha256'],'proposed_prompt_sha256':_prompt_fingerprint(),
  'prompt_source_sha256':fingerprints,'runtime_source_sha256':runtime_fingerprint(),
  'source_cases_sha256':digest(case_path),'selected_cases':40,'groups':['C'],'planned_cells':160,
  'bkt_config_hash':parameters['config_hash'],'index_sha256':index['sha256'],'indexed_chunk_count':index['chunk_count'],
  'preflight_cases':list(REPAIR_PREFLIGHT_CASES),'model':'deepseek-flash','temperature':0.3,
  'thinking':{'type':'disabled'},'max_tokens':8192,'automatic_retries':0,
  'formal_request_ceiling':120,'preflight_request_ceiling':4,'new_batch_request_ceiling':124,
  'cumulative_requests_before':ledger['cumulative_requests'],'current_cumulative_ceiling':ledger['cumulative_ceiling'],
  'proposed_cumulative_ceiling':981,'additional_capacity_above_current_ceiling':94,
  'paid_requests_sent':0,'services_started':0,'budget_amendment_applied':False,
  'generation_input_excludes':['reference_answers','answerability_labels','annotation_reasons'],
  'required_before_real_preflight':['explicit authorization recorded','independent runner finalized and frozen','free request-body isolation test passed','model availability verified without model generation','index, cases, BKT and source fingerprints rechecked','global and per-phase ledger guards active'],
  'stop_conditions':['configuration drift','budget exhausted','unresolved request state','failed preflight','provider failure'],
  'cleanup':'no services started; historical and completed full-text batches unchanged'}
 encoded=json.dumps(proposal,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
 proposal['proposal_sha256']=hashlib.sha256(encoded).hexdigest();write_json(docs/'associated-batch-proposal.json',proposal)
 md=f'''# 历史RAG四条件关联补跑方案（待授权）

用户本轮选择继续历史160格补跑。恢复审计检查{audit['git_revisions_checked']}个可见修订、{audit['unreachable_commits_checked']}个不可达提交、{audit['mixed_line_ending_checks']}种逐文件换行组合，未找到原提示源码。旧停止批次不修改、不重标为有效。

原提示指纹：`{audit['target_prompt_sha256']}`；拟采用当前提示指纹：`{proposal['proposed_prompt_sha256']}`。提示源码已独立保存在私有复现目录，正文与附录不包含原始prompt。

关联批次保留原40题、C组会话/BKT初始化逻辑、旧685片段索引以及原BKT参数。题集、索引和BKT哈希均通过不收费核验。拟按四条件各40格执行，共160格；检索关／约束开40格零生成。此方案属于新提示版本的关联补跑，不能声称恢复了原提示版本，也不混入已完成全文480格结果。

固定deepseek-flash、temperature=0.3、thinking.type=disabled、8192 tokens、零自动重试。4例真实预检最多4次，正式最多120次，合计最多124次；失败及未决计数，预检失败或配置漂移停止。

当前累计857／887次，仅余30次。拟将累计上限调整为981次（857+124），较原上限增加94次。本方案尚未获得新增额度及模板变更授权，未发送请求、未启动服务。

本地准备验收：160格唯一，四条件各40格，40格零生成，正式上限120次；旧索引、题集、BKT校验通过，源码快照哈希一致。正式请求体隔离测试、独立运行器冻结和实时模型可用性检查仍需在明确授权后完成，并在任何真实生成前通过。

方案指纹：`{proposal['proposal_sha256']}`。机器可读配置见associated-batch-proposal.json，矩阵见proposed-matrix.csv，恢复证明见audit-summary.json。
'''
 (docs/'关联补跑方案.md').write_text(md,encoding='utf-8')
 write_json(docs/'proposal-validation.json',{'status':'passed_local_preparation','matrix_cells':160,'unique_cells':160,'zero_generation_cells':40,'formal_max_requests':120,'preflight_max_requests':4,'source_cases_match_original':True,'index_match_original':True,'bkt_match_original':True,'snapshot_matches_proposed_prompt':True,'paid_requests_sent':0,'services_started':0,'requires_authorization':True})
 print(json.dumps({k:proposal[k] for k in ['status','planned_cells','new_batch_request_ceiling','proposed_cumulative_ceiling','proposal_sha256','paid_requests_sent']},ensure_ascii=False))
if __name__=='__main__':main()
