# Hermes Multiplex Plugin — Architecture Blueprint

## Problem Statement

Goran is locked to the conductor profile in Telegram. Cannot directly interact with any other agent (analyst, coder, etc.). Needs to:
1. Switch between agent sessions within a single chat
2. See responses from all agents with session-specific prefixes
3. Spawn new sessions without interrupting current session
4. Message any agent with `@agent` syntax from any session
5. Receive subagent delegation responses inline

---

## Architecture Overview

### Component Map

```
┌─────────────────────────────────────────────────────────┐
│                    TELEGRAM / WHATSAPP                    │
│                      (any platform)                      │
└──────────────────────┬──────────────────────────────────┘
                       │ Message arrives at gateway
                       ▼
┌─────────────────────────────────────────────────────────┐
│                 MULTIPLEX PLUGIN                         │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐         │
│  │ PREFIX   │  │ SESSION   │  │ RESPONSE     │         │
│  │ PARSER   │  │ TRACKER   │  │ RELAY        │         │
│  │          │  │           │  │              │         │
│  │ @analyst │  │ per-agent │  │ [analyst-01] │         │
│  │ @coder   │  │ session   │  │ prefix       │         │
│  │ /switch  │  │ state     │  │ injection    │         │
│  │ /spawn   │  │           │  │              │         │
│  └──────────┘  └───────────┘  └──────────────┘         │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │              CONFIG STORE (YAML)                  │  │
│  │  - Agent mapping (name → profile)                │  │
│  │  - Session history per chat_id                   │  │
│  │  - Prefix format configuration                   │  │
│  │  - Auto-switch on spawn (yes/no)                 │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │ Routed to correct profile
                       ▼
┌─────────────────────────────────────────────────────────┐
│              GATEWAY PROFILE ROUTER                      │
│                                                         │
│  conductor ──→ analyst ──→ coder ──→ reviewer ──→ ...  │
│  (default)      (active)    (idle)     (idle)           │
│                                                         │
│  Each profile gets its own:                             │
│  - Session store (isolated context)                     │
│  - System prompt (role-specific)                        │
│  - Toolset (role-specific)                             │
│  - Memory (Graphiti/Tier2 for conductor only)           │
└─────────────────────────────────────────────────────────┘
```

---

## Command Design

### Prefix-Based Routing (Primary)

```
/analyst investigate gateway OOM
         ↓
    Multiplex Gate strips "/analyst"
         ↓
    Routes "investigate gateway OOM" to analyst profile
         ↓
    Analyst responds → [analyst-4f2a] Root cause: socket leak
```

### Session Switching Commands

| Command | Action | Example |
|---------|--------|---------|
| `/switch analyst` | Switch current chat to analyst session | Messages now go to analyst |
| `/switch main` | Return to conductor (main) session | Default behavior |
| `/switch @coder` | Alternative syntax | Same as /switch coder |
| `/spawn architect [task]` | Create NEW architect session, optionally assign task | `/spawn architect design API` |
| `/spawn architect autoswitch` | Spawn and auto-switch to new session | Configurable default |
| `/list` | List all active sessions + their status | `[main-a3f2] Running, [analyst-4f2a] Idle 5m` |

### Session-Aware Prefixing

Every response from an agent is prefixed with `[agent-session_id]`:

```
[analyst-4f2a] Root cause: WebSocket handler at discord.js:3275
               needs close() call. Evidence: 3 code paths, 0.92 confidence.

[coder-7b1c]    Fix applied — added close handler at discord.js:3312.
                Tests: 4 pass, 0 fail.

[main-d9e3]     Goran, coder applied the fix. Want me to spawn reviewer
                for verification?
```

### Cross-Session Messaging

From ANY session, you can message another agent:

```
[analyst-4f2a] I've traced the root cause.
@coder patch discord.js:3275 with close handler

→ Conductor intercepts @coder → routes to coder-7b1c session
→ If coder session doesn't exist, auto-creates one
→ Coder processes and responds
→ Both messages show in chat with correct prefixes
```

### Subagent Delegation Visibility

