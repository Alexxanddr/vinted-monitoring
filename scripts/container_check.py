"""Offline container/volume smoke check. Requires locally built vinted-monitoring:test."""

import json
import subprocess
import time
import uuid


def docker(*args, check=True):
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True
    ).stdout.strip()


def main():
    name = "vm-check-" + uuid.uuid4().hex[:10]
    volume = name + "-data"
    token = "offline-test-token-" + "x" * 32
    docker("volume", "create", volume)
    container = None
    try:
        for iteration in range(2):
            container = docker(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "-v",
                f"{volume}:/data",
                "-e",
                "VM_TRANSPORT=http",
                "-e",
                f"VM_HTTP_TOKEN={token}",
                "vinted-monitoring:test",
            )
            for _ in range(40):
                probe = subprocess.run(
                    [
                        "docker",
                        "exec",
                        name,
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')",
                    ],
                    capture_output=True,
                )
                if probe.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise AssertionError("container did not become ready: " + docker("logs", name))
            query = (
                """
import json, urllib.request
body = json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'}).encode()
request = urllib.request.Request('http://127.0.0.1:8000/mcp', data=body,
    headers={'Authorization':'Bearer '+%r, 'Content-Type':'application/json',
             'Accept':'application/json, text/event-stream'})
with urllib.request.urlopen(request) as response:
    print(len(json.load(response)['result']['tools']))
"""
                % token
            )
            assert docker("exec", name, "python", "-c", query) == "8"
            assert docker("exec", name, "id", "-u") == "10001"
            if iteration == 0:
                seed = """
from vinted_monitoring.store import Store
from vinted_monitoring.models import Query
s = Store('/data/monitor.sqlite3')
s.create('Offline persistence', Query(text='fixture'), 600)
s.suspend()
s.close()
"""
            else:
                seed = """
from vinted_monitoring.store import Store
s = Store('/data/monitor.sqlite3')
assert len(s.watches()) == 1
assert s.suspended()
assert s.db.execute('PRAGMA user_version').fetchone()[0] == 2
s.close()
"""
            # Stop before opening the database through a second maintenance process.
            docker("stop", "--time", "10", name)
            docker("rm", name)
            container = None
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{volume}:/data",
                "--entrypoint",
                "python",
                "vinted-monitoring:test",
                "-c",
                seed,
            )
        print(
            json.dumps(
                {
                    "container": "passed",
                    "mcp_tools": 8,
                    "persistent_restart": "passed",
                    "external_network": "disabled",
                }
            )
        )
    finally:
        if container:
            docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)


if __name__ == "__main__":
    main()
