"""Fetch only the pinned budget tokenizer, verify its digest, then publish atomically."""
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

REVISION = 'a09a35458c702b33eeacc393d103063234e8bc28'
DIGEST = 'c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539'
URL = f'https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/resolve/{REVISION}/tokenizer.json'


def main():
    path = Path(__file__).resolve().parents[1] / 'artifacts/workflow-tokenizer/tokenizer.json'
    if path.exists():
        if sha256(path.read_bytes()).hexdigest() != DIGEST:
            raise ValueError('Existing tokenizer has a different digest; refusing to overwrite it')
        print('预算 tokenizer 已就绪：', path)
        return
    with urlopen(URL, timeout=60) as response:
        data = response.read()
    if sha256(data).hexdigest() != DIGEST:
        raise ValueError('Downloaded tokenizer digest mismatch')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.download')
    temporary.write_bytes(data)
    temporary.replace(path)
    print('已下载并校验预算 tokenizer（无模型权重）：', path)


if __name__ == '__main__':
    main()
