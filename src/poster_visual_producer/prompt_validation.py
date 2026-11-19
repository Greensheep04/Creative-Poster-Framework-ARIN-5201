"""Lazy backend-specific exact token validation, including post-adaptation requests."""
from .contracts import StageError

class SDXLPromptValidator:
    def __init__(self, model_dir):
        from transformers import CLIPTokenizer
        self.tokenizers=tuple(CLIPTokenizer.from_pretrained(str(model_dir/name),local_files_only=True) for name in ('tokenizer','tokenizer_2'))
        self.limit=min(t.model_max_length for t in self.tokenizers)
    def count(self,text):
        return [len(t(text or '',truncation=False)['input_ids']) for t in self.tokenizers]
    def __call__(self,request):
        result={key:self.count(getattr(request,key)) for key in ('prompt','negative_prompt')}
        if any(n>self.limit for counts in result.values() for n in counts):
            raise StageError('PROMPT_OVERFLOW','Final SDXL request exceeds tokenizer capacity')
        return result
