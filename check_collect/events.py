"""Business events are saved in the caller's transaction."""
import json
import secrets
import time


def ensure_schema(db):
    if not db.in_transaction: db.execute('BEGIN IMMEDIATE')
    db.execute('''CREATE TABLE IF NOT EXISTS event_outbox(
      id TEXT PRIMARY KEY, event_type TEXT NOT NULL, recipient TEXT NOT NULL REFERENCES users(id),
      payload TEXT NOT NULL, created_ms REAL NOT NULL, delivered_ms REAL)''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(event_outbox)')}
    for name,kind in [('published_ms','REAL'),('publish_attempts','INTEGER NOT NULL DEFAULT 0'),('last_error','TEXT')]:
        if name not in columns: db.execute(f'ALTER TABLE event_outbox ADD COLUMN {name} {kind}')


def queue_event(db,kind,recipient,payload):
    event_id=secrets.token_hex(16)
    db.execute('INSERT INTO event_outbox(id,event_type,recipient,payload,created_ms) VALUES(?,?,?,?,?)',
               (event_id,kind,recipient,json.dumps(payload,ensure_ascii=False),time.time()*1000))
    return event_id


def queue_role(db,role,kind,payload,exclude=()):
    recipients=[r[0] for r in db.execute('SELECT user_id FROM user_roles WHERE role=? ORDER BY user_id',(role,)) if r[0] not in exclude]
    return [queue_event(db,kind,uid,payload) for uid in recipients]
