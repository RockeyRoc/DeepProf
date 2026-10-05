"""Atomic, fingerprinted local-inference checkpoints; no provider calls."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import numpy as np

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def signature(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)

def freeze(path,spec,resume=False):
    path=Path(path); spec={**spec,'config_sha256':signature(spec)}
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if old!=spec:raise ValueError('frozen_configuration_mismatch')
        if not resume:raise ValueError('existing_run_requires_resume')
    else:write_json(path,spec)
    return spec

def validate_array(values,n,dimension):
    values=np.asarray(values,dtype=np.float32)
    expected=(n,dimension) if dimension else (n,)
    if values.shape!=expected:raise ValueError(f'score_count_or_shape_mismatch:{values.shape}:{expected}')
    if not np.isfinite(values).all():raise ValueError('non_finite_inference_output')
    if dimension and np.any(np.linalg.norm(values,axis=1)<=0):raise ValueError('zero_embedding')
    return values

def read_chunk(folder,start,keys,stage_sha256,dimension):
    stem=Path(folder)/f'{start:07d}'
    meta_path=stem.with_suffix('.json');array_path=stem.with_suffix('.npy')
    if not meta_path.exists():return None
    try:meta=json.loads(meta_path.read_text(encoding='utf-8'))
    except (ValueError,OSError):return None
    if meta.get('stage_sha256')!=stage_sha256:raise ValueError('checkpoint_configuration_mismatch')
    if meta.get('keys')!=keys:raise ValueError('checkpoint_input_order_mismatch')
    try:
        if sha(array_path)!=meta['array_sha256']:return None
        return validate_array(np.load(array_path,allow_pickle=False),len(keys),dimension)
    except (OSError,ValueError,KeyError):return None

def write_chunk(folder,start,keys,values,stage_sha256,dimension,elapsed):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    values=validate_array(values,len(keys),dimension)
    stem=folder/f'{start:07d}';array_path=stem.with_suffix('.npy');temporary=stem.with_suffix('.npy.tmp')
    with temporary.open('wb') as stream:
        np.save(stream,values,allow_pickle=False);stream.flush();os.fsync(stream.fileno())
    temporary.replace(array_path)
    write_json(stem.with_suffix('.json'),{'stage_sha256':stage_sha256,'keys':keys,'array_sha256':sha(array_path),
                                        'rows':len(keys),'elapsed_seconds':elapsed})
