# -*- coding: utf-8 -*-
import sqlite3
import os
import time
import threading
from contextlib import contextmanager
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chatroom.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


@contextmanager
def db_conn():
    """Context manager: ensures the connection is always closed, even on exceptions."""
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


_db_initialized = False
_db_init_lock = threading.Lock()  # prevents concurrent init_db execution


def _safe_add_column(cursor, table, col_def):
    """Add a column only if it does not already exist (idempotent)."""
    try:
        cursor.execute(f'ALTER TABLE {table} ADD COLUMN {col_def}')
    except sqlite3.OperationalError:
        pass


def init_db():
    global _db_initialized
    if _db_initialized:
        return
    with _db_init_lock:
        if _db_initialized:   # re-check after acquiring lock (double-checked locking)
            return
        _init_db_locked()


def _init_db_locked():
    global _db_initialized
    with db_conn() as conn:
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            is_banned INTEGER NOT NULL DEFAULT 0
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            is_group INTEGER NOT NULL DEFAULT 0,
            is_self_chat INTEGER NOT NULL DEFAULT 0,
            avatar_url TEXT,
            announcement TEXT NOT NULL DEFAULT '',
            created_by INTEGER,
            created_at REAL NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS conversation_members (
            conversation_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at REAL NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')

        # msg_type: 'text' | 'image' | 'audio' | 'file'
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            media_url TEXT,
            is_revoked INTEGER NOT NULL DEFAULT 0,
            edited_at REAL,
            original_message_id INTEGER,
            timestamp REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id),
            FOREIGN KEY (sender_id) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS favorite_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(user_id, message_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (message_id) REFERENCES messages(id)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorite_messages(user_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorites_user_created ON favorite_messages(user_id, created_at DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorites_msg ON favorite_messages(message_id)')

        c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            avatar_emoji TEXT NOT NULL DEFAULT '😊',
            avatar_url TEXT,
            bio TEXT NOT NULL DEFAULT '',
            theme TEXT NOT NULL DEFAULT 'light',
            font_size TEXT NOT NULL DEFAULT 'medium',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS pinned_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            pinned_by INTEGER NOT NULL,
            pinned_at REAL NOT NULL,
            UNIQUE(conversation_id, message_id),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id),
            FOREIGN KEY (message_id) REFERENCES messages(id),
            FOREIGN KEY (pinned_by) REFERENCES users(id)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pinned_conv ON pinned_messages(conversation_id, pinned_at DESC)')

        # friends: normalized – always store min(a,b) as requester_id, max(a,b) as addressee_id.
        # initiated_by tracks who actually sent the request.
        # The single UNIQUE(requester_id, addressee_id) prevents concurrent A→B and B→A inserts.
        c.execute('''CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            addressee_id INTEGER NOT NULL,
            initiated_by INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            UNIQUE(requester_id, addressee_id),
            FOREIGN KEY (requester_id) REFERENCES users(id),
            FOREIGN KEY (addressee_id) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )''')

        # user_files: records every uploaded file so we can track per-user storage usage
        c.execute('''CREATE TABLE IF NOT EXISTS user_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_path TEXT NOT NULL UNIQUE,
            file_size INTEGER NOT NULL,
            uploaded_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_user_files_uid ON user_files(user_id)')

        # ── Default settings ────────────────────────────────────────────────
        for key, value in [
            ('registration_enabled', '1'),
            ('max_message_length', '2000'),
            ('system_name', '聊天室'),
            ('allow_friend_requests', '1'),
            ('default_storage_quota_mb', '10240'),   # 10 GB
            ('db_version', '0'),
        ]:
            c.execute('INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)', (key, value))

        # ── Idempotent column migrations ────────────────────────────────────
        _safe_add_column(c, 'users',               'is_banned INTEGER NOT NULL DEFAULT 0')
        _safe_add_column(c, 'conversation_members', "role TEXT NOT NULL DEFAULT 'member'")
        _safe_add_column(c, 'conversations',        'is_self_chat INTEGER NOT NULL DEFAULT 0')
        _safe_add_column(c, 'messages',             "msg_type TEXT NOT NULL DEFAULT 'text'")
        _safe_add_column(c, 'messages',             'media_url TEXT')
        _safe_add_column(c, 'messages',             'is_revoked INTEGER NOT NULL DEFAULT 0')
        _safe_add_column(c, 'messages',             'edited_at REAL')
        _safe_add_column(c, 'messages',             'original_message_id INTEGER')
        _safe_add_column(c, 'user_profiles',        'avatar_url TEXT')
        _safe_add_column(c, 'user_profiles',        'storage_quota_mb INTEGER')  # NULL = use global default
        _safe_add_column(c, 'friends',              'initiated_by INTEGER')
        _safe_add_column(c, 'conversations',        'avatar_url TEXT')
        _safe_add_column(c, 'conversations',        "announcement TEXT NOT NULL DEFAULT ''")

        # ── Data migrations (run once per version) ──────────────────────────
        db_version = c.execute(
            "SELECT value FROM system_settings WHERE key = 'db_version'"
        ).fetchone()
        ver = int(db_version['value']) if db_version else 0

        if ver < 1:
            # Set group creators as admin
            c.execute('''UPDATE conversation_members SET role = 'admin'
                         WHERE user_id = (
                             SELECT created_by FROM conversations
                             WHERE id = conversation_id
                               AND is_group = 1
                               AND created_by IS NOT NULL
                         )''')

        if ver < 2:
            # Normalise legacy friends rows (ensure requester_id < addressee_id)
            c.execute('''UPDATE friends
                         SET requester_id = addressee_id,
                             addressee_id  = requester_id
                         WHERE requester_id > addressee_id''')
            # Back-fill initiated_by for rows that don't have it yet
            c.execute('UPDATE friends SET initiated_by = requester_id WHERE initiated_by IS NULL')

        if ver < 2:
            c.execute("UPDATE system_settings SET value = '2' WHERE key = 'db_version'")

        if ver < 3:
            c.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('default_storage_quota_mb', '10240')")
            c.execute("UPDATE system_settings SET value = '3' WHERE key = 'db_version'")

        if ver < 4:
            c.execute("UPDATE system_settings SET value = '4' WHERE key = 'db_version'")

        if ver < 5:
            c.execute("UPDATE conversations SET announcement = '' WHERE announcement IS NULL")
            c.execute("UPDATE system_settings SET value = '5' WHERE key = 'db_version'")

        conn.commit()
    _db_initialized = True


_ph = PasswordHasher(type=Type.ID)  # argon2id


def create_user(username, password):
    with db_conn() as conn:
        try:
            password_hash = _ph.hash(password)
            conn.execute(
                'INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                (username, password_hash, time.time())
            )
            conn.commit()
            return True, '注册成功'
        except sqlite3.IntegrityError:
            return False, '用户名已存在'


def verify_user(username, password):
    with db_conn() as conn:
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        user = dict(user) if user else None
    if not user:
        return None
    try:
        _ph.verify(user['password_hash'], password)
        return user
    except VerifyMismatchError:
        return None


def get_user_by_id(user_id):
    with db_conn() as conn:
        user = conn.execute('SELECT id, username FROM users WHERE id = ?', (user_id,)).fetchone()
    return dict(user) if user else None


def is_user_banned(user_id):
    with db_conn() as conn:
        row = conn.execute('SELECT is_banned FROM users WHERE id = ?', (user_id,)).fetchone()
    return bool(row and row['is_banned'])


def search_users(query, exclude_id=None):
    escaped_query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    like_query = f'%{escaped_query}%'
    with db_conn() as conn:
        if exclude_id:
            users = conn.execute(
                'SELECT id, username FROM users WHERE username LIKE ? ESCAPE "\\" AND id != ? LIMIT 20',
                (like_query, exclude_id)
            ).fetchall()
        else:
            users = conn.execute(
                'SELECT id, username FROM users WHERE username LIKE ? ESCAPE "\\" LIMIT 20',
                (like_query,)
            ).fetchall()
    return [dict(u) for u in users]


def search_users_for_viewer(viewer_id, query):
    escaped_query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    like_query = f'%{escaped_query}%'
    with db_conn() as conn:
        users = conn.execute(
            '''SELECT u.id, u.username, p.avatar_url, p.avatar_emoji
               FROM users u
               LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE u.username LIKE ? ESCAPE "\\"
               ORDER BY CASE WHEN u.id = ? THEN 0 ELSE 1 END, u.username
               LIMIT 20''',
            (like_query, viewer_id)
        ).fetchall()
    result = []
    for u in users:
        uid = u['id']
        relation = 'none'
        can_chat = False
        if uid == viewer_id:
            relation = 'self'
            can_chat = True
        else:
            norm_a, norm_b = _norm_pair(viewer_id, uid)
            with db_conn() as conn:
                rel = conn.execute(
                    'SELECT status, initiated_by FROM friends WHERE requester_id = ? AND addressee_id = ?',
                    (norm_a, norm_b)
                ).fetchone()
            if rel:
                if rel['status'] == 'accepted':
                    relation = 'friend'
                    can_chat = True
                elif rel['status'] == 'pending':
                    relation = 'pending_out' if rel['initiated_by'] == viewer_id else 'pending_in'
        result.append({
            'id': uid,
            'username': u['username'],
            'avatar_url': u['avatar_url'],
            'avatar_emoji': u['avatar_emoji'],
            'relation': relation,
            'can_chat': can_chat
        })
    return result


def can_start_private_chat(user1_id, user2_id):
    if user1_id == user2_id:
        return True
    norm_a, norm_b = _norm_pair(user1_id, user2_id)
    with db_conn() as conn:
        rel = conn.execute(
            'SELECT status FROM friends WHERE requester_id = ? AND addressee_id = ?',
            (norm_a, norm_b)
        ).fetchone()
    return bool(rel and rel['status'] == 'accepted')


def create_self_conversation(user_id):
    """Return (or create) the user's private self-chat / notes conversation."""
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            '''SELECT c.id FROM conversations c
               JOIN conversation_members cm ON c.id = cm.conversation_id
               WHERE c.is_self_chat = 1 AND cm.user_id = ?''',
            (user_id,)
        ).fetchone()
        if existing:
            conn.execute('ROLLBACK')
            return existing['id']
        now = time.time()
        c = conn.cursor()
        c.execute(
            'INSERT INTO conversations (name, is_group, is_self_chat, created_by, created_at) VALUES (?, 0, 1, ?, ?)',
            ('我的备忘录', user_id, now)
        )
        conv_id = c.lastrowid
        c.execute(
            'INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
            (conv_id, user_id, now)
        )
        conn.commit()
    return conv_id


def create_private_conversation(user1_id, user2_id):
    # Guard: creating a conversation with yourself is handled by create_self_conversation
    if user1_id == user2_id:
        return create_self_conversation(user1_id)
    with db_conn() as conn:
        # BEGIN IMMEDIATE acquires a write lock before the SELECT so no other writer can
        # slip a concurrent INSERT in between the check and the insert (TOCTOU fix).
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute(
            '''SELECT c.id FROM conversations c
               JOIN conversation_members cm1 ON c.id = cm1.conversation_id AND cm1.user_id = ?
               JOIN conversation_members cm2 ON c.id = cm2.conversation_id AND cm2.user_id = ?
               WHERE c.is_group = 0 AND c.is_self_chat = 0''',
            (user1_id, user2_id)
        ).fetchone()
        if existing:
            conn.execute('ROLLBACK')
            return existing['id']
        now = time.time()
        c = conn.cursor()
        c.execute(
            'INSERT INTO conversations (is_group, is_self_chat, created_by, created_at) VALUES (0, 0, ?, ?)',
            (user1_id, now)
        )
        conv_id = c.lastrowid
        c.execute(
            'INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
            (conv_id, user1_id, now)
        )
        c.execute(
            'INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
            (conv_id, user2_id, now)
        )
        conn.commit()
    return conv_id


def create_group_conversation(name, creator_id, member_ids):
    with db_conn() as conn:
        now = time.time()
        c = conn.cursor()
        c.execute(
            'INSERT INTO conversations (name, is_group, is_self_chat, created_by, created_at) VALUES (?, 1, 0, ?, ?)',
            (name, creator_id, now)
        )
        conv_id = c.lastrowid
        all_members = set(member_ids) | {creator_id}
        for uid in all_members:
            role = 'admin' if uid == creator_id else 'member'
            c.execute(
                'INSERT INTO conversation_members (conversation_id, user_id, joined_at, role) VALUES (?, ?, ?, ?)',
                (conv_id, uid, now, role)
            )
        conn.commit()
    return conv_id


def get_user_conversations(user_id):
    with db_conn() as conn:
        convs = conn.execute(
            '''SELECT c.id, c.name, c.is_group, c.is_self_chat, c.avatar_url, c.announcement, c.created_at
               FROM conversations c
               JOIN conversation_members cm ON c.id = cm.conversation_id
               WHERE cm.user_id = ?
               ORDER BY c.created_at DESC''',
            (user_id,)
        ).fetchall()
        result = []
        for conv in convs:
            conv_dict = dict(conv)
            members = conn.execute(
                     '''SELECT u.id, u.username, p.avatar_url, p.avatar_emoji FROM users u
                   JOIN conversation_members cm ON u.id = cm.user_id
                         LEFT JOIN user_profiles p ON p.user_id = u.id
                   WHERE cm.conversation_id = ?''',
                (conv['id'],)
            ).fetchall()
            conv_dict['members'] = [dict(m) for m in members]
            if conv_dict.get('is_self_chat'):
                conv_dict['display_name'] = '我的备忘录'
            elif not conv_dict['is_group']:
                other = [m for m in conv_dict['members'] if m['id'] != user_id]
                conv_dict['display_name'] = other[0]['username'] if other else '未知'
            else:
                conv_dict['display_name'] = conv_dict['name'] or '群聊'
            last_msg = conn.execute(
                '''SELECT m.id, m.content, m.msg_type, m.timestamp, m.is_revoked, u.username as sender_name
                   FROM messages m JOIN users u ON m.sender_id = u.id
                   WHERE m.conversation_id = ?
                   ORDER BY m.timestamp DESC LIMIT 1''',
                (conv['id'],)
            ).fetchone()
            conv_dict['last_message'] = dict(last_msg) if last_msg else None
            result.append(conv_dict)
    result.sort(
        key=lambda x: x['last_message']['timestamp'] if x['last_message'] else x['created_at'],
        reverse=True
    )
    return result


def save_message(conversation_id, sender_id, content, msg_type='text', media_url=None, original_message_id=None):
    with db_conn() as conn:
        now = time.time()
        c = conn.cursor()
        c.execute(
            '''INSERT INTO messages
               (conversation_id, sender_id, content, msg_type, media_url, original_message_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (conversation_id, sender_id, content, msg_type, media_url, original_message_id, now)
        )
        msg_id = c.lastrowid
        conn.commit()
    return {
        'id': msg_id, 'conversation_id': conversation_id, 'sender_id': sender_id,
        'content': content, 'msg_type': msg_type, 'media_url': media_url,
        'is_revoked': 0, 'edited_at': None, 'original_message_id': original_message_id,
        'timestamp': now
    }


def get_messages(conversation_id, limit=50, before=None):
    with db_conn() as conn:
        base_query = '''
            SELECT m.id, m.conversation_id, m.sender_id, m.content,
                   m.msg_type, m.media_url, m.is_revoked, m.edited_at,
                   m.original_message_id, m.timestamp, u.username as sender_name,
                   om.content AS original_content, ou.username AS original_sender_name,
                   om.msg_type AS original_msg_type
            FROM messages m
            JOIN users u ON m.sender_id = u.id
            LEFT JOIN messages om ON m.original_message_id = om.id
            LEFT JOIN users ou ON om.sender_id = ou.id
            WHERE m.conversation_id = ?
        '''
        if before:
            msgs = conn.execute(
                base_query + ' AND m.timestamp < ? ORDER BY m.timestamp DESC LIMIT ?',
                (conversation_id, before, limit)
            ).fetchall()
        else:
            msgs = conn.execute(
                base_query + ' ORDER BY m.timestamp DESC LIMIT ?',
                (conversation_id, limit)
            ).fetchall()
    return [dict(m) for m in reversed(msgs)]


def get_message_by_id(message_id):
    with db_conn() as conn:
        msg = conn.execute(
            '''SELECT m.id, m.conversation_id, m.sender_id, m.content, m.msg_type,
                      m.media_url, m.is_revoked, m.edited_at, m.original_message_id, m.timestamp,
                      u.username as sender_name
               FROM messages m JOIN users u ON m.sender_id = u.id
               WHERE m.id = ?''',
            (message_id,)
        ).fetchone()
    return dict(msg) if msg else None


def revoke_message(message_id, user_id):
    with db_conn() as conn:
        msg = conn.execute(
            'SELECT id, sender_id, is_revoked FROM messages WHERE id = ?',
            (message_id,)
        ).fetchone()
        if not msg:
            return False, '消息不存在'
        if msg['sender_id'] != user_id:
            return False, '只能撤回自己的消息'
        if msg['is_revoked']:
            return False, '消息已撤回'
        conn.execute(
            "UPDATE messages SET is_revoked = 1, content = '消息已撤回', media_url = NULL WHERE id = ?",
            (message_id,)
        )
        conn.execute('DELETE FROM favorite_messages WHERE message_id = ?', (message_id,))
        conn.commit()
    return True, '已撤回'


def edit_message(message_id, user_id, content):
    with db_conn() as conn:
        msg = conn.execute(
            'SELECT id, sender_id, is_revoked, msg_type FROM messages WHERE id = ?',
            (message_id,)
        ).fetchone()
        if not msg:
            return False, '消息不存在'
        if msg['sender_id'] != user_id:
            return False, '只能编辑自己的消息'
        if msg['is_revoked']:
            return False, '消息已撤回'
        if msg['msg_type'] != 'text':
            return False, '仅文本消息可编辑'
        conn.execute(
            'UPDATE messages SET content = ?, edited_at = ? WHERE id = ?',
            (content, time.time(), message_id)
        )
        conn.commit()
    return True, '已编辑'


def toggle_favorite_message(user_id, message_id):
    with db_conn() as conn:
        msg = conn.execute(
            'SELECT is_revoked FROM messages WHERE id = ?',
            (message_id,)
        ).fetchone()
        if not msg or msg['is_revoked']:
            return False, False
        conn.execute('BEGIN IMMEDIATE')
        deleted = conn.execute(
            'DELETE FROM favorite_messages WHERE user_id = ? AND message_id = ?',
            (user_id, message_id)
        ).rowcount
        if deleted:
            conn.commit()
            return True, False
        conn.execute(
            'INSERT OR IGNORE INTO favorite_messages (user_id, message_id, created_at) VALUES (?, ?, ?)',
            (user_id, message_id, time.time())
        )
        conn.commit()
        return True, True


def get_favorite_messages(user_id, limit=30, before=None):
    safe_limit = max(1, min(int(limit), 100))
    with db_conn() as conn:
        params = [user_id]
        where_clause = 'WHERE fm.user_id = ?'
        if before is not None:
            where_clause += ' AND fm.created_at < ?'
            params.append(float(before))
        params.append(safe_limit + 1)
        rows = conn.execute(
            f'''SELECT m.id, m.conversation_id, m.sender_id, m.content, m.msg_type,
                      m.media_url, m.is_revoked, m.edited_at, m.original_message_id, m.timestamp,
                      u.username as sender_name, fm.created_at as favorited_at,
                      c.name as conversation_name, c.is_group, c.is_self_chat
               FROM favorite_messages fm
               JOIN messages m ON fm.message_id = m.id
               JOIN users u ON m.sender_id = u.id
               JOIN conversations c ON c.id = m.conversation_id
               {where_clause}
               ORDER BY fm.created_at DESC
               LIMIT ?''',
            tuple(params)
        ).fetchall()
    items = [dict(r) for r in rows[:safe_limit]]
    has_more = len(rows) > safe_limit
    next_before = items[-1]['favorited_at'] if has_more and items else None
    return {
        'items': items,
        'has_more': has_more,
        'next_before': next_before
    }


def get_conversation_members(conversation_id):
    with db_conn() as conn:
        members = conn.execute(
            '''SELECT u.id, u.username FROM users u
               JOIN conversation_members cm ON u.id = cm.user_id
               WHERE cm.conversation_id = ?''',
            (conversation_id,)
        ).fetchall()
    return [dict(m) for m in members]


def is_member(conversation_id, user_id):
    with db_conn() as conn:
        row = conn.execute(
            'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conversation_id, user_id)
        ).fetchone()
    return row is not None


# ===== Storage functions =====

def verify_file_owner(user_id: int, file_path: str) -> bool:
    """Check that the file_path was uploaded by this user."""
    with db_conn() as conn:
        row = conn.execute(
            'SELECT 1 FROM user_files WHERE user_id = ? AND file_path = ?',
            (user_id, file_path)
        ).fetchone()
    return row is not None


def record_file_upload(user_id: int, file_path: str, file_size: int) -> bool:
    """Record a file upload atomically with quota check. Returns False if quota exceeded."""
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute(
            'SELECT COALESCE(SUM(file_size), 0) as used FROM user_files WHERE user_id = ?',
            (user_id,)
        ).fetchone()
        used = row['used'] if row else 0

        quota_row = conn.execute(
            'SELECT storage_quota_mb FROM user_profiles WHERE user_id = ?', (user_id,)
        ).fetchone()
        if quota_row and quota_row['storage_quota_mb'] is not None:
            quota_mb = quota_row['storage_quota_mb']
        else:
            setting = conn.execute(
                "SELECT value FROM system_settings WHERE key = 'default_storage_quota_mb'"
            ).fetchone()
            quota_mb = int(setting['value']) if setting else 10240
        quota_bytes = quota_mb * 1024 * 1024

        if used + file_size > quota_bytes:
            conn.execute('ROLLBACK')
            return False

        conn.execute(
            'INSERT OR IGNORE INTO user_files (user_id, file_path, file_size, uploaded_at) VALUES (?, ?, ?, ?)',
            (user_id, file_path, file_size, time.time())
        )
        conn.commit()
    return True


def get_user_storage_info(user_id: int) -> dict:
    """Return used bytes, quota bytes, and percentage for a user."""
    with db_conn() as conn:
        row = conn.execute(
            'SELECT COALESCE(SUM(file_size), 0) as used FROM user_files WHERE user_id = ?',
            (user_id,)
        ).fetchone()
        used = row['used'] if row else 0

        # Per-user override > global default
        quota_row = conn.execute(
            'SELECT storage_quota_mb FROM user_profiles WHERE user_id = ?', (user_id,)
        ).fetchone()
        if quota_row and quota_row['storage_quota_mb'] is not None:
            quota_mb = quota_row['storage_quota_mb']
        else:
            setting = conn.execute(
                "SELECT value FROM system_settings WHERE key = 'default_storage_quota_mb'"
            ).fetchone()
            quota_mb = int(setting['value']) if setting else 10240
    quota = quota_mb * 1024 * 1024  # bytes
    return {
        'used_bytes': used,
        'quota_bytes': quota,
        'quota_mb': quota_mb,
        'used_mb': round(used / (1024 * 1024), 2),
        'percent': round(used / quota * 100, 1) if quota else 0,
    }


def set_user_quota(user_id: int, quota_mb):
    """Admin: set per-user quota override. Pass None to reset to global default."""
    with db_conn() as conn:
        conn.execute(
            '''INSERT OR IGNORE INTO user_profiles
               (user_id, avatar_emoji, bio, theme, font_size)
               VALUES (?, '\U0001f60a', '', 'light', 'medium')''',
            (user_id,)
        )
        conn.execute(
            'UPDATE user_profiles SET storage_quota_mb = ? WHERE user_id = ?',
            (quota_mb, user_id)
        )
        conn.commit()


# ===== Admin functions =====

def get_all_users():
    with db_conn() as conn:
        users = conn.execute(
            '''SELECT u.id, u.username, u.created_at, u.is_banned,
                      COALESCE(p.storage_quota_mb, NULL) as storage_quota_mb,
                      COALESCE((
                          SELECT SUM(f.file_size) FROM user_files f WHERE f.user_id = u.id
                      ), 0) as storage_used_bytes
               FROM users u
               LEFT JOIN user_profiles p ON p.user_id = u.id
               ORDER BY u.id'''
        ).fetchall()
    return [dict(u) for u in users]


def delete_user(user_id):
    with db_conn() as conn:
        c = conn.cursor()
        # Remove file upload records
        c.execute('DELETE FROM user_files WHERE user_id = ?', (user_id,))
        # ── Transfer group ownership before removing membership ────────────
        # For every group this user created, promote the longest-standing admin
        # (or any remaining member) as the new owner so the group stays manageable.
        owned_groups = c.execute(
            'SELECT id FROM conversations WHERE created_by = ? AND is_group = 1', (user_id,)
        ).fetchall()
        for grp in owned_groups:
            gid = grp['id']
            # Try to find another admin first, otherwise any other member
            successor = c.execute(
                '''SELECT user_id FROM conversation_members
                   WHERE conversation_id = ? AND user_id != ?
                   ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined_at
                   LIMIT 1''',
                (gid, user_id)
            ).fetchone()
            if successor:
                new_owner = successor['user_id']
                c.execute('UPDATE conversations SET created_by = ? WHERE id = ?', (new_owner, gid))
                c.execute(
                    "UPDATE conversation_members SET role = 'admin' WHERE conversation_id = ? AND user_id = ?",
                    (gid, new_owner)
                )
            else:
                # No other members — set NULL (group will be cleaned up below)
                c.execute('UPDATE conversations SET created_by = NULL WHERE id = ?', (gid,))
        # ── Remove user records ────────────────────────────────────────────
        c.execute('DELETE FROM conversation_members WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM messages WHERE sender_id = ?', (user_id,))
        c.execute('''DELETE FROM messages WHERE conversation_id NOT IN (
            SELECT DISTINCT conversation_id FROM conversation_members)''')
        c.execute('''DELETE FROM conversations WHERE id NOT IN (
            SELECT DISTINCT conversation_id FROM conversation_members)''')
        c.execute('DELETE FROM friends WHERE requester_id = ? OR addressee_id = ?', (user_id, user_id))
        c.execute('DELETE FROM user_profiles WHERE user_id = ?', (user_id,))
        c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()


def get_all_groups():
    with db_conn() as conn:
        groups = conn.execute(
            '''SELECT c.id, c.name, c.created_at, u.username as creator_name
               FROM conversations c
               LEFT JOIN users u ON c.created_by = u.id
               WHERE c.is_group = 1
               ORDER BY c.id'''
        ).fetchall()
        result = []
        for g in groups:
            gd = dict(g)
            members = conn.execute(
                '''SELECT u.id, u.username FROM users u
                   JOIN conversation_members cm ON u.id = cm.user_id
                   WHERE cm.conversation_id = ?''',
                (g['id'],)
            ).fetchall()
            gd['members'] = [dict(m) for m in members]
            gd['member_count'] = len(gd['members'])
            result.append(gd)
    return result


def rename_group(conv_id, new_name):
    with db_conn() as conn:
        conn.execute('UPDATE conversations SET name = ? WHERE id = ? AND is_group = 1', (new_name, conv_id))
        conn.commit()


def delete_group(conv_id):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
        c.execute('DELETE FROM conversation_members WHERE conversation_id = ?', (conv_id,))
        c.execute('DELETE FROM conversations WHERE id = ? AND is_group = 1', (conv_id,))
        conn.commit()


def ban_user(user_id, ban=True):
    with db_conn() as conn:
        conn.execute('UPDATE users SET is_banned = ? WHERE id = ?', (1 if ban else 0, user_id))
        conn.commit()
    return True, '已封禁用户' if ban else '已解封用户'


# ===== Profile & Settings =====

def get_profile(user_id):
    with db_conn() as conn:
        user = conn.execute(
            'SELECT id, username, created_at FROM users WHERE id = ?', (user_id,)
        ).fetchone()
        profile = conn.execute(
            'SELECT * FROM user_profiles WHERE user_id = ?', (user_id,)
        ).fetchone()
    if not user:
        return None
    result = dict(user)
    if profile:
        keys = [k for k in ('avatar_emoji', 'avatar_url', 'bio', 'theme', 'font_size') if k in profile.keys()]
        result.update({k: profile[k] for k in keys})
    else:
        result.update({'avatar_emoji': '😊', 'avatar_url': None, 'bio': '', 'theme': 'light', 'font_size': 'medium'})
    return result


def update_profile(user_id, avatar_emoji=None, avatar_url=None, bio=None, theme=None, font_size=None):
    with db_conn() as conn:
        # INSERT OR IGNORE guarantees a row exists with defaults before we UPDATE,
        # eliminating the TOCTOU race between two concurrent first-time profile saves.
        conn.execute(
            '''INSERT OR IGNORE INTO user_profiles
               (user_id, avatar_emoji, avatar_url, bio, theme, font_size)
               VALUES (?, '😊', NULL, '', 'light', 'medium')''',
            (user_id,)
        )
        updates = {}
        if avatar_emoji is not None: updates['avatar_emoji'] = avatar_emoji
        if avatar_url   is not None: updates['avatar_url']   = avatar_url
        if bio          is not None: updates['bio']          = bio
        if theme        is not None: updates['theme']        = theme
        if font_size    is not None: updates['font_size']    = font_size
        if updates:
            set_clause = ', '.join(f'{k} = ?' for k in updates)
            conn.execute(
                f'UPDATE user_profiles SET {set_clause} WHERE user_id = ?',
                list(updates.values()) + [user_id]
            )
        conn.commit()


def change_password(user_id, old_password, new_password):
    with db_conn() as conn:
        user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            return False, '用户不存在'
        try:
            _ph.verify(user['password_hash'], old_password)
        except VerifyMismatchError:
            return False, '原密码错误'
        new_hash = _ph.hash(new_password)
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
        conn.commit()
    return True, '密码已更新'


# ===== Friends / Contacts =====

def _norm_pair(a, b):
    """Canonical ordering: always (min, max) so (A,B) and (B,A) map to the same row."""
    return (min(a, b), max(a, b))


def get_friends(user_id):
    with db_conn() as conn:
        friends = conn.execute(
            '''SELECT u.id, u.username, p.avatar_url, p.avatar_emoji
               FROM friends f
               JOIN users u ON u.id = CASE
                   WHEN f.requester_id = ? THEN f.addressee_id
                   ELSE f.requester_id
               END
               LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE (f.requester_id = ? OR f.addressee_id = ?) AND f.status = 'accepted'
               ORDER BY u.username''',
            (user_id, user_id, user_id)
        ).fetchall()
    return [dict(f) for f in friends]


def get_friend_requests(user_id):
    """Pending requests where this user is the recipient (initiated_by != user_id)."""
    with db_conn() as conn:
        requests = conn.execute(
                '''SELECT f.id, u.id as user_id, u.username, p.avatar_url, p.avatar_emoji, f.created_at
               FROM friends f
               JOIN users u ON f.initiated_by = u.id
                    LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE (f.requester_id = ? OR f.addressee_id = ?)
                 AND f.initiated_by != ?
                 AND f.status = 'pending'
               ORDER BY f.created_at DESC''',
            (user_id, user_id, user_id)
        ).fetchall()
    return [dict(r) for r in requests]


def get_pending_request_count(user_id):
    with db_conn() as conn:
        count = conn.execute(
            '''SELECT COUNT(*) as cnt FROM friends
               WHERE (requester_id = ? OR addressee_id = ?)
                 AND initiated_by != ?
                 AND status = 'pending' ''',
            (user_id, user_id, user_id)
        ).fetchone()
    return count['cnt'] if count else 0


def send_friend_request(requester_id, addressee_id):
    """
    Race-condition-safe: canonical row (min, max) + INSERT with UNIQUE constraint.
    If A->B and B->A arrive concurrently they both map to the same row, so only
    one INSERT can succeed; the other gets IntegrityError.
    If B's row already exists as pending, we auto-accept instead.
    """
    norm_req, norm_addr = _norm_pair(requester_id, addressee_id)
    with db_conn() as conn:
        existing = conn.execute(
            'SELECT * FROM friends WHERE requester_id = ? AND addressee_id = ?',
            (norm_req, norm_addr)
        ).fetchone()
        if existing:
            if existing['status'] == 'accepted':
                return False, '已经是好友'
            if existing['initiated_by'] == requester_id:
                return False, '好友请求已发送，等待对方确认'
            # Other side already sent – auto-accept
            conn.execute(
                "UPDATE friends SET status = 'accepted' WHERE requester_id = ? AND addressee_id = ?",
                (norm_req, norm_addr)
            )
            conn.commit()
            return True, '对方已向你发送好友请求，已自动同意'
        try:
            conn.execute(
                '''INSERT INTO friends
                   (requester_id, addressee_id, initiated_by, status, created_at)
                   VALUES (?, ?, ?, 'pending', ?)''',
                (norm_req, norm_addr, requester_id, time.time())
            )
            conn.commit()
            return True, '好友请求已发送'
        except sqlite3.IntegrityError:
            return False, '请求已存在'


def accept_friend_request(request_id, user_id):
    with db_conn() as conn:
        req = conn.execute(
            '''SELECT * FROM friends WHERE id = ?
               AND (requester_id = ? OR addressee_id = ?)
               AND initiated_by != ?
               AND status = 'pending' ''',
            (request_id, user_id, user_id, user_id)
        ).fetchone()
        if not req:
            return False, '请求不存在'
        conn.execute("UPDATE friends SET status = 'accepted' WHERE id = ?", (request_id,))
        conn.commit()
    return True, '已接受好友请求'


def reject_friend_request(request_id, user_id):
    with db_conn() as conn:
        conn.execute(
            '''DELETE FROM friends WHERE id = ?
               AND (requester_id = ? OR addressee_id = ?)
               AND initiated_by != ?
               AND status = 'pending' ''',
            (request_id, user_id, user_id, user_id)
        )
        conn.commit()
    return True, '已拒绝好友请求'


def remove_friend(user_id, friend_id):
    norm_a, norm_b = _norm_pair(user_id, friend_id)
    with db_conn() as conn:
        conn.execute(
            'DELETE FROM friends WHERE requester_id = ? AND addressee_id = ?',
            (norm_a, norm_b)
        )
        conn.commit()
    return True, '已删除好友'


def get_friend_review(user_id):
    with db_conn() as conn:
        incoming = conn.execute(
                '''SELECT f.id, f.created_at, u.id AS user_id, u.username, p.avatar_url, p.avatar_emoji
               FROM friends f
               JOIN users u ON u.id = f.initiated_by
                    LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE (f.requester_id = ? OR f.addressee_id = ?)
                 AND f.initiated_by != ?
                 AND f.status = 'pending'
               ORDER BY f.created_at DESC''',
            (user_id, user_id, user_id)
        ).fetchall()
        outgoing = conn.execute(
                '''SELECT f.id, f.created_at, u.id AS user_id, u.username, p.avatar_url, p.avatar_emoji
               FROM friends f
               JOIN users u ON u.id = CASE WHEN f.requester_id = ? THEN f.addressee_id ELSE f.requester_id END
                    LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE (f.requester_id = ? OR f.addressee_id = ?)
                 AND f.initiated_by = ?
                 AND f.status = 'pending'
               ORDER BY f.created_at DESC''',
            (user_id, user_id, user_id, user_id)
        ).fetchall()
    return {
        'incoming': [dict(r) for r in incoming],
        'outgoing': [dict(r) for r in outgoing]
    }


# ===== Group Settings =====

def get_group_settings(conv_id, user_id):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT * FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return None
        members = conn.execute(
                '''SELECT u.id, u.username, p.avatar_url, p.avatar_emoji, cm.role, cm.joined_at
               FROM users u JOIN conversation_members cm ON u.id = cm.user_id
                    LEFT JOIN user_profiles p ON p.user_id = u.id
               WHERE cm.conversation_id = ?
               ORDER BY CASE cm.role WHEN 'admin' THEN 0 ELSE 1 END, u.username''',
            (conv_id,)
        ).fetchall()
        my_role_row = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, user_id)
        ).fetchone()
    my_role = my_role_row['role'] if my_role_row else 'member'
    if conv['created_by'] == user_id:
        my_role = 'admin'
    return {
        'id': conv['id'],
        'name': conv['name'],
        'avatar_url': conv['avatar_url'],
        'announcement': conv['announcement'] or '',
        'created_by': conv['created_by'],
        'members': [dict(m) for m in members],
        'my_role': my_role,
    }


def update_group_announcement(conv_id, user_id, announcement):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        role = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, user_id)
        ).fetchone()
        if not (role and role['role'] == 'admin') and conv['created_by'] != user_id:
            return False, '需要管理员权限'
        conn.execute(
            'UPDATE conversations SET announcement = ? WHERE id = ? AND is_group = 1',
            (announcement.strip(), conv_id)
        )
        conn.commit()
    return True, '群公告已更新'


