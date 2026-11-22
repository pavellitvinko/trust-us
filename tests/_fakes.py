"""Fake model clients shared by the perception, gateway and pattern tests.

Nothing here touches the network: each fake replays queued responses and records requests.
"""

from contextlib import contextmanager
from types import SimpleNamespace as NS


class FakeOpenAI:
    """Replays `responses` in order; an Exception in the queue is raised when its turn comes."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.responses = NS(stream=self._stream)

    @contextmanager
    def _stream(self, **request):
        self.requests.append(request)
        response = self.queue.pop(0)
        if isinstance(response, Exception):
            raise response
        yield NS(get_final_response=lambda: response)
