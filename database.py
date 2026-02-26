import sqlite3
import os
import time
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError

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
        created_at REAL NOT NULL,
        is_banned INTEGER NOT NULL DEFAULT 0
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
        role TEXT NOT NULL DEFAULT 'member',
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

    c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        avatar_emoji TEXT NOT NULL DEFAULT '😊',
        bio TEXT NOT NULL DEFAULT '',
        theme TEXT NOT NULL DEFAULT 'light',
        font_size TEXT NOT NULL DEFAULT 'medium',
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS friends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requester_id INTEGER NOT NULL,
        addressee_id INTEGER NOT NULL,
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

    # Default system settings
    for key, value in [
        ('registration_enabled', '1'),
        ('max_message_length', '2000'),
        ('system_name', '聊天室'),
        ('allow_friend_requests', '1'),
        ('db_version', '0'),
    ]:
        c.execute('INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)', (key, value))

    # Column migrations (idempotent)
    for sql in [
        'ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0',
        'ALTER TABLE conversation_members ADD COLUMN role TEXT NOT NULL DEFAULT "member"',
    ]:
        try:
            c.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Data migration: set group creators as admin (run once)
    db_version = c.execute("SELECT value FROM system_settings WHERE key = 'db_version'").fetchone()
    if db_version and db_version['value'] == '0':
        c.execute('''UPDATE conversation_members SET role = 'admin'
                     WHERE user_id = (
                         SELECT created_by FROM conversations
                         WHERE id = conversation_id AND is_group = 1 AND created_by IS NOT NULL
                     )''')
        c.execute("UPDATE system_settings SET value = '1' WHERE key = 'db_version'")

    conn.commit()
    conn.close()
    _db_initialized = True


_ph = PasswordHasher(type=Type.ID)  # argon2id


def create_user(username, password):
    conn = get_db()
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
    finally:
        conn.close()


def verify_user(username, password):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    if not user:
        return None
    try:
        _ph.verify(user['password_hash'], password)
        return dict(user)
    except VerifyMismatchError:
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
        role = 'admin' if uid == creator_id else 'member'
        c.execute('INSERT INTO conversation_members (conversation_id, user_id, joined_at, role) VALUES (?, ?, ?, ?)',
                  (conv_id, uid, now, role))
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


# ===== Profile & Settings =====

def get_profile(user_id):
    conn = get_db()
    user = conn.execute('SELECT id, username, created_at FROM users WHERE id = ?', (user_id,)).fetchone()
    profile = conn.execute('SELECT * FROM user_profiles WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if not user:
        return None
    result = dict(user)
    if profile:
        result.update({k: profile[k] for k in ('avatar_emoji', 'bio', 'theme', 'font_size')})
    else:
        result.update({'avatar_emoji': '😊', 'bio': '', 'theme': 'light', 'font_size': 'medium'})
    return result


def update_profile(user_id, avatar_emoji=None, bio=None, theme=None, font_size=None):
    conn = get_db()
    existing = conn.execute('SELECT * FROM user_profiles WHERE user_id = ?', (user_id,)).fetchone()
    if existing:
        updates = {}
        if avatar_emoji is not None:
            updates['avatar_emoji'] = avatar_emoji
        if bio is not None:
            updates['bio'] = bio
        if theme is not None:
            updates['theme'] = theme
        if font_size is not None:
            updates['font_size'] = font_size
        if updates:
            set_clause = ', '.join(f'{k} = ?' for k in updates)
            conn.execute(f'UPDATE user_profiles SET {set_clause} WHERE user_id = ?',
                         list(updates.values()) + [user_id])
    else:
        conn.execute(
            'INSERT INTO user_profiles (user_id, avatar_emoji, bio, theme, font_size) VALUES (?, ?, ?, ?, ?)',
            (user_id, avatar_emoji or '😊', bio or '', theme or 'light', font_size or 'medium')
        )
    conn.commit()
    conn.close()


def change_password(user_id, old_password, new_password):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if not user:
        return False, '用户不存在'
    try:
        _ph.verify(user['password_hash'], old_password)
    except VerifyMismatchError:
        return False, '原密码错误'
    new_hash = _ph.hash(new_password)
    conn = get_db()
    conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (new_hash, user_id))
    conn.commit()
    conn.close()
    return True, '密码已更新'


# ===== Friends / Contacts =====

