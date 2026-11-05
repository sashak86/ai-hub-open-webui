#!/usr/bin/env python3
"""Pinned public weights only: no service, Python package, or model execution.

Usage: python scripts/install-flux-klein.py --models-dir ~/ai-local/comfyui/models
Weights remain outside git. Existing files are accepted only after SHA256 verification.
"""
import argparse,concurrent.futures,hashlib,json,os,shutil,urllib.request
from pathlib import Path
MANIFEST=next((p for p in [Path(__file__).resolve().parents[1]/'docs/models/flux-klein-4b.json',Path(__file__).resolve().parent/'models/flux-klein-4b.json'] if p.is_file()),None)
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def download(root,row):
    dest=root/row['folder']/Path(row['file']).name;dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if dest.stat().st_size==row['size'] and digest(dest)==row['sha256']:return dest.name+' verified (already installed)'
        raise ValueError('Existing file has a different checksum: '+str(dest))
    partial=dest.with_suffix(dest.suffix+'.part');offset=partial.stat().st_size if partial.exists() else 0
    url='https://huggingface.co/'+row['repo']+'/resolve/'+row['revision']+'/'+row['file']
    req=urllib.request.Request(url,headers={'Range':'bytes='+str(offset)+'-'} if offset else {})
    with urllib.request.urlopen(req,timeout=90) as response:
        append=offset and response.status==206
        with partial.open('ab' if append else 'wb') as f:
            for chunk in iter(lambda:response.read(1024*1024),b''):f.write(chunk)
    if partial.stat().st_size!=row['size'] or digest(partial)!=row['sha256']:raise ValueError('Downloaded checksum mismatch: '+dest.name)
    os.replace(partial,dest);return dest.name+' installed and SHA256 verified'
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--models-dir',type=Path,required=True);args=p.parse_args()
    root=args.models_dir.expanduser();root.mkdir(parents=True,exist_ok=True);rows=json.loads(MANIFEST.read_text())
    required=sum(r['size'] for r in rows if not (root/r['folder']/Path(r['file']).name).exists())+512*1024*1024
    if shutil.disk_usage(root).free<required:raise SystemExit('Insufficient free disk space for pinned weights')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda r:download(root,r),rows):print(result,flush=True)
if __name__=='__main__':main()