When the conductor delegates to a subagent:

```
You:  @conductor analyze gateway OOM
                    │
[main-d9e3]         Spawning analyst for investigation...
                    │
[analyst-4f2a]      Investigating... analyzing 3 code paths
[analyst-4f2a]      Root cause found: WebSocket leak, 0.92 confidence
                    │
[main-d9e3]         Analyst complete. Root cause: socket leak.
                    Spawning reviewer for verification...
                    │
[reviewer-2e8f]     Verifying analyst findings...
[reviewer-2e8f]     CONFIRMED: 3/3 evidence chains match. PASS.
                    │
[main-d9e3]         All subagents complete. Fix available.
                    Want me to spawn coder?
```

---

## Session State Machine

```
                    ┌────────┐
     new message ──→│ ROUTE  │←── message with @agent prefix
                    └───┬────┘
                        │
                ┌───────┼───────┐
                ▼       ▼       ▼
           ┌────────┐ ┌──────┐ ┌──────────┐
           │CONDUCTOR│ │AGENT1│ │AGENT2... │
           │(default)│ │      │ │          │
           └───┬────┘ └──┬───┘ └────┬─────┘
               │         │          │
               │    ┌────▼────┐     │
               │    │PROCESS  │     │
               │    └────┬────┘     │
               │         │          │
               ▼         ▼          ▼
           ┌──────────────────────────────┐
           │    RESPONSE with [prefix]    │
           │    delivered to Telegram     │
           └──────────────────────────────┘

Session Lifecycle:
  ACTIVE → message received → keep alive
  ACTIVE → 15min idle → IDLE (session kept, context preserved)
  IDLE → message → ACTIVE (resume with full context)
  IDLE → /switch → ACTIVE (user manually activates)
  NEW → /spawn → ACTIVE (fresh session)
  DONE → agent signals completion → ARCHIVED (summary kept)
```

---

## Integration Points (Based on Gateway Code Analysis)

### Gateway Hook: `pre_gateway_dispatch`
**Location:** `gateway/run.py:5804`
**What it does:** Fires BEFORE message reaches profile router. Perfect injection point for prefix detection.

```
Message arrives → pre_gateway_dispatch hook
    → Multiplex plugin: strip prefix, lookup session, reroute
    → OR: pass through unchanged (conductor default)
```

### Session Key Manipulation
**Location:** `gateway/session.py:600`
**Current:** `agent:main:{platform}:{chat_type}:{chat_id}[...]`
**Multiplex modification:** `agent:{agent_name}:{platform}:{chat_type}:{chat_id}:{session_id}`

Each agent gets its own session key namespace within the same chat.

### Plugin Registration
**Reference:** `hermes_cli/plugins.py:287` (PluginContext)
**Pattern:** 
```yaml
# plugin.yaml
name: multiplex
kind: standalone  # or backend
provides_tools:
  - switch_session
  - spawn_session
  - list_sessions
commands:
  - /switch
  - /spawn
  - /list
  - /sessions
```

### Config API (EVOL-style reference)
**Reference:** EVOL plugin `config.py` pattern
**Multiplex config:**
```yaml
# ~/.hermes/profiles/conductor/plugins/multiplex/config.yaml
multiplex:
  agents:
    analyst: { profile: "analyst", enabled: true, prefix: "[analyst]" }
    coder: { profile: "coder", enabled: true, prefix: "[coder]" }
    researcher: { profile: "researcher", enabled: true, prefix: "[researcher]" }
    operative: { profile: "operative", enabled: true, prefix: "[operative]" }
    reviewer: { profile: "reviewer", enabled: true, prefix: "[reviewer]" }
    architect: { profile: "architect", enabled: true, prefix: "[architect]" }
    orchestrator: { profile: "orchestrator", enabled: true, prefix: "[orch]" }
    shadow: { profile: "shadow", enabled: true, prefix: "[shadow]" }
    valmet: { profile: "valmet", enabled: true, prefix: "[valmet]" }
  settings:
    default_agent: "conductor"
    auto_switch_on_spawn: false
    session_idle_timeout: 900  # 15 minutes
    show_delegation_chain: true
    prefix_format: "[{agent}-{session_id}]"
  platforms:
    telegram: { enabled: true }
    whatsapp: { enabled: false, adapter: "whatsapp_web" }
    discord: { enabled: false }
    cli: { enabled: true }
```