def update_group_avatar(conv_id, user_id, avatar_url):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        role = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, user_id)
        ).fetchone()
        if not (role and role['role'] == 'admin') and conv['created_by'] != user_id:
            return False, '需要管理员权限'
        conn.execute(
            'UPDATE conversations SET avatar_url = ? WHERE id = ? AND is_group = 1',
            (avatar_url, conv_id)
        )
        conn.commit()
    return True, '群头像已更新'


def pin_message(conv_id, message_id, user_id):
    with db_conn() as conn:
        conv = conn.execute('SELECT is_group FROM conversations WHERE id = ?', (conv_id,)).fetchone()
        if not conv:
            return False, '会话不存在'
        if not is_member(conv_id, user_id):
            return False, '无权限'
        msg = conn.execute(
            'SELECT id FROM messages WHERE id = ? AND conversation_id = ? AND is_revoked = 0',
            (message_id, conv_id)
        ).fetchone()
        if not msg:
            return False, '消息不存在或不可置顶'
        if conv['is_group']:
            role = conn.execute(
                'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
                (conv_id, user_id)
            ).fetchone()
            creator = conn.execute('SELECT created_by FROM conversations WHERE id = ?', (conv_id,)).fetchone()
            if not (role and role['role'] == 'admin') and (not creator or creator['created_by'] != user_id):
                return False, '仅群管理员可置顶'
        conn.execute(
            '''INSERT OR IGNORE INTO pinned_messages (conversation_id, message_id, pinned_by, pinned_at)
               VALUES (?, ?, ?, ?)''',
            (conv_id, message_id, user_id, time.time())
        )
        conn.commit()
    return True, '已置顶'


