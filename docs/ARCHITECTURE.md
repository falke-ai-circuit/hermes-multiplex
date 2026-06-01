# Hermes Multiplex Plugin — Architecture Blueprint v2

## Problem Statement

Goran is locked to the conductor profile in Telegram. Cannot directly interact with any other agent. Needs:

1. **Chat with any agent** using natural `@agentname` mentions
2. **Single chat, multiple agents** — all responses appear inline with prefixes
3. **Persistent sessions** — `@analyst` messages go to same analyst session until explicitly reset
4. **Session management** via `/multix` commands
5. **Subagent visibility** — see delegation chain responses inline

---

## Command Design

### Primary: `@agentname` Inline Mentions (Natural Chat)

```
Goran: @analyst investigate why gateway OOM keeps happening
       @researcher find open source alternatives to Docmost

[analyst-4f2a]    Investigating... tracing 3 code paths
[researcher-b7d3] Searching SearXNG... 12 results found

[analyst-4f2a]    Root cause: WebSocket leak at discord.js:3275. 
                  Confidence 0.92. Evidence: 3 independent paths converge.

[researcher-b7d3] Top alternatives: HedgeDoc (Docker, free, REST API),
                  Outline (requires license), BookStack (no real-time collab).

Goran: @analyst what's the fix?
[analyst-4f2a]    Same session (4f2a). Fix: add close() handler at line 3275.
                  Want me to tell @coder?

Goran: @coder apply the fix at discord.js:3275
[coder-8e1c]       Applied. 4 tests pass, 0 fail.

Goran: @analyst-4f2a what was that confidence score again?
[analyst-4f2a]    0.92 — traced from 3 independent code paths.
```

### Secondary: `/multix` Management Commands

```
/multix switch analyst     → Switch default routing to analyst (no prefix = analyst)
/multix switch main        → Return default routing to conductor
/multix spawn researcher   → Create NEW researcher session (fresh context)
/multix list               → Show all active sessions + status
/multix list analyst       → Show all analyst sessions
/multix kill analyst-4f2a  → End a specific session
/multix config             → Show current configuration
```

### Routing Logic

```
Message arrives
    │
    ├── Contains @agentname? → Route to that agent's active session
    │   └── No active session? → Create new session for that agent
    │
    ├── Contains @sessionid? → Route to that specific session
    │   └── Session doesn't exist? → Reply "[conductor] Session X not found. Active: ..."
    │
    ├── Starts with /multix? → Process management command
    │
    └── No prefix? → Route to currently active session (default: conductor)
```

---

## Response Format

Every agent response is prefixed with `[agent-session_id]`:

```
[analyst-4f2a]    4-character session ID for easy reference
[coder-8e1c]      Same format for all agents
[researcher-b7d3] You can @mention the session ID: @analyst-4f2a
[main-d9e3]       Conductor sessions use "main" as agent name
[orch-2f1a]       Orchestrator follows same pattern
```

### Subagent Delegation Visibility

When the conductor delegates, you see the chain inline:

```
Goran: @conductor analyze gateway OOM end-to-end

[main-d9e3]        Spawning analyst...

[analyst-4f2a]     Root cause: WebSocket leak at discord.js:3275 (0.92)
[main-d9e3]        Analyst done. Spawning reviewer...

[reviewer-5b2c]    Verifying... CONFIRMED. 3/3 evidence chains match. PASS.
[main-d9e3]        All checks passed. Spawning coder...

[coder-8e1c]       Patch applied at discord.js:3275. 4 tests pass.
[main-d9e3]        Done. Fix applied and verified. 
                   Root cause: WebSocket leak. Fix: close handler. Tests: 4/4.
```

---

## Session State Machine

```
                    ┌────────┐
     message ───────→│ ROUTE  │
                    └───┬────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
    @agentname     /multix cmd    no prefix
          │             │             │
          ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────────┐
   │Find/create│  │Process   │  │Route to      │
   │session   │  │command   │  │active session│
   └────┬─────┘  └──────────┘  └──────┬───────┘
        │                             │
        ▼                             ▼
   ┌──────────────────────────────────────┐
   │        Agent processes message       │
   └────────────────┬─────────────────────┘
                    │
                    ▼
   ┌──────────────────────────────────────┐
   │  Response with [agent-session_id]    │
   │  delivered to SAME Telegram chat     │
   └──────────────────────────────────────┘
```

**Session lifecycle:**
- ACTIVE → receiving messages → keep alive
- 15min idle → IDLE (context preserved)
- IDLE → @mention → ACTIVE (resume with full context)
- `/multix kill` → ARCHIVED
- `/multix spawn` → NEW (fresh context, even if active session exists)

---

## Agent Configuration

