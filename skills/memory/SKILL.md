---
name: memory
description: Persistent memory backed by SQLite FTS5 via the memory-search service
triggers:
  - remember
  - forget
  - recall
  - memory
allowed-tools:
  - Bash
---

# Memory

You have access to a persistent memory store via the memory-search service at http://localhost:7890.

## Operations

### Save a memory
```bash
curl -s -X POST http://localhost:7890/memory/save \
  -H "Content-Type: application/json" \
  -d '{"type": "fact", "content": "...", "contact": "platform:sender_id"}'
```

Memory types: `fact`, `preference`, `lesson`, `project`, `relationship`, `context`

### Search memories
```bash
curl -s "http://localhost:7890/memory/search?q=query&contact=platform:sender_id&limit=5"
```

### Load all memories for a contact
```bash
curl -s "http://localhost:7890/memory/load?contact=platform:sender_id"
```

### Memory stats
```bash
curl -s http://localhost:7890/memory/stats
```

## When to use memory

- Proactively save facts the user shares about themselves, their preferences, ongoing projects, or important context.
- Search memory at the start of a conversation to recall relevant context.
- Save lessons learned from mistakes or corrections.
- Admin can see all memories. Trusted contacts see only their own.