def unpin_message(conv_id, message_id, user_id):
    with db_conn() as conn:
        if not is_member(conv_id, user_id):
            return False, '无权限'
        conv = conn.execute('SELECT is_group, created_by FROM conversations WHERE id = ?', (conv_id,)).fetchone()
        if not conv:
            return False, '会话不存在'
        if conv['is_group']:
            role = conn.execute(
                'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
                (conv_id, user_id)
            ).fetchone()
            if not (role and role['role'] == 'admin') and conv['created_by'] != user_id:
                return False, '仅群管理员可取消置顶'
        conn.execute(
            'DELETE FROM pinned_messages WHERE conversation_id = ? AND message_id = ?',
            (conv_id, message_id)
        )
        conn.commit()
    return True, '已取消置顶'


def get_pinned_messages(conv_id):
    with db_conn() as conn:
        rows = conn.execute(
            '''SELECT pm.message_id, pm.pinned_by, pm.pinned_at,
                      m.content, m.msg_type, m.media_url, m.timestamp,
                      u.username AS pinned_by_name
               FROM pinned_messages pm
               JOIN messages m ON m.id = pm.message_id
               JOIN users u ON u.id = pm.pinned_by
               WHERE pm.conversation_id = ?
               ORDER BY pm.pinned_at DESC''',
            (conv_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_group_name(conv_id, user_id, new_name):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        role = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, user_id)
        ).fetchone()
        if not (role and role['role'] == 'admin') and conv['created_by'] != user_id:
            return False, '需要管理员权限'
        conn.execute(
            'UPDATE conversations SET name = ? WHERE id = ? AND is_group = 1', (new_name, conv_id)
        )
        conn.commit()
    return True, '群名已更新'