```yaml
# ~/.hermes/profiles/conductor/plugins/multiplex/config.yaml

multiplex:
  agents:
    analyst:
      profile: "analyst"
      prefix: "[analyst]"
      description: "Root cause investigation, code analysis, FalkorDB graphs"
      auto_create: true
    
    coder:
      profile: "coder"
      prefix: "[coder]"
      description: "Code changes, OpenHands delegation, repo work"
      auto_create: true
    
    researcher:
      profile: "researcher"
      prefix: "[researcher]"
      description: "Web research, SearXNG, OSINT, comparative analysis"
      auto_create: true
    
    operative:
      profile: "operative"
      prefix: "[operative]"
      description: "Docker, SSH, infrastructure, deployments"
      auto_create: true
    
    reviewer:
      profile: "reviewer"
      prefix: "[reviewer]"
      description: "Code verification, Selenium testing, adversarial validation"
      auto_create: true
    
    architect:
      profile: "architect"
      prefix: "[architect]"
      description: "Blueprint design, Docmost publishing, system architecture"
      auto_create: true
    
    orchestrator:
      profile: "orchestrator"
      prefix: "[orch]"
      description: "Multi-agent lane coordination, taskboard management"
      auto_create: true
    
    shadow:
      profile: "shadow"
      prefix: "[shadow]"
      description: "Offensive security, dark reasoning, Venice API"
      auto_create: false  # Requires explicit spawn
    
    valmet:
      profile: "valmet"
      prefix: "[valmet]"
      description: "Industrial automation, DNA protocols, LightRAG"
      auto_create: false  # Requires explicit spawn
  
  settings:
    default_agent: "conductor"
    conductor_prefix: "[main]"
    session_idle_timeout: 900  # 15 minutes
    show_delegation_chain: true  # Show [main→analyst→reviewer] chain
    prefix_format: "[{agent}-{session_id}]"
    auto_create_agents: true  # Create session on first @mention
  
  platforms:
    telegram:
      enabled: true
    cli:
      enabled: true
```

---

## Plugin Structure

```
hermes-multiplex/
├── plugin.yaml              # Manifest
├── src/
│   ├── __init__.py
│   ├── parser.py            # @agentname, @sessionid, /multix detection
│   ├── tracker.py           # Per-chat session state (ACTIVE/IDLE/ARCHIVED)
│   ├── router.py            # Message routing to correct profile session
│   ├── relay.py             # Response prefix injection
│   └── commands.py          # /multix switch, spawn, list, kill, config
├── config/
│   └── config.yaml          # Default agent mapping + settings
├── state/
│   └── sessions.json        # Runtime session registry per chat_id
├── tests/
│   ├── test_parser.py
│   ├── test_tracker.py
│   └── test_router.py
└── docs/
    └── ARCHITECTURE.md
```

---

## Gateway Integration Points

| Hook | Location | What It Does |
|------|----------|-------------|
| `pre_gateway_dispatch` | `gateway/run.py:5804` | Intercept message BEFORE profile routing — detect @mentions, /multix |
| Session key manipulation | `gateway/session.py:600` | Inject `agent_name:session_id` into session keys for per-agent isolation |
| Response handler | Gateway output adapter | Inject `[agent-session_id]` prefix before platform delivery |
| `delegate_task` interceptor | `tools/delegate_tool.py` | Capture subagent responses for inline visibility |

**Session key format:** `agent:{agent_name}:{platform}:{chat_type}:{chat_id}:{session_id}`

---

## Phase 0 Deliverables (Skeleton)

- [x] Project scaffold + plugin.yaml manifest
- [x] Architecture blueprint (this document)
- [x] GitHub repo: `falke-ai-circuit/hermes-multiplex`
- [ ] `pre_gateway_dispatch` trace — verify hook fires before profile routing
- [ ] Session key isolation test — confirm per-agent context separation
- [ ] `delegate_task` response intercept proof-of-concept

---

## Roadmap

### Phase 1: Core Routing (Week 1-2)
- [ ] `@agentname` parser — extract agent name + message from inline mention
- [ ] `@sessionid` parser — route to specific session by ID
- [ ] Session tracker — create, find, resume, idle, archive
- [ ] `/multix` command parser — switch, spawn, list, kill, config
- [ ] Gateway `pre_gateway_dispatch` integration
- [ ] Response prefix injection `[agent-session_id]`

### Phase 2: Session Management (Week 2-3)
- [ ] `/multix switch` — change default routing
- [ ] `/multix spawn` — explicit new session
- [ ] `/multix list` — active sessions display
- [ ] `/multix kill` — session termination
- [ ] Session persistence — survive gateway restart

### Phase 3: Subagent Visibility (Week 3-4)
- [ ] `delegate_task` response interceptor
- [ ] Inline subagent chain: `[main→analyst→reviewer]`
- [ ] Completion signaling: "Analyst done. Coder applying fix..."

### Phase 4: Configuration (Week 4-5)
- [ ] `/multix config get/set`
- [ ] Per-agent enable/disable
- [ ] Prefix format customization
- [ ] Auto-create agent toggle

### Phase 5: Platform Abstraction (Week 5-6)
- [ ] Platform adapter interface
- [ ] CLI multi-session support
- [ ] Platform-specific response formatting

---

## Architecture Decisions

| ADR | Decision | Rationale |
|-----|----------|-----------|
| **ADR-1** | One prefix: `/multix` | No namespace pollution. All agent + session management under one command. |
| **ADR-2** | `@agentname` for chat | Natural — same as mentioning someone. Zero learning curve. |
| **ADR-3** | `@sessionid` for precision | Reference specific past sessions by their 4-char ID. |
| **ADR-4** | `pre_gateway_dispatch` hook | Earliest interception. No core routing changes. |
| **ADR-5** | Single chat, mixed responses | Goran's core requirement. All agents respond in the SAME chat. |
| **ADR-6** | Conductor always mediates | Single control point. Logging, filtering, rate limiting centralized. |
