import sqlite3
import hashlib
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chatroom.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_db_initialized = False

def init_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at REAL NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        is_group INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER,
        created_at REAL NOT NULL,
        FOREIGN KEY (created_by) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS conversation_members (
        conversation_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        joined_at REAL NOT NULL,
        PRIMARY KEY (conversation_id, user_id),
        FOREIGN KEY (conversation_id) REFERENCES conversations(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        sender_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        timestamp REAL NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id),
        FOREIGN KEY (sender_id) REFERENCES users(id)
    )''')

    conn.commit()
    conn.close()
    _db_initialized = True


def hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(32).hex()
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return h, salt


def create_user(username, password):
    conn = get_db()
    try:
        password_hash, salt = hash_password(password)
        conn.execute(
            'INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)',
            (username, password_hash, salt, time.time())
        )
        conn.commit()
        return True, '注册成功'
    except sqlite3.IntegrityError:
        return False, '用户名已存在'
    finally:
        conn.close()


def verify_user(username, password):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    if not user:
        return None
    h, _ = hash_password(password, user['salt'])
    if h == user['password_hash']:
        return dict(user)
    return None


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute('SELECT id, username FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def search_users(query, exclude_id=None):
    conn = get_db()
    if exclude_id:
        users = conn.execute(
            'SELECT id, username FROM users WHERE username LIKE ? AND id != ? LIMIT 20',
            (f'%{query}%', exclude_id)
        ).fetchall()
    else:
        users = conn.execute(
            'SELECT id, username FROM users WHERE username LIKE ? LIMIT 20',
            (f'%{query}%',)
        ).fetchall()
    conn.close()
    return [dict(u) for u in users]


def create_private_conversation(user1_id, user2_id):
    conn = get_db()
    # Check if private conversation already exists
    existing = conn.execute('''
        SELECT c.id FROM conversations c
        JOIN conversation_members cm1 ON c.id = cm1.conversation_id AND cm1.user_id = ?
        JOIN conversation_members cm2 ON c.id = cm2.conversation_id AND cm2.user_id = ?
        WHERE c.is_group = 0
    ''', (user1_id, user2_id)).fetchone()

    if existing:
        conn.close()
        return existing['id']

    now = time.time()
    c = conn.cursor()
    c.execute('INSERT INTO conversations (is_group, created_by, created_at) VALUES (0, ?, ?)',
              (user1_id, now))
    conv_id = c.lastrowid
    c.execute('INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
              (conv_id, user1_id, now))
    c.execute('INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
              (conv_id, user2_id, now))
    conn.commit()
    conn.close()
    return conv_id


def create_group_conversation(name, creator_id, member_ids):
    conn = get_db()
    now = time.time()
    c = conn.cursor()
    c.execute('INSERT INTO conversations (name, is_group, created_by, created_at) VALUES (?, 1, ?, ?)',
              (name, creator_id, now))
    conv_id = c.lastrowid
    all_members = set(member_ids) | {creator_id}
    for uid in all_members:
        c.execute('INSERT INTO conversation_members (conversation_id, user_id, joined_at) VALUES (?, ?, ?)',
                  (conv_id, uid, now))
    conn.commit()
    conn.close()
    return conv_id


def get_user_conversations(user_id):
    conn = get_db()
    convs = conn.execute('''
        SELECT c.id, c.name, c.is_group, c.created_at
        FROM conversations c
        JOIN conversation_members cm ON c.id = cm.conversation_id
        WHERE cm.user_id = ?
        ORDER BY c.created_at DESC
    ''', (user_id,)).fetchall()

    result = []
    for conv in convs:
        conv_dict = dict(conv)
        members = conn.execute('''
            SELECT u.id, u.username FROM users u
            JOIN conversation_members cm ON u.id = cm.user_id
            WHERE cm.conversation_id = ?
        ''', (conv['id'],)).fetchall()
        conv_dict['members'] = [dict(m) for m in members]

        if not conv_dict['is_group']:
            other = [m for m in conv_dict['members'] if m['id'] != user_id]
            conv_dict['display_name'] = other[0]['username'] if other else '未知'
        else:
            conv_dict['display_name'] = conv_dict['name'] or '群聊'

        # Get last message
        last_msg = conn.execute('''
            SELECT m.content, m.timestamp, u.username as sender_name
            FROM messages m JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id = ?
            ORDER BY m.timestamp DESC LIMIT 1
        ''', (conv['id'],)).fetchone()
        conv_dict['last_message'] = dict(last_msg) if last_msg else None

        result.append(conv_dict)

    # Sort by last message time
    result.sort(key=lambda x: x['last_message']['timestamp'] if x['last_message'] else x['created_at'], reverse=True)
    conn.close()
    return result


def save_message(conversation_id, sender_id, content):
    conn = get_db()
    now = time.time()
    c = conn.cursor()
    c.execute('INSERT INTO messages (conversation_id, sender_id, content, timestamp) VALUES (?, ?, ?, ?)',
              (conversation_id, sender_id, content, now))
    msg_id = c.lastrowid
    conn.commit()
    conn.close()
    return {'id': msg_id, 'conversation_id': conversation_id, 'sender_id': sender_id,
            'content': content, 'timestamp': now}


def get_messages(conversation_id, limit=50, before=None):
    conn = get_db()
    if before:
        msgs = conn.execute('''
            SELECT m.id, m.conversation_id, m.sender_id, m.content, m.timestamp, u.username as sender_name
            FROM messages m JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id = ? AND m.timestamp < ?
            ORDER BY m.timestamp DESC LIMIT ?
        ''', (conversation_id, before, limit)).fetchall()
    else:
        msgs = conn.execute('''
            SELECT m.id, m.conversation_id, m.sender_id, m.content, m.timestamp, u.username as sender_name
            FROM messages m JOIN users u ON m.sender_id = u.id
            WHERE m.conversation_id = ?
            ORDER BY m.timestamp DESC LIMIT ?
        ''', (conversation_id, limit)).fetchall()
    conn.close()
    return [dict(m) for m in reversed(msgs)]


def get_conversation_members(conversation_id):
    conn = get_db()
    members = conn.execute('''
        SELECT u.id, u.username FROM users u
        JOIN conversation_members cm ON u.id = cm.user_id
        WHERE cm.conversation_id = ?
    ''', (conversation_id,)).fetchall()
    conn.close()
    return [dict(m) for m in members]


def is_member(conversation_id, user_id):
    conn = get_db()
    row = conn.execute(
        'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conversation_id, user_id)
    ).fetchone()
    conn.close()
    return row is not None


# ===== Admin functions =====

def get_all_users():
    conn = get_db()
    users = conn.execute('SELECT id, username, created_at FROM users ORDER BY id').fetchall()
    conn.close()
    return [dict(u) for u in users]


def delete_user(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM conversation_members WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM messages WHERE sender_id = ?', (user_id,))
    # Nullify created_by reference before deleting user
    c.execute('UPDATE conversations SET created_by = NULL WHERE created_by = ?', (user_id,))
    # Clean up empty conversations (no members left)
    c.execute('''DELETE FROM messages WHERE conversation_id NOT IN (
        SELECT DISTINCT conversation_id FROM conversation_members
    )''')
    c.execute('''DELETE FROM conversations WHERE id NOT IN (
        SELECT DISTINCT conversation_id FROM conversation_members
    )''')
    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()


def get_all_groups():
    conn = get_db()
    groups = conn.execute('''
        SELECT c.id, c.name, c.created_at, u.username as creator_name
        FROM conversations c
        LEFT JOIN users u ON c.created_by = u.id
        WHERE c.is_group = 1
        ORDER BY c.id
    ''').fetchall()
    result = []
    for g in groups:
        gd = dict(g)
        members = conn.execute('''
            SELECT u.id, u.username FROM users u
            JOIN conversation_members cm ON u.id = cm.user_id
            WHERE cm.conversation_id = ?
        ''', (g['id'],)).fetchall()
        gd['members'] = [dict(m) for m in members]
        gd['member_count'] = len(gd['members'])
        result.append(gd)
    conn.close()
    return result


def rename_group(conv_id, new_name):
    conn = get_db()
    conn.execute('UPDATE conversations SET name = ? WHERE id = ? AND is_group = 1', (new_name, conv_id))
    conn.commit()
    conn.close()


def delete_group(conv_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
    c.execute('DELETE FROM conversation_members WHERE conversation_id = ?', (conv_id,))
    c.execute('DELETE FROM conversations WHERE id = ? AND is_group = 1', (conv_id,))
    conn.commit()
    conn.close()
