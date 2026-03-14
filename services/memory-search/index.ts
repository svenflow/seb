/**
 * memory-search — SQLite FTS5 HTTP daemon for seb
 * Listens on http://localhost:7890
 *
 * Routes:
 *   POST /memory/save    { type, content, contact? }
 *   GET  /memory/load    ?contact=platform:sender_id
 *   GET  /memory/search  ?q=query&contact=...&limit=5
 *   GET  /memory/stats
 */

import { Database } from "bun:sqlite";
import { mkdir } from "node:fs/promises";

const PORT = parseInt(process.env.MEMORY_PORT ?? "7890");
const DATA_DIR = new URL("./data", import.meta.url).pathname;

await mkdir(DATA_DIR, { recursive: true });

const db = new Database(`${DATA_DIR}/memory.db`);

db.run("PRAGMA journal_mode=WAL");

db.run(`
  CREATE TABLE IF NOT EXISTS memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    type      TEXT NOT NULL,
    content   TEXT NOT NULL,
    contact   TEXT,
    created   INTEGER NOT NULL DEFAULT (unixepoch())
  )
`);

db.run(`
  CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    contact,
    type,
    content='memories',
    content_rowid='id'
  )
`);

// Keep FTS in sync
db.run(`
  CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, contact, type)
    VALUES (new.id, new.content, COALESCE(new.contact, ''), new.type);
  END
`);

db.run(`
  CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, contact, type)
    VALUES ('delete', old.id, old.content, COALESCE(old.contact, ''), old.type);
  END
`);

type Memory = {
  id: number;
  type: string;
  content: string;
  contact: string | null;
  created: number;
};

const server = Bun.serve({
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname;

    try {
      if (path === "/memory/save" && req.method === "POST") {
        const body = (await req.json()) as {
          type: string;
          content: string;
          contact?: string;
        };
        if (!body.type || !body.content) {
          return json({ error: "type and content required" }, 400);
        }
        const stmt = db.prepare(
          "INSERT INTO memories (type, content, contact) VALUES (?, ?, ?)"
        );
        const result = stmt.run(body.type, body.content, body.contact ?? null);
        return json({ id: result.lastInsertRowid, ok: true });
      }

      if (path === "/memory/load" && req.method === "GET") {
        const contact = url.searchParams.get("contact");
        let rows: Memory[];
        if (contact) {
          rows = db
            .prepare("SELECT * FROM memories WHERE contact = ? ORDER BY created DESC")
            .all(contact) as Memory[];
        } else {
          rows = db
            .prepare("SELECT * FROM memories ORDER BY created DESC")
            .all() as Memory[];
        }
        return json({ memories: rows });
      }

      if (path === "/memory/search" && req.method === "GET") {
        const q = url.searchParams.get("q");
        const contact = url.searchParams.get("contact");
        const limit = parseInt(url.searchParams.get("limit") ?? "10");
        if (!q) return json({ error: "q required" }, 400);

        let rows: Memory[];
        if (contact) {
          rows = db
            .prepare(
              `SELECT m.* FROM memories_fts f
               JOIN memories m ON m.id = f.rowid
               WHERE memories_fts MATCH ? AND m.contact = ?
               ORDER BY rank LIMIT ?`
            )
            .all(q, contact, limit) as Memory[];
        } else {
          rows = db
            .prepare(
              `SELECT m.* FROM memories_fts f
               JOIN memories m ON m.id = f.rowid
               WHERE memories_fts MATCH ?
               ORDER BY rank LIMIT ?`
            )
            .all(q, limit) as Memory[];
        }
        return json({ memories: rows, query: q });
      }

      if (path === "/memory/stats" && req.method === "GET") {
        const total = (db.prepare("SELECT COUNT(*) as n FROM memories").get() as { n: number }).n;
        const byType = db
          .prepare("SELECT type, COUNT(*) as n FROM memories GROUP BY type")
          .all() as { type: string; n: number }[];
        return json({ total, byType });
      }

      return json({ error: "not found" }, 404);
    } catch (err) {
      console.error(err);
      return json({ error: String(err) }, 500);
    }
  },
});

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

console.log(`memory-search listening on http://localhost:${PORT}`);
