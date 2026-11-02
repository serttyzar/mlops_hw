"""Injectable generation backend; loaded version is immutable for process lifetime."""
import time
from release import verify_artifact


class StubBackend:
    label = 'STUB ONLY'

    def __init__(self, manifest, artifact=None):
        if manifest['runtime']['base_revision'] != 'fixture-only':
            raise ValueError('Stub cannot serve a real model manifest')
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.calls = 0
        self.max_new_tokens = manifest['runtime']['generation']['max_new_tokens']

    def generate(self, prompt, limit):
        self.calls += 1
        time.sleep(.01)
        return {'answer': f'STUB ONLY: {self.loaded_id}', 'output_tokens': 1}


class TransformersBackend:
    label = 'Transformers (real weights)'

    def __init__(self, manifest, artifact):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.loaded_id = manifest['model_id']
        self.runtime_sha256 = manifest['runtime_sha256']
        self.runtime = manifest['runtime']
        self.calls = 0
        self.max_new_tokens = self.runtime['generation']['max_new_tokens']
        self.device = self.runtime['device']
        dtype = getattr(torch, self.runtime['dtype'])
        self.tokenizer = AutoTokenizer.from_pretrained(str(artifact), local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(str(artifact), local_files_only=True, dtype=dtype)
        if manifest.get('adapter_subdir'):
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, artifact / manifest['adapter_subdir'], local_files_only=True)
        self.model = self.model.to(self.device).eval()
        # Generation config from the snapshot is pinned, overrides from runtime are explicit.
        if self.runtime['generation']['do_sample'] is not False:
            raise ValueError('This backend supports only the evaluated greedy protocol')

    def generate(self, prompt, limit):
        settings = self.runtime['generation']
        text = self.tokenizer.apply_chat_template(
            [{'role': 'system', 'content': settings['system']}, {'role': 'user', 'content': prompt}],
            tokenize=False, add_generation_prompt=True, enable_thinking=settings['enable_thinking'])
        batch = self.tokenizer(text, return_tensors='pt')
        if batch['input_ids'].shape[1] > self.runtime['max_input_tokens']:
            raise ValueError('Input exceeds evaluated token limit')
        self.calls += 1
        batch = batch.to(self.device)
        self.torch.manual_seed(settings['seed'])
        with self.torch.inference_mode():
            out = self.model.generate(**batch, do_sample=False, max_new_tokens=limit,
                                      pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id)
        ids = out[0, batch['input_ids'].shape[1]:]
        return {'answer': self.tokenizer.decode(ids, skip_special_tokens=True), 'output_tokens': len(ids)}
