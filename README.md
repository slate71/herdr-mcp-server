# herdr-mcp-server

An MCP server that lets an assistant drive the coding agents running in [herdr](https://herdr.dev) on your computer. A voice or chat assistant can start an agent on a task, check on any agent session (including ones you started in a terminal), send it follow-ups, answer its questions and approve its permission prompts. You can take over any session in herdr at any time.

It always works through a coding agent: it starts one in a new tab and only ever talks to it, never to a bare shell. [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude`) is the only kind so far; another kind needs a reader for its transcript.

It talks to herdr through the `herdr` CLI, and reads what each session said from its transcript under `~/.claude/projects`. Agents started from here open as unlabelled tabs in one herdr workspace (`herdr-mcp` unless configured or named in the call), so herdr keeps naming them from what runs in them.

It was built for the [Home Assistant voice agent](https://github.com/longcw/home-assistant-mcp-agent), but works with any MCP client.

## Tools

| Tool | What it does |
| --- | --- |
| `list_herdr_agents(query?)` | Sessions that are working, blocked or active in the last 6 hours; a query searches all of them by title, folder, workspace or state |
| `get_herdr_agent(agent)` | A session's state and the last thing the agent said in its current turn |
| `watch_herdr_agent(agent)` | Follows a session until it finishes or needs you |
| `prompt_herdr_agent(agent, text)` | Sends a follow-up, or answers the multiple-choice question the session asks |
| `answer_herdr_permission(agent, allow)` | Approves once, or refuses, the permission prompt a session is blocked on |
| `start_herdr_agent(prompt, kind?, folder?, workspace?)` | Starts an agent (`claude`) on a task, in a folder under `~/code` by default |
| `stop_herdr_agent(agent)` | Interrupts a working session |

An `agent` argument is a description, not an id: words from the title ("the agents-js issue"), a folder or workspace, a state ("the blocked one"), "the focused one", or a pane id. When more than one session fits, the tool lists them so the client can ask which one.

The tool descriptions tell the calling model to confirm before `start_herdr_agent` and before approving a permission, and to send follow-ups about a session's work back to that session instead of answering them itself. Every result names the session's pane id for that. The server does not enforce any of it.

## Results that outlive a tool call

`start_herdr_agent`, `watch_herdr_agent`, `prompt_herdr_agent` and `answer_herdr_permission` send an MCP progress notification at once, so a client that supports progress can keep the conversation going. Each tool then waits up to `HERDR_MCP_WATCH_WINDOW` seconds for the agent to finish or block. One still working after that is reported through a webhook. The client names its webhook in two headers on every MCP request:

- `X-Callback-Url`: where to POST. Put any routing the client needs, such as a conversation or user, in the URL itself.
- `X-Callback-Token`: sent back as `Authorization: Bearer <token>`.

When the session settles, the server POSTs this once:

```json
{"source": "Claude Code", "text": "\"Fix the flaky test\" finished.\nLast message: …", "title": "Fix the flaky test", "state": "done", "pane_id": "w3:p2"}
```

`state` is `done`, `idle`, `blocked` or `closed`. A failed callback is retried with backoff, up to 10 times. Pending callbacks are saved in `~/.local/state/herdr-mcp-server/subscriptions.json`, so they survive a restart. A client that sends no headers gets "still working" and can ask again later.

## Run

Needs [uv](https://docs.astral.sh/uv/), herdr, and Claude Code logged in.

```bash
uv sync
uv run python server.py        # serves http://0.0.0.0:8960/mcp
```

On macOS, to run it at login and restart it if it dies:

```bash
scripts/install-launchagent.sh
```

The server only talks to a herdr server that is already running; it never starts one.

The server has no authentication of its own, and a client can use it to run code on this computer. Only expose it on a network you trust, such as a home LAN reachable from outside only through Tailscale.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HERDR_MCP_HOST` / `HERDR_MCP_PORT` | `0.0.0.0` / `8960` | Where the MCP server listens |
| `HERDR_MCP_WORKSPACE` | `herdr-mcp` | herdr workspace label for agents started from here, when the call names none |
| `HERDR_MCP_CODE_DIR` | `~/code` | Default folder for new agents, and where folder names are looked up |
| `HERDR_MCP_WATCH_WINDOW` | `60` | Seconds a tool call waits before its result goes to the webhook instead |
| `HERDR_MCP_SETTLE` | `4` | Seconds a session has to stay idle or blocked before it counts as settled |
| `HERDR_MCP_SUBSCRIPTION_TTL_HOURS` | `24` | How long a webhook waits for its session before it is dropped |
| `HERDR_BIN` | `herdr` on `PATH` | The herdr CLI |

## Limits

- Only settled states are reported, not the messages Claude Code writes while it works.
- A question with several parts has to be answered in herdr.
- Session state comes from herdr, which detects it from the pane's contents, so an unusual screen can be misread.
