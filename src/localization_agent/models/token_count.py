"""Exact token counts using a caller-selected local tokenizer; no network/model calls."""
from hashlib import sha256
from pathlib import Path


class LocalTokenCounter:
    def __init__(self, path: str | Path):
        from tokenizers import Tokenizer
        path=Path(path)
        self.tokenizer=Tokenizer.from_file(str(path))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.name='tokenizer.json:sha256:'+sha256(path.read_bytes()).hexdigest()

    def __call__(self,text: str) -> int:
        return len(self.tokenizer.encode(text,add_special_tokens=False).ids)
