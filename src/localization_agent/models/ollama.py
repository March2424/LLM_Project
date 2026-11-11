"""Local Ollama backend using the shared ChatModel protocol, with bounded calls."""
import json
from urllib.request import Request, urlopen

from .protocols import ModelReply
from .chat_completions import estimate_tokens


class OllamaModel:
    def __init__(self, model='llama3.2:1b', endpoint='http://localhost:11434/api/chat',
                 max_output_tokens=384, context_tokens=4096, timeout=120, max_calls=30):
        self.model, self.endpoint = model, endpoint
        self.max_output_tokens, self.context_tokens = max_output_tokens, context_tokens
        self.timeout, self.max_calls, self.calls = timeout, max_calls, 0
        if max_output_tokens < 1 or context_tokens <= max_output_tokens or timeout <= 0 or max_calls < 1:
            raise ValueError('Invalid Ollama context, output, timeout or call limits')

    def __call__(self, task, prompt):
        if self.calls >= self.max_calls:
            raise RuntimeError('Local model call limit reached')
        if estimate_tokens(prompt) + self.max_output_tokens > self.context_tokens:
            raise ValueError('Estimated prompt plus output exceeds configured local context')
        self.calls += 1
        payload = {'model': self.model, 'stream': False,
            'messages': [{'role': 'user', 'content': prompt}],
            'options': {'temperature': 0, 'num_predict': self.max_output_tokens, 'num_ctx': self.context_tokens}}
        request = Request(self.endpoint, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        if data.get('done_reason') == 'length':
            raise RuntimeError('Ollama output truncated; increase max_output_tokens')
        return ModelReply(data['message']['content'], data.get('model', self.model),
                          data.get('prompt_eval_count'), data.get('eval_count'), 0.0)
