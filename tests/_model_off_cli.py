"""Run the real CLI with fail-fast model/network guards for the sample checkpoint."""

import atexit
import importlib.abc
import json
import os
import runpy
import socket
import sys
from pathlib import Path

attempts = {"model": [], "network": []}


class ForbiddenCall(BaseException):
    """Must not be swallowed by a provider's ordinary error handling."""


class BlockModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ("ftf.models", "openai") or fullname.startswith(
            ("ftf.models.", "google.genai")
        ):
            attempts["model"].append(fullname)
            raise ForbiddenCall(f"model import forbidden: {fullname}")


def block_network(*args, **kwargs):
    attempts["network"].append("network attempt")
    raise ForbiddenCall("network access forbidden")


@atexit.register
def write_audit():
    Path(os.environ["FTF_MODEL_OFF_AUDIT"]).write_text(json.dumps(attempts))


sys.meta_path.insert(0, BlockModels())
socket.socket.connect = block_network
socket.socket.connect_ex = block_network
socket.create_connection = block_network
socket.getaddrinfo = block_network
sys.argv[0] = "ftf"
runpy.run_module("ftf.cli", run_name="__main__")