def add_group_member(conv_id, operator_id, new_member_id):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        role = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, operator_id)
        ).fetchone()
        if not (role and role['role'] == 'admin') and conv['created_by'] != operator_id:
            return False, '需要管理员权限'
        existing = conn.execute(
            'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, new_member_id)
        ).fetchone()
        if existing:
            return False, '该用户已在群中'
        conn.execute(
            "INSERT INTO conversation_members (conversation_id, user_id, joined_at, role) VALUES (?, ?, ?, 'member')",
            (conv_id, new_member_id, time.time())
        )
        conn.commit()
    return True, '成员已添加'


def remove_group_member(conv_id, operator_id, member_id):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        role = conn.execute(
            'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, operator_id)
        ).fetchone()
        if not (role and role['role'] == 'admin') and conv['created_by'] != operator_id:
            return False, '需要管理员权限'
        if conv['created_by'] == member_id:
            return False, '不能移除群主'
        conn.execute(
            'DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, member_id)
        )
        conn.commit()
    return True, '成员已移除'


def set_member_role(conv_id, operator_id, member_id, role):
    if role not in ('admin', 'member'):
        return False, '无效角色'
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv or conv['created_by'] != operator_id:
            return False, '只有群主可以设置管理员'
        conn.execute(
            'UPDATE conversation_members SET role = ? WHERE conversation_id = ? AND user_id = ?',
            (role, conv_id, member_id)
        )
        conn.commit()
    return True, '角色已更新'


