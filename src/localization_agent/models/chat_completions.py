"""Small HTTP adapter for chat-completions compatible services; no SDK required."""
import json
import math
import os
import threading
import time
import ssl
from dataclasses import dataclass
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocols import ModelReply


def estimate_tokens(text: str) -> int:
    """Planning heuristic, not a provider tokenizer or exact billing count."""
    return math.ceil(len(text.encode('utf-8')) / 3) + 16


class BudgetExceeded(RuntimeError):
    pass


class RetryableModelError(RuntimeError):
    """Temporary service/transport failure; another attempt may incur another charge."""


class ModelResponseError(RuntimeError):
    """A received response is unusable; retrying the same limits is not a repair."""


def read_response_json(response, deadline: float, read_timeout: float):
    """Bound the whole body read, including providers sending keepalive whitespace."""
    chunks = []
    socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Model response exceeded total request deadline')
        if socket is not None:
            socket.settimeout(min(read_timeout, remaining))
        # read() can wait forever while small keepalive chunks reset the socket timeout.
        chunk = response.read1(65536)
        if not chunk:
            return json.loads(b''.join(chunks))
        chunks.append(chunk)


@dataclass
class CallBudget:
    max_calls: int = 20
    max_tokens: int = 200000
    max_cost: float | None = None
    calls: int = 0
    tokens: int = 0
    cost: float = 0.0
    estimated_usage_calls: int = 0
    unpriced_calls: int = 0

    def __post_init__(self):
        self._lock = threading.RLock()
        if min(self.max_calls, self.max_tokens) < 0 or (self.max_cost is not None and self.max_cost < 0):
            raise ValueError('Budget limits cannot be negative')

    def reserve(self, tokens: int, cost: float | None):
        with self._lock:
            self._reserve(tokens, cost)

    def _reserve(self, tokens: int, cost: float | None):
        if self.max_cost is not None and cost is None:
            raise ValueError('A money limit needs input/output prices for every model')
        if (self.calls >= self.max_calls or self.tokens + tokens > self.max_tokens
                or (self.max_cost is not None and self.cost + cost > self.max_cost)):
            raise BudgetExceeded('Call, token or configured money budget exhausted before request')
        self.calls += 1
        self.tokens += tokens
        self.cost += cost or 0
        self.estimated_usage_calls += 1
        self.unpriced_calls += cost is None
        if callback := getattr(self, 'on_change', None):
            callback()

    def settle(self, reserved_tokens: int, reserved_cost: float | None,
               actual_tokens: int, actual_cost: float | None):
        with self._lock:
            self.tokens += actual_tokens - reserved_tokens
            self.cost += (actual_cost or 0) - (reserved_cost or 0)
            self.estimated_usage_calls -= 1
            if callback := getattr(self, 'on_change', None):
                callback()

    def snapshot(self):
        from dataclasses import asdict
        with self._lock:
            return asdict(self)


class ChatCompletionsModel:
    def __init__(self, *, endpoint: str, model: str, api_key_env: str = 'LLM_API_KEY',
                 max_output_tokens: int = 8192, context_tokens: int = 32768,
                 temperature: float = 0.1, timeout: float = 60, total_timeout: float = 180,
                 input_price_per_million: float | None = None,
                 output_price_per_million: float | None = None,
                 extra_body: dict | None = None, budget: CallBudget | None = None):
        self.endpoint, self.model, self.api_key_env = endpoint, model, api_key_env
        self.max_output_tokens, self.context_tokens = max_output_tokens, context_tokens
        self.temperature, self.timeout = temperature, timeout
        self.total_timeout = total_timeout
        self.input_price, self.output_price = input_price_per_million, output_price_per_million
        self.extra_body, self.budget = extra_body or {}, budget or CallBudget()
        if max_output_tokens < 1 or context_tokens <= max_output_tokens or min(timeout, total_timeout) <= 0:
            raise ValueError('Invalid model context/output limits or timeout')
        if any(x is not None and x < 0 for x in (self.input_price, self.output_price)):
            raise ValueError('Prices cannot be negative')
        if set(self.extra_body) & {'model', 'messages', 'max_tokens', 'stream'}:
            raise ValueError('extra_body cannot override model, messages, limits or streaming')

    def _cost(self, inputs, outputs):
        if self.input_price is None or self.output_price is None:
            return None
        return (inputs * self.input_price + outputs * self.output_price) / 1000000

    def __call__(self, task: str, prompt: str) -> ModelReply:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(f'Missing credential environment variable: {self.api_key_env}')
        inputs = estimate_tokens(prompt)
        if inputs + self.max_output_tokens > self.context_tokens:
            raise ValueError('Estimated prompt plus output exceeds configured model context')
        payload = {'model': self.model, 'messages': [{'role': 'user', 'content': prompt}],
                   'temperature': self.temperature, 'max_tokens': self.max_output_tokens,
                   'stream': False, **self.extra_body}
        if task == 'discover_graph':
            payload.setdefault('response_format', {'type': 'json_object'})
        reserved_cost = self._cost(inputs, self.max_output_tokens)
        self.budget.reserve(inputs + self.max_output_tokens, reserved_cost)
        request = Request(self.endpoint, data=json.dumps(payload).encode(),
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        try:
            deadline = time.monotonic() + self.total_timeout
            with urlopen(request, timeout=min(self.timeout, self.total_timeout)) as response:
                result = read_response_json(response, deadline, self.timeout)
        except HTTPError as error:
            # Do not expose provider response bodies, which may echo sensitive input.
            if error.code in {408, 429, 500, 502, 503, 504}:
                raise RetryableModelError(f'Model HTTP {error.code}') from None
            raise RuntimeError(f'Model HTTP {error.code}; request not automatically retried') from None
        except (URLError, TimeoutError, ConnectionError, IncompleteRead, ssl.SSLError) as error:
            reason = error.reason if isinstance(error, URLError) else error
            detail = type(reason).__name__
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise RuntimeError('Model TLS certificate verification failed') from None
            raise RetryableModelError(f'Model transport failure: {detail}') from None
        except json.JSONDecodeError:
            raise RetryableModelError('Model returned invalid JSON') from None
        if not isinstance(result, dict):
            raise ModelResponseError('Model response is not an object')
        usage = result.get('usage') or {}
        actual_input, actual_output = usage.get('prompt_tokens'), usage.get('completion_tokens')
        if actual_input is not None and actual_output is not None:
            actual_cost = self._cost(actual_input, actual_output)
            self.budget.settle(inputs + self.max_output_tokens, reserved_cost,
                               actual_input + actual_output, actual_cost)
        else:
            actual_cost = None
        choices = result.get('choices')
        if not choices or not isinstance(choices, list) or not isinstance(choices[0], dict):
            raise ModelResponseError('Model response has no completion choices')
        choice = choices[0]
        if choice.get('finish_reason') == 'length':
            raise ModelResponseError('Model output truncated; reduce batch size or increase output limit')
        content = (choice.get('message') or {}).get('content')
        if not isinstance(content, str) or not content.strip():
            raise ModelResponseError('Model response has no text content')
        return ModelReply(content, result.get('model', self.model), actual_input, actual_output, actual_cost)
