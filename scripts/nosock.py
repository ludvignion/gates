"""pytest plugin: block outbound sockets and log every attempt to $NOSOCK_LOG. Used by
verdict_checks.py to prove "no test makes a live call". Loaded via PYTEST_ADDOPTS="-p nosock"."""
import os
import socket
import traceback

LOG = os.environ.get("NOSOCK_LOG")


def _log(addr):
    if LOG:
        with open(LOG, "a") as f:
            f.write(f"{addr}\n" + "".join(traceback.format_stack(limit=14)[:-1]) + "----\n")


def _connect(self, addr, *a, **k):
    _log(addr)
    raise OSError("network blocked by nosock")


def _create_connection(addr, *a, **k):
    _log(addr)
    raise OSError("network blocked by nosock")


socket.socket.connect = _connect
socket.create_connection = _create_connection
