"""Startup hook: make sure the server pane is running, opening it if not.

After a herdr restart the plugin's tab comes back as a bare shell with the pane's
title and no process in it. Close those and open a fresh plugin pane in the
workspace labelled for it, creating that workspace when there is none.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "herdr-mcp-server")
ENTRYPOINT = "server"
TITLE = "herdr-mcp"
# the workspace the server pane lives in; agents it starts go where HERDR_MCP_WORKSPACE says
WORKSPACE = "herdr-mcp"
HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")


def herdr(*args: str) -> dict:
    out = subprocess.run([HERDR, *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout).get("result", {})


def running() -> bool:
    """A server.py whose working directory is this checkout is the pane's process."""
    pids = subprocess.run(
        ["pgrep", "-f", "python server.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    for pid in pids.stdout.split():
        lsof = subprocess.run(
            ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
        )
        cwd = next((l[1:] for l in lsof.stdout.splitlines() if l.startswith("n")), "")
        if Path(cwd or "/") == ROOT:
            return True
    return False


def main() -> int:
    if running():
        print(f"ensure-pane: {PLUGIN_ID}/{ENTRYPOINT} running")
        return 0
    workspaces = herdr("workspace", "list").get("workspaces") or []
    found = next((w for w in workspaces if w.get("label") == WORKSPACE), None)
    if found is None:
        created = herdr(
            "workspace",
            "create",
            "--label",
            WORKSPACE,
            "--cwd",
            str(ROOT),
            "--no-focus",
        )
        workspace_id = created["workspace"]["workspace_id"]
    else:
        workspace_id = found["workspace_id"]
    # the restored tab carries the pane's title but no process; each restart would stack one
    for pane in herdr("pane", "list").get("panes") or []:
        if pane.get("label") == TITLE and pane.get("workspace_id") == workspace_id:
            subprocess.run([HERDR, "pane", "close", pane["pane_id"]], check=False)
    subprocess.run(
        [
            HERDR, "plugin", "pane", "open",
            "--plugin", PLUGIN_ID, "--entrypoint", ENTRYPOINT,
            "--placement", "tab", "--workspace", workspace_id, "--no-focus",
        ],
        check=True,
    )  # fmt: skip
    print(f"ensure-pane: {PLUGIN_ID}/{ENTRYPOINT} opened in {workspace_id}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(
            f"ensure-pane: {exc.cmd[1:3]} failed: {exc.stderr or exc.stdout}",
            file=sys.stderr,
        )
        sys.exit(1)
