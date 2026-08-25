# Security Policy

## Maintenance status

The project is actively maintained. The current scope is deliberately narrow so it stays
sustainable — the previous 14-month gap happened because the goal was "maintain every feature
for every client", which did not hold.

**In scope right now:**
- ✅ Security vulnerabilities
- ✅ Compatibility breaks that make the server unusable on install (dependency updates,
  upstream breaking changes)
- ✅ Regression tests guarding the above

**Decided from community feedback** (see the pinned discussion):
- Which pre-existing defects to fix (timeouts, session history, per-client compatibility)
- Whether to move toward native MCP Elicitation / Apps to shrink the implementation

**Out of scope permanently:**
- ❌ Arbitrary command execution (removed in v2.6.1, will not return)
- ❌ Prompt patterns designed to force a callback on every step (they existed to save
  request quota, which no longer applies)

## Supported versions

| Version | Supported |
|---------|-----------|
| 2.6.1 and later | ✅ Supported |
| 2.6.0 and earlier | ❌ **Not supported — contains a known command execution issue (see below)** |

If you are on 2.6.0 or earlier, upgrade:

```bash
uvx mcp-feedback-enhanced@latest
```

## Issues fixed in v2.6.1

### Unauthenticated command execution (issue [#219](https://github.com/Minidoracat/mcp-feedback-enhanced/issues/219))

**Affected:** all versions up to and including 2.6.0
**Fixed in:** 2.6.1
**Impact:** arbitrary program execution in the project directory

Versions up to 2.6.0 accepted a `run_command` message over the WebSocket
endpoint `/ws` and passed it to `subprocess.Popen`. Three factors combined
to make this exploitable:

1. `/ws` had **no authentication** — it only checked whether an active session existed,
   and a new connection would take over the existing session's socket.
2. The safety check was a **blocklist of shell metacharacters** (`;`, `&&`, `||`, `|`,
   `>`, `<`, backtick, `$(`, `rm -rf`, …). Because execution used `shell=False`,
   metacharacters were never the risk — plain binaries such as `cat`, `curl`,
   `wget`, `python`, and `powershell` passed straight through.
3. The auto-command feature was **enabled by default** (`autoCommandEnabled: true`),
   so a configured command would fire automatically on new sessions and on
   feedback submission.

Default binding is `127.0.0.1`, which limits exposure to local processes.
However, older documentation recommended setting `MCP_WEB_HOST=0.0.0.0` for
SSH remote development, which turned this into a network-reachable issue.

**Resolution:** command execution was **removed entirely** rather than hardened.
A blocklist cannot safely permit arbitrary executables. The WebSocket handler,
session methods, frontend UI, and related settings are all gone, and
`tests/unit/test_no_command_execution.py` guards against reintroduction.

### Cross-Site WebSocket Hijacking (CSWSH)

Reported privately as `GHSA-cmr5-gpm3-79vf` (critical) and
`GHSA-2wx7-r4rh-f663` (high). Thanks to both reporters for the detailed
write-ups and working proofs of concept.

**Affected:** all versions up to and including 2.6.0
**Fixed in:** 2.6.1
**Impact:** in ≤2.6.0, arbitrary command execution triggered from a web page

Browsers are **not** restricted by the same-origin policy when opening a
WebSocket. A malicious page could therefore make the victim's browser connect
to `ws://127.0.0.1:<port>/ws` and send messages, because `/ws` accepted any
`Origin` and called `accept()` without validation. Combined with the
`run_command` handler above, this escalated to remote code execution — the
loopback binding did not help, since the browser itself is local.

**Resolution — two independent layers:**

1. Command execution was removed entirely (see above), so the escalation path is gone.
2. `/ws` now validates `Origin` **before** `accept()`. Only loopback origins on
   the server's own port, the bound host itself, and desktop WebView schemes are
   allowed. Requests without an `Origin` header (non-browser clients such as the
   desktop app) are still permitted, since a cross-origin page cannot suppress
   the header.

Cross-origin connection attempts are rejected with HTTP 403 before the handshake
completes. `tests/unit/test_websocket_origin.py` guards this, using the exact
origins from the reported proofs of concept.

## Remaining security considerations

These are **known and unfixed** properties of the current architecture. Treat this
tool as local, single-user development tooling:

- **No authentication on the Web UI or WebSocket.** Any *local process* that can
  reach the port can read session content (project path, AI summary, feedback
  history) and submit feedback on your behalf. Origin validation stops malicious
  *web pages*, but it cannot stop a local process — an `Origin` header is only
  enforced by browsers.
- **HTTP `/api/*` endpoints are unauthenticated.** Cross-origin pages cannot read
  the responses (the same-origin policy does apply to `fetch`), but a local
  process can.
- **`MCP_WEB_HOST=0.0.0.0` is unsafe.** It exposes the above to anyone who can
  reach the port. Use SSH port forwarding instead.

Keep the default `127.0.0.1` binding and use SSH port forwarding for remote work.

## Reporting a vulnerability

Report privately via
[GitHub Security Advisories](https://github.com/Minidoracat/mcp-feedback-enhanced/security/advisories/new).

Please do not open a public issue for a vulnerability that is not already public.

Include where possible:

- affected version
- reproduction steps or a proof of concept
- impact assessment

Security reports are prioritised. For anything that cannot be fixed within the current
architecture, it will be documented here rather than silently left open — the
"no authentication on the Web UI" item above is an example of that.
