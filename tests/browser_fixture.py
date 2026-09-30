"""Loopback mock fixture for browser tests. Never included in the executable."""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_runtime import AgentRuntime
from agent_server import AgentServer

with tempfile.TemporaryDirectory(prefix="vidental-browser-test-") as temporary:
    server = AgentServer(("127.0.0.1", 27100), {"http://127.0.0.1:5179"})
    runtime = AgentRuntime(Path(temporary), mock=True)
    server.runtime = runtime
    runtime.start_monitor()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(json.dumps({"code": runtime.pairings.new_code()}), flush=True)
    try:
        sys.stdin.readline()
    finally:
        server.shutdown()
        server.server_close()
        runtime.close()