def get_friends(user_id):
    conn = get_db()
    friends = conn.execute('''
        SELECT u.id, u.username,
               CASE WHEN f.requester_id = ? THEN f.addressee_id ELSE f.requester_id END as friend_uid
        FROM friends f
        JOIN users u ON u.id = CASE WHEN f.requester_id = ? THEN f.addressee_id ELSE f.requester_id END
        WHERE (f.requester_id = ? OR f.addressee_id = ?) AND f.status = 'accepted'
        ORDER BY u.username
    ''', (user_id, user_id, user_id, user_id)).fetchall()
    conn.close()
    return [dict(f) for f in friends]


def get_friend_requests(user_id):
    conn = get_db()
    requests = conn.execute('''
        SELECT f.id, u.id as user_id, u.username, f.created_at
        FROM friends f
        JOIN users u ON f.requester_id = u.id
        WHERE f.addressee_id = ? AND f.status = 'pending'
        ORDER BY f.created_at DESC
    ''', (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in requests]


def get_pending_request_count(user_id):
    conn = get_db()
    count = conn.execute(
        'SELECT COUNT(*) as cnt FROM friends WHERE addressee_id = ? AND status = "pending"',
        (user_id,)
    ).fetchone()
    conn.close()
    return count['cnt'] if count else 0


def send_friend_request(requester_id, addressee_id):
    conn = get_db()
    existing = conn.execute('''
        SELECT * FROM friends WHERE
        (requester_id = ? AND addressee_id = ?) OR (requester_id = ? AND addressee_id = ?)
    ''', (requester_id, addressee_id, addressee_id, requester_id)).fetchone()
    if existing:
        conn.close()
        if existing['status'] == 'accepted':
            return False, '已经是好友'
        return False, '好友请求已发送或已存在'
    try:
        conn.execute(
            'INSERT INTO friends (requester_id, addressee_id, status, created_at) VALUES (?, ?, "pending", ?)',
            (requester_id, addressee_id, time.time())
        )
        conn.commit()
        conn.close()
        return True, '好友请求已发送'
    except sqlite3.IntegrityError:
        conn.close()
        return False, '请求已存在'


def accept_friend_request(request_id, user_id):
    conn = get_db()
    req = conn.execute(
        'SELECT * FROM friends WHERE id = ? AND addressee_id = ? AND status = "pending"',
        (request_id, user_id)
    ).fetchone()
    if not req:
        conn.close()
        return False, '请求不存在'
    conn.execute('UPDATE friends SET status = "accepted" WHERE id = ?', (request_id,))
    conn.commit()
    conn.close()
    return True, '已接受好友请求'


def reject_friend_request(request_id, user_id):
    conn = get_db()
    conn.execute(
        'DELETE FROM friends WHERE id = ? AND addressee_id = ? AND status = "pending"',
        (request_id, user_id)
    )
    conn.commit()
    conn.close()
    return True, '已拒绝好友请求'


def remove_friend(user_id, friend_id):
    conn = get_db()
    conn.execute('''DELETE FROM friends WHERE
        (requester_id = ? AND addressee_id = ?) OR (requester_id = ? AND addressee_id = ?)''',
                 (user_id, friend_id, friend_id, user_id))
    conn.commit()
    conn.close()
    return True, '已删除好友'


# ===== Group Settings =====

def get_group_settings(conv_id, user_id):
    conn = get_db()
    conv = conn.execute('SELECT * FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv:
        conn.close()
        return None
    members = conn.execute('''
        SELECT u.id, u.username, cm.role, cm.joined_at
        FROM users u JOIN conversation_members cm ON u.id = cm.user_id
        WHERE cm.conversation_id = ?
        ORDER BY CASE cm.role WHEN 'admin' THEN 0 ELSE 1 END, u.username
    ''', (conv_id,)).fetchall()
    my_role_row = conn.execute(
        'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, user_id)
    ).fetchone()
    conn.close()
    my_role = my_role_row['role'] if my_role_row else 'member'
    # Creator always has admin rights
    if conv['created_by'] == user_id:
        my_role = 'admin'
    return {
        'id': conv['id'],
        'name': conv['name'],
        'created_by': conv['created_by'],
        'members': [dict(m) for m in members],
        'my_role': my_role,
    }


def update_group_name(conv_id, user_id, new_name):
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv:
        conn.close()
        return False, '群聊不存在'
    role = conn.execute(
        'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, user_id)
    ).fetchone()
    if not (role and role['role'] == 'admin') and conv['created_by'] != user_id:
        conn.close()
        return False, '需要管理员权限'
    conn.execute('UPDATE conversations SET name = ? WHERE id = ? AND is_group = 1', (new_name, conv_id))
    conn.commit()
    conn.close()
    return True, '群名已更新'


def add_group_member(conv_id, operator_id, new_member_id):
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv:
        conn.close()
        return False, '群聊不存在'
    role = conn.execute(
        'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, operator_id)
    ).fetchone()
    if not (role and role['role'] == 'admin') and conv['created_by'] != operator_id:
        conn.close()
        return False, '需要管理员权限'
    existing = conn.execute(
        'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, new_member_id)
    ).fetchone()
    if existing:
        conn.close()
        return False, '该用户已在群中'
    conn.execute(
        'INSERT INTO conversation_members (conversation_id, user_id, joined_at, role) VALUES (?, ?, ?, "member")',
        (conv_id, new_member_id, time.time())
    )
    conn.commit()
    conn.close()
    return True, '成员已添加'


def remove_group_member(conv_id, operator_id, member_id):
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv:
        conn.close()
        return False, '群聊不存在'
    role = conn.execute(
        'SELECT role FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, operator_id)
    ).fetchone()
    if not (role and role['role'] == 'admin') and conv['created_by'] != operator_id:
        conn.close()
        return False, '需要管理员权限'
    if conv['created_by'] == member_id:
        conn.close()
        return False, '不能移除群主'
    conn.execute(
        'DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, member_id)
    )
    conn.commit()
    conn.close()
    return True, '成员已移除'


def set_member_role(conv_id, operator_id, member_id, role):
    if role not in ('admin', 'member'):
        return False, '无效角色'
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv or conv['created_by'] != operator_id:
        conn.close()
        return False, '只有群主可以设置管理员'
    conn.execute(
        'UPDATE conversation_members SET role = ? WHERE conversation_id = ? AND user_id = ?',
        (role, conv_id, member_id)
    )
    conn.commit()
    conn.close()
    return True, '角色已更新'


def leave_group(conv_id, user_id):
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv:
        conn.close()
        return False, '群聊不存在'
    if conv['created_by'] == user_id:
        conn.close()
        return False, '群主不能退出群聊，请先转让群主或解散群聊'
    conn.execute(
        'DELETE FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, user_id)
    )
    conn.commit()
    conn.close()
    return True, '已退出群聊'


def transfer_group_owner(conv_id, current_owner_id, new_owner_id):
    conn = get_db()
    conv = conn.execute('SELECT created_by FROM conversations WHERE id = ? AND is_group = 1', (conv_id,)).fetchone()
    if not conv or conv['created_by'] != current_owner_id:
        conn.close()
        return False, '只有群主可以转让群主'
    new_member = conn.execute(
        'SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
        (conv_id, new_owner_id)
    ).fetchone()
    if not new_member:
        conn.close()
        return False, '新群主必须是群成员'
    conn.execute('UPDATE conversations SET created_by = ? WHERE id = ?', (new_owner_id, conv_id))
    conn.execute(
        'UPDATE conversation_members SET role = "admin" WHERE conversation_id = ? AND user_id = ?',
        (conv_id, new_owner_id)
    )
    conn.execute(
        'UPDATE conversation_members SET role = "member" WHERE conversation_id = ? AND user_id = ?',
        (conv_id, current_owner_id)
    )
    conn.commit()
    conn.close()
    return True, '群主已转让'


# ===== System Settings =====

def get_system_settings():
    conn = get_db()
    rows = conn.execute('SELECT key, value FROM system_settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def update_system_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


# ===== Admin Extended =====

def get_admin_stats():
    conn = get_db()
    user_count = conn.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
    group_count = conn.execute('SELECT COUNT(*) as c FROM conversations WHERE is_group = 1').fetchone()['c']
    msg_count = conn.execute('SELECT COUNT(*) as c FROM messages').fetchone()['c']
    active_users = conn.execute(
        'SELECT COUNT(DISTINCT sender_id) as c FROM messages WHERE timestamp > ?',
        (time.time() - 86400,)
    ).fetchone()['c']
    conn.close()
    return {
        'user_count': user_count,
        'group_count': group_count,
        'message_count': msg_count,
        'active_users_24h': active_users,
    }


def ban_user(user_id, ban=True):
    conn = get_db()
    conn.execute('UPDATE users SET is_banned = ? WHERE id = ?', (1 if ban else 0, user_id))
    conn.commit()
    conn.close()
    return True, '已封禁用户' if ban else '已解封用户'


def get_all_users():
    conn = get_db()
    users = conn.execute(
        'SELECT id, username, created_at, is_banned FROM users ORDER BY id'
    ).fetchall()
    conn.close()
    return [dict(u) for u in users]