---

## Component Specifications

### 1. Prefix Parser (`src/parser.py`)
```
Input:  "/analyst investigate gateway OOM"
Output: { agent: "analyst", message: "investigate gateway OOM", command: null }

Input:  "/switch coder"
Output: { agent: null, message: null, command: { type: "switch", target: "coder" } }

Input:  "/spawn researcher search for alternatives"
Output: { agent: null, message: "search for alternatives", command: { type: "spawn", target: "researcher" } }

Input:  "@coder fix the bug"
Output: { agent: "coder", message: "fix the bug", command: null }
```

**Supports:** `/prefix`, `@prefix`, `!prefix`, configurable.

### 2. Session Tracker (`src/tracker.py`)
```
Data structure:
{
  "telegram:-1001234567890": {
    "active": "analyst-4f2a",
    "sessions": {
      "main-d9e3": {
        "profile": "conductor",
        "state": "ACTIVE",
        "created": "2026-06-01T15:00:00Z",
        "last_active": "2026-06-01T15:05:00Z"
      },
      "analyst-4f2a": {
        "profile": "analyst",
        "state": "IDLE",
        "created": "2026-06-01T14:45:00Z",
        "last_active": "2026-06-01T14:50:00Z"
      }
    }
  }
}
```

**Operations:**
- `get_session(chat_id, agent)` → session_id
- `set_active(chat_id, session_id)` → switch context
- `create_session(chat_id, agent, profile)` → new session_id
- `list_sessions(chat_id)` → all active sessions

### 3. Response Relay (`src/relay.py`)
```
Input:  agent_output from any profile
Output: prefixed message to Telegram

Process:
1. Agent produces response (via gateway response handler)
2. Relay intercepts via post_response hook
3. Injects [agent-session_id] prefix
4. Forwards to platform adapter for delivery
5. Logs to session transcript
```

### 4. Config Manager (`src/config.py`)
```
- Load YAML config ~/.hermes/profiles/conductor/plugins/multiplex/config.yaml
- Register slash commands through PluginContext
- Expose config via /multiplex config set/get commands
- Hot-reload support (watch file changes)
```

### 5. Command Registry (`src/commands.py`)
```
Registered commands:
  /switch <agent>     → activate agent session
  /spawn <agent>      → create new session
  /list               → show active sessions
  /sessions           → detailed session info
  /multiplex config   → get/set configuration
  /multiplex status   → plugin health + stats
```

---

## Platform Abstraction

The multiplex plugin MUST be platform-agnostic. Same commands and prefixes work on Telegram, WhatsApp, Discord, CLI.

```
Message In → ParseMessage (platform adapter)
    → MultiplexGate (prefix detection, routing)
    → ProfileRouter (existing gateway mechanism)
    → ResponseOut → FormatMessage (platform adapter)
```

Each platform implements two functions:
- `parse_message(raw_input) → { text, platform, chat_id, user_id, thread_id }`
- `format_output(agent_response, prefix, platform_context) → platform_native_message`

Initial focus: **Telegram** (existing integration). WhatsApp/Discord follow once Telegram implementation is stable.

---

## Deployment & Configuration Model (EVOL-style)

Following the EVOL plugin reference architecture:

### Installation
```bash
# Clone to plugins directory
cd ~/.hermes/plugins/
git clone https://github.com/falke-ai-circuit/hermes-multiplex.git multiplex

# Enable in gateway config.yaml
plugins:
  enabled:
    - multiplex

# Restart gateway (required for plugin code changes)
docker restart hermes-agent-llic-conductor-1
```

