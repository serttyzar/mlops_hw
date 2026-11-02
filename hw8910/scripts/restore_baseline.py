"""Materialize public baseline from pinned snapshot and the preserved release evidence."""
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from publication import read,write
from release import digest,verify_artifact
from gate_bridge import verify_release_report

m=read(ROOT/'metrics/manifest-baseline.json')
r=read(ROOT/'metrics/release-report-baseline.json')
snapshot=ROOT/'.models/hub/models--Qwen--Qwen3-0.6B/snapshots'/m['runtime']['base_revision']
artifact=ROOT/'.models/baseline'
artifact.mkdir(exist_ok=True)
for source in snapshot.iterdir():
    target=artifact/source.name
    if source.is_file() and not target.exists():
        target.symlink_to(source.resolve())
(artifact/'chat_template.jinja').write_text(read(snapshot/'tokenizer_config.json')['chat_template'])
workspace=ROOT/'.real'
workspace.mkdir(exist_ok=True)
verify_artifact(m,workspace)
verify_release_report(m,r,workspace)
write(workspace/'manifest-baseline.json',m)
write(workspace/'report-baseline.json',r)
shutil.copyfile(ROOT.parent/'hw67/data/eval/golden.jsonl',workspace/'input.txt')
write(workspace/'config.json',{'format':'jsonl','tokenizer_revision':m['runtime']['tokenizer_sha256'],
      'template_sha256':m['runtime']['template_sha256'],'max_length':m['runtime']['max_input_tokens'],
      'code_revision':digest((ROOT/'lab.py').read_bytes())})
print('Baseline materialized and verified. Publish with make baseline; then make serve.')