def leave_group(conv_id, user_id):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv:
            return False, '群聊不存在'
        if conv['created_by'] == user_id:
            return False, '群主不能退出群聊，请先转让群主或解散群聊'
        conn.execute(
            'DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, user_id)
        )
        conn.commit()
    return True, '已退出群聊'


def transfer_group_owner(conv_id, current_owner_id, new_owner_id):
    with db_conn() as conn:
        conv = conn.execute(
            'SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)
        ).fetchone()
        if not conv or conv['created_by'] != current_owner_id:
            return False, '只有群主可以转让群主'
        new_member = conn.execute(
            'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
            (conv_id, new_owner_id)
        ).fetchone()
        if not new_member:
            return False, '新群主必须是群成员'
        conn.execute('UPDATE conversations SET created_by = ? WHERE id = ?', (new_owner_id, conv_id))
        conn.execute(
            "UPDATE conversation_members SET role = 'admin' WHERE conversation_id = ? AND user_id = ?",
            (conv_id, new_owner_id)
        )
        conn.execute(
            "UPDATE conversation_members SET role = 'member' WHERE conversation_id = ? AND user_id = ?",
            (conv_id, current_owner_id)
        )
        conn.commit()
    return True, '群主已转让'


# ===== System Settings =====

def get_system_settings():
    with db_conn() as conn:
        rows = conn.execute('SELECT key, value FROM system_settings').fetchall()
    return {r['key']: r['value'] for r in rows}


def update_system_setting(key, value):
    with db_conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)', (key, value)
        )
        conn.commit()


# ===== Admin Extended =====

def get_admin_stats():
    with db_conn() as conn:
        user_count   = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
        group_count  = conn.execute(
            'SELECT COUNT(*) as c FROM conversations WHERE is_group = 1'
        ).fetchone()['c']
        msg_count    = conn.execute('SELECT COUNT(*) as c FROM messages').fetchone()['c']
        active_users = conn.execute(
            'SELECT COUNT(DISTINCT sender_id) as c FROM messages WHERE timestamp > ?',
            (time.time() - 86400,)
        ).fetchone()['c']
    return {
        'user_count': user_count,
        'group_count': group_count,
        'message_count': msg_count,
        'active_users_24h': active_users,
    }
