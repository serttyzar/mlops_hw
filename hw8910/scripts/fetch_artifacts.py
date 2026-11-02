"""Fetch exact existing artifacts; never train and never update Registry aliases."""
import argparse
import json
from pathlib import Path
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base-only', action='store_true')
    p.add_argument('--tracking-uri', default='http://127.0.0.1:5006')
    args = p.parse_args()
    baseline = json.loads((ROOT.parent/'hw67/evaluation/baseline.json').read_text())
    print(snapshot_download(baseline['name'], revision=baseline['revision'], cache_dir=ROOT/'.models/hub',
                            allow_patterns=['*.json','*.safetensors','*.jinja','merges.txt','vocab.json']))
    if not args.base_only:
        import mlflow
        mlflow.set_tracking_uri(args.tracking_uri)
        spec = json.loads((ROOT.parent/'hw67/metrics/candidate-model.json').read_text())
        client = mlflow.MlflowClient()
        version = client.get_model_version(spec['name'],str(spec['version']))
        if version.run_id != spec['run_id']:
            raise ValueError('Registry/training run mismatch')
        dest=ROOT/'.models/training'
        dest.mkdir(exist_ok=True)
        print(client.download_artifacts(spec['run_id'],'model',str(dest)))


if __name__=='__main__':
    main()
