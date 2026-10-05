import json
import math
import numpy as np
import pytest
from pathlib import Path
from uuid import uuid4
from scripts import nomiracl_checkpoints as cp
from scripts import run_nomiracl_zh_reranker as evaluation

@pytest.fixture
def tmp_path():
    # Avoid the host's restricted pytest temporary-directory ACL.
    path = Path(__file__).resolve().parents[1] / 'docs/experiments/bkt-rag-improvement-20261001/validation/test-workspaces' / uuid4().hex
    path.mkdir(parents=True)
    return path

def test_threshold_field_and_test_isolation():
    dev=[{'split':'dev','gold_relevant':0,'max_reranker_score':x} for x in [0,1]]
    dev += [{'split':'dev','gold_relevant':1,'max_reranker_score':x} for x in [1,2]]
    chosen=evaluation.pick_threshold(dev)
    assert chosen['threshold']==2 and chosen['development_false_accept_rate']==0
    assert chosen['development_relevant_accept_rate']==.5
    with pytest.raises(ValueError,match='dev_only'):evaluation.pick_threshold([{**r,'split':'test'} for r in dev])
    with pytest.raises(ValueError):evaluation.pick_threshold([])

def test_threshold_boundary_ties_and_nonfinite():
    rows=[{'gold_relevant':0,'max_reranker_score':0} for _ in range(20)]
    rows += [{'gold_relevant':1,'max_reranker_score':1}]
    assert evaluation.pick_threshold(rows)['threshold']==1
    with pytest.raises(ValueError,match='non_finite'):evaluation.pick_threshold([{**r,'max_reranker_score':math.nan} for r in rows])

def test_ranking_auc_and_gate_known_values():
    assert evaluation.order({'b':1,'a':1})==['a','b']
    assert evaluation.ndcg_at_k(['a','b'],{'a':1,'b':0})==1
    assert evaluation.ndcg_at_k(['b','a'],{'a':1,'b':0})==pytest.approx(1/math.log2(3))
    assert evaluation.auc([0,1],[0,1])==1
    assert evaluation.auc([1,1],[0,1])==.5

def test_checkpoint_resume_corruption_and_fingerprint(tmp_path):
    keys=['a','b'];v=np.ones((2,1024),dtype=np.float32)
    cp.write_chunk(tmp_path,0,keys,v,'signature',1024,.2)
    assert np.array_equal(cp.read_chunk(tmp_path,0,keys,'signature',1024),v)
    with pytest.raises(ValueError,match='configuration'):cp.read_chunk(tmp_path,0,keys,'different',1024)
    with pytest.raises(ValueError,match='order'):cp.read_chunk(tmp_path,0,list(reversed(keys)),'signature',1024)
    (tmp_path/'0000000.npy').write_bytes(b'corrupt')
    assert cp.read_chunk(tmp_path,0,keys,'signature',1024) is None
    cp.write_chunk(tmp_path,0,keys,v,'signature',1024,.2)
    assert cp.read_chunk(tmp_path,0,keys,'signature',1024).shape==(2,1024)

def test_scores_count_empty_and_finite():
    with pytest.raises(ValueError,match='shape'):cp.validate_array([1],2,0)
    with pytest.raises(ValueError,match='non_finite'):cp.validate_array([float('inf')],1,0)
    with pytest.raises(ValueError,match='zero_embedding'):cp.validate_array(np.zeros((1,1024)),1,1024)

def test_freeze_guard_and_duplicate_topics(tmp_path):
    path=tmp_path/'freeze.json';cp.freeze(path,{'a':1})
    assert cp.freeze(path,{'a':1},True)['a']==1
    with pytest.raises(ValueError):cp.freeze(path,{'a':2},True)
    with pytest.raises(ValueError,match='resume'):cp.freeze(path,{'a':1})
    (tmp_path/'topics').write_text('1\tq\n1\tother\n')
    with pytest.raises(ValueError,match='duplicate'):evaluation.read_topics(tmp_path/'topics')

def test_duplicate_and_invalid_qrels(tmp_path):
    path=tmp_path/'qrels';path.write_text('1 Q0 a 1\n1 Q0 a 0\n')
    with pytest.raises(ValueError,match='duplicate'):evaluation.read_qrels(path)
    path.write_text('1 Q0 a 2\n')
    with pytest.raises(ValueError,match='grade'):evaluation.read_qrels(path)

def test_identical_corpus_duplicates_and_conflicts(tmp_path):
    import gzip
    root=tmp_path/'data/chinese';root.mkdir(parents=True)
    (root/'topics').mkdir();(root/'qrels').mkdir()
    docs=[{'docid':'a','title':'title','text':'text'}]*2
    with gzip.open(root/'corpus.jsonl.gz','wt',encoding='utf-8') as stream:
        stream.write('\n'.join(json.dumps(r) for r in docs))
    for i,group in enumerate(['dev.relevant','dev.non_relevant','test.relevant','test.non_relevant']):
        (root/'topics'/f'{group}.tsv').write_text(f'{i}\tquery\n')
        (root/'qrels'/f'{group}.tsv').write_text(f'{i} Q0 a {int("non_" not in group)}\n')
    assert evaluation.collect_dataset(tmp_path)['identical_duplicate_corpus_rows']==1
    docs[1]={**docs[1],'text':'conflict'}
    with gzip.open(root/'corpus.jsonl.gz','wt',encoding='utf-8') as stream:
        stream.write('\n'.join(json.dumps(r) for r in docs))
    with pytest.raises(ValueError,match='conflicting'):evaluation.collect_dataset(tmp_path)

def test_cuda_fallback_preserves_separate_stage_and_cleanup(tmp_path,monkeypatch):
    calls=[]
    class Process:
        def __init__(self,command,**kwargs):
            device=command[-1];calls.append(device);self.pid=len(calls);self.stdout=[]
            self.returncode=1 if device=='cuda' else 0
            cp.write_json(tmp_path/'checkpoints'/'documents'/device/'worker-state.json',
                          {'status':'failed' if self.returncode else 'completed','device_failure':bool(self.returncode),'device':device})
        def poll(self):return self.returncode
        def wait(self):return self.returncode
    monkeypatch.setattr(evaluation.subprocess,'Popen',Process)
    result=evaluation.run_stage(tmp_path,'documents','cuda','python',[])
    assert calls==['cuda','cpu'] and result['status']=='completed'
    assert json.loads((tmp_path/'cleanup-proof.json').read_text())['all_workers_exited']