### Runtime Configuration
```bash
# All configuration via chat commands
/multiplex config set agents.analyst.enabled true
/multiplex config set settings.auto_switch_on_spawn true
/multiplex config set settings.prefix_format "[{agent}-{session_id}]"
/multiplex config get  # Show full config
```

### State Persistence
```
~/.hermes/profiles/conductor/plugins/multiplex/
├── config.yaml          # User configuration
├── state/               # Runtime state (session tracker)
│   └── sessions.json    # Per-chat session registry
├── logs/                # Plugin operation logs
└── migrations/          # Config schema migrations
```

---

## Roadmap

### Phase 0: Foundation (Week 1)
- [ ] Project repo setup: `falke-ai-circuit/hermes-multiplex`
- [ ] Plugin skeleton with `plugin.yaml`, `register()`, empty commands
- [ ] Config YAML schema + validation
- [ ] Test harness: simulate Telegram messages

### Phase 1: Core Routing (Week 2)
- [ ] Prefix Parser: `/agent`, `@agent`, `/switch`, `/spawn`
- [ ] Session Tracker: create, switch, idle, archive
- [ ] Integration with `pre_gateway_dispatch` hook
- [ ] Basic response relay with prefix injection

### Phase 2: Full Session Management (Week 3)
- [ ] `/switch` command with session persistence
- [ ] `/spawn` with optional auto-switch
- [ ] `/list` active sessions display
- [ ] Cross-session `@agent` messaging from any session

### Phase 3: Subagent Visibility (Week 4)
- [ ] Intercept `delegate_task` responses for inline display
- [ ] Chain prefix: `[main-d9e3→analyst-4f2a]` for delegation chains
- [ ] Completion signaling: "Analyst done. Coder applying fix..."

### Phase 4: Platform Abstraction (Week 5)
- [ ] Platform adapter interface
- [ ] WhatsApp web adapter (secondary)
- [ ] Discord adapter (secondary)
- [ ] CLI multi-session support

### Phase 5: Configuration UI (Week 6)
- [ ] `/multiplex config` command suite (get/set/reset)
- [ ] Agent enable/disable per platform
- [ ] Prefix format customization
- [ ] Session retention policies

---

## Technical Constraints (From Gateway Code Analysis)

| Constraint | Impact | Mitigation |
|-----------|--------|------------|
| No `sessions_spawn` API | Cannot spawn full gateway sessions programmatically | Use `delegate_task` as proxy; build session abstraction on top |
| Per-session adapter locking | `_active_sessions` guard serializes messages per session key | Multiplex own session keys; don't fight the lock |
| ContextVars for concurrency | Tool routing uses task-local state | Compatible — our session lookup is read-only on context vars |
| Plugins opt-in via config.yaml | Must be explicitly enabled | Document installation clearly |
| Profile isolation | Each agent gets full profile directory | Leverage existing profiles; no new infrastructure |
| Gateway restart required | Plugin code changes need gateway restart | Document; hot-reload config changes only |

---

## Architecture Decision Records (ADR)

### ADR-1: Prefix Detection Location
**Decision:** Intercept at `pre_gateway_dispatch` hook (before profile routing).
**Rationale:** Earliest possible interception. Avoids modifying core routing code. If multiplex is disabled, zero overhead.
**Alternative:** Post-routing interception → would require profile to process message first, adding latency.

### ADR-2: Session Storage
**Decision:** JSON file per chat_id on local filesystem.
**Rationale:** Simple. Survives restarts. No external dependency. Migrate to Redis if scale demands it.
**Alternative:** SQLite → adds dependency. In-memory → lost on restart.

### ADR-3: Agent Communication Protocol
**Decision:** Conductor mediates all cross-agent communication. `@agent` messages route through conductor → target agent.
**Rationale:** Single point of control. Easy to add logging, filtering, rate limiting. Matches existing architecture.
**Alternative:** Peer-to-peer agent messaging → complex state management, harder to debug.

### ADR-4: Platform Scope
**Decision:** Telegram first. WhatsApp/Discord follow modular adapter pattern.
**Rationale:** Existing integration is stable. Goran uses Telegram daily. Platform abstraction layer designed from start.
