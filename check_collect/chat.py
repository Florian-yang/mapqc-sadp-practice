"""Issue-bound private chats and two role-based work groups."""
import re
import secrets
import time
from .auth import AuthError
from .events import queue_event, queue_role


GROUPS = {
    'collectors': ('collector', '采集者交流群'),
    'reviewers': ('reviewer', '核验者交流群'),
}


class Chat:
    def __init__(self, collection):
        self.collection = collection
        self.store = collection.store
        with self.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS chat_rooms(
              id TEXT PRIMARY KEY, issue_id TEXT NOT NULL UNIQUE REFERENCES issues(id),
              created_ms REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_messages(
              sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              id TEXT NOT NULL UNIQUE, room_id TEXT NOT NULL,
              sender TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL,
              request_id TEXT NOT NULL, created_ms REAL NOT NULL,
              UNIQUE(sender,request_id));
            CREATE INDEX IF NOT EXISTS chat_room_messages ON chat_messages(room_id,sequence);
            ''')

    @staticmethod
    def _roles(db, uid):
        if not isinstance(uid, str) or not uid:
            raise AuthError('请先登录并加入采集者或核验者')
        roles = {r['role'] for r in db.execute('SELECT role FROM user_roles WHERE user_id=?', (uid,))}
        if not roles.intersection(('collector', 'reviewer')):
            raise AuthError('需要采集者或核验者权限')
        return roles

    @staticmethod
    def _issue(db, issue_id):
        # Participants come only from the original error and the owning POI.
        if not isinstance(issue_id, str) or not re.fullmatch(r'[a-f0-9]{32}', issue_id):
            raise AuthError('讨论不存在或无权访问')
        row = db.execute('''SELECT i.id AS issue_id,i.poi_id,i.reviewer,i.description,
                            p.owner,p.name AS poi_name
                            FROM issues i JOIN pois p ON p.id=i.poi_id WHERE i.id=?''', (issue_id,)).fetchone()
        if not row:
            raise AuthError('讨论不存在或无权访问')
        return dict(row)

    @staticmethod
    def _participant(uid, roles, issue):
        return ((uid == issue['owner'] and 'collector' in roles)
                or (uid == issue['reviewer'] and 'reviewer' in roles))

    def _room(self, db, uid, room_id, roles=None):
        roles = self._roles(db, uid) if roles is None else roles
        if not isinstance(room_id, str):
            raise AuthError('讨论不存在或无权访问')
        if room_id in GROUPS:
            role, title = GROUPS[room_id]
            if role not in roles:
                raise AuthError('讨论不存在或无权访问')
            return {'id': room_id, 'kind': 'group', 'role': role, 'title': title, 'issue_id': None}
        if not re.fullmatch(r'[a-f0-9]{32}', room_id):
            raise AuthError('讨论不存在或无权访问')
        row = db.execute('SELECT issue_id FROM chat_rooms WHERE id=?', (room_id,)).fetchone()
        if not row:
            raise AuthError('讨论不存在或无权访问')
        issue = self._issue(db, row['issue_id'])
        if not self._participant(uid, roles, issue):
            raise AuthError('讨论不存在或无权访问')
        return {'id': room_id, 'kind': 'private', 'title': issue['poi_name'] + '：错误讨论', **issue}

    def list_rooms(self, uid):
        with self.store.connect() as db:
            db.execute('BEGIN')
            roles = self._roles(db, uid)
            rooms = [self._room(db, uid, room_id, roles) for room_id, (role, _) in GROUPS.items() if role in roles]
            for row in db.execute('SELECT id,issue_id FROM chat_rooms ORDER BY created_ms DESC,id'):
                issue = self._issue(db, row['issue_id'])
                if self._participant(uid, roles, issue):
                    rooms.append({'id': row['id'], 'kind': 'private',
                                  'title': issue['poi_name'] + '：错误讨论', **issue})
            return rooms

    def open_private(self, uid, issue_id):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            roles = self._roles(db, uid)
            issue = self._issue(db, issue_id)
            if not self._participant(uid, roles, issue):
                raise AuthError('讨论不存在或无权访问')
            existing = db.execute('SELECT id FROM chat_rooms WHERE issue_id=?', (issue_id,)).fetchone()
            if existing:
                return existing['id']
            room_id = secrets.token_hex(16)
            db.execute('INSERT INTO chat_rooms VALUES(?,?,?)', (room_id, issue_id, time.time() * 1000))
            return room_id

    def room(self, uid, room_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            return self._room(db, uid, room_id)

    def messages(self, uid, room_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._room(db, uid, room_id)
            return [dict(r) for r in db.execute('''SELECT id,room_id,sender,text,request_id,created_ms
                FROM (SELECT * FROM chat_messages WHERE room_id=? ORDER BY sequence DESC LIMIT 100)
                ORDER BY sequence''', (room_id,))]

    def send(self, uid, room_id, text, request_id):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
            raise AuthError('请填写1—1000字的消息')
        text = text.strip()
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', request_id):
            raise AuthError('消息请求标识无效，请刷新后重试')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            room = self._room(db, uid, room_id)
            previous = db.execute('SELECT id,room_id,text FROM chat_messages WHERE sender=? AND request_id=?',
                                  (uid, request_id)).fetchone()
            if previous:
                if previous['room_id'] != room_id or previous['text'] != text:
                    raise AuthError('同一消息请求标识不能用于不同内容或讨论')
                return previous['id']
            message_id = secrets.token_hex(16)
            db.execute('INSERT INTO chat_messages(id,room_id,sender,text,request_id,created_ms) VALUES(?,?,?,?,?,?)',
                       (message_id, room_id, uid, text, request_id, time.time() * 1000))
            payload = {'room_id': room_id, 'message_id': message_id}
            if room['kind'] == 'group':
                queue_role(db, room['role'], 'chat.message', payload, exclude=(uid,))
            else:
                recipient = room['reviewer'] if uid == room['owner'] else room['owner']
                queue_event(db, 'chat.message', recipient, payload)
            return message_id
