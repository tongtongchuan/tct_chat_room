from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
import database as db
import os
import json
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

DEBUG = False # get debug mode

if DEBUG:
    socketio = SocketIO(app, cors_allowed_origins="*")
else:
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

# user_id -> set of sid
online_users = {}


@app.before_request
def before_req():
    db.init_db()


# ---------- Pages ----------

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('chat'))
    return redirect(url_for('login'))


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/chat')
def chat():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html')


# ---------- Auth API ----------

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    # Check if registration is enabled
    settings = db.get_system_settings()
    if settings.get('registration_enabled', '1') == '0':
        return jsonify({'ok': False, 'msg': '注册已关闭'})
    if not username or not password:
        return jsonify({'ok': False, 'msg': '用户名和密码不能为空'})
    if len(username) < 2 or len(username) > 20:
        return jsonify({'ok': False, 'msg': '用户名长度应为2-20个字符'})
    if len(password) < 4:
        return jsonify({'ok': False, 'msg': '密码至少4个字符'})
    ok, msg = db.create_user(username, password)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    user = db.verify_user(username, password)
    if user:
        if user.get('is_banned'):
            return jsonify({'ok': False, 'msg': '账号已被封禁，请联系管理员'})
        session['user_id'] = user['id']
        session['username'] = user['username']
        return jsonify({'ok': True, 'user': {'id': user['id'], 'username': user['username']}})
    return jsonify({'ok': False, 'msg': '用户名或密码错误'})


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    return jsonify({'ok': True, 'user': {'id': session['user_id'], 'username': session['username']}})


# ---------- Conversation API ----------

@app.route('/api/conversations')
def get_conversations():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    convs = db.get_user_conversations(session['user_id'])
    return jsonify({'ok': True, 'conversations': convs})


@app.route('/api/conversations/private', methods=['POST'])
def create_private():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    other_id = request.json.get('user_id')
    if not other_id or other_id == session['user_id']:
        return jsonify({'ok': False, 'msg': '无效用户'})
    conv_id = db.create_private_conversation(session['user_id'], other_id)
    return jsonify({'ok': True, 'conversation_id': conv_id})


@app.route('/api/conversations/group', methods=['POST'])
def create_group():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.json
    name = data.get('name', '').strip()
    member_ids = data.get('member_ids', [])
    if not name:
        return jsonify({'ok': False, 'msg': '请输入群名'})
    if not member_ids:
        return jsonify({'ok': False, 'msg': '请至少选择一个成员'})
    conv_id = db.create_group_conversation(name, session['user_id'], member_ids)
    # Notify members
    for uid in member_ids:
        if uid in online_users:
            for sid in online_users[uid]:
                socketio.emit('conversation_created', {'conversation_id': conv_id}, room=sid)
    return jsonify({'ok': True, 'conversation_id': conv_id})


@app.route('/api/messages/<int:conv_id>')
def get_messages(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    if not db.is_member(conv_id, session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    before = request.args.get('before', type=float)
    msgs = db.get_messages(conv_id, before=before)
    return jsonify({'ok': True, 'messages': msgs})


@app.route('/api/users/search')
def search_users():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'ok': True, 'users': []})
    users = db.search_users(q, exclude_id=session['user_id'])
    return jsonify({'ok': True, 'users': users})


# ---------- Profile & Settings API ----------

@app.route('/api/profile')
def get_profile():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    profile = db.get_profile(session['user_id'])
    return jsonify({'ok': True, 'profile': profile})


@app.route('/api/profile', methods=['PUT'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.json or {}
    avatar_emoji = data.get('avatar_emoji')
    bio = data.get('bio')
    theme = data.get('theme')
    font_size = data.get('font_size')
    if theme and theme not in ('light', 'dark'):
        return jsonify({'ok': False, 'msg': '无效的主题'})
    if font_size and font_size not in ('small', 'medium', 'large'):
        return jsonify({'ok': False, 'msg': '无效的字体大小'})
    if bio is not None and len(bio) > 100:
        return jsonify({'ok': False, 'msg': '个性签名最多100个字符'})
    db.update_profile(session['user_id'], avatar_emoji=avatar_emoji, bio=bio,
                      theme=theme, font_size=font_size)
    return jsonify({'ok': True, 'msg': '设置已保存'})


@app.route('/api/settings/password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.json or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')
    if not old_password or not new_password:
        return jsonify({'ok': False, 'msg': '请填写完整'})
    if len(new_password) < 4:
        return jsonify({'ok': False, 'msg': '新密码至少4个字符'})
    ok, msg = db.change_password(session['user_id'], old_password, new_password)
    if ok:
        session.clear()
    return jsonify({'ok': ok, 'msg': msg})


# ---------- Contacts / Friends API ----------

@app.route('/api/contacts')
def get_contacts():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    friends = db.get_friends(session['user_id'])
    pending_count = db.get_pending_request_count(session['user_id'])
    return jsonify({'ok': True, 'friends': friends, 'pending_count': pending_count})


@app.route('/api/contacts/requests')
def get_friend_requests():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    requests = db.get_friend_requests(session['user_id'])
    return jsonify({'ok': True, 'requests': requests})


@app.route('/api/contacts/request', methods=['POST'])
def send_friend_request():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    settings = db.get_system_settings()
    if settings.get('allow_friend_requests', '1') == '0':
        return jsonify({'ok': False, 'msg': '好友功能已关闭'})
    target_id = request.json.get('user_id')
    if not target_id or target_id == session['user_id']:
        return jsonify({'ok': False, 'msg': '无效用户'})
    ok, msg = db.send_friend_request(session['user_id'], target_id)
    if ok:
        # Notify target user via Socket.IO
        if target_id in online_users:
            for sid in online_users[target_id]:
                socketio.emit('friend_request', {
                    'from_id': session['user_id'],
                    'from_name': session['username']
                }, room=sid)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/contacts/requests/<int:request_id>/accept', methods=['POST'])
def accept_friend_request(request_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.accept_friend_request(request_id, session['user_id'])
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/contacts/requests/<int:request_id>/reject', methods=['POST'])
def reject_friend_request(request_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.reject_friend_request(request_id, session['user_id'])
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/contacts/<int:friend_id>', methods=['DELETE'])
def remove_friend(friend_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.remove_friend(session['user_id'], friend_id)
    return jsonify({'ok': ok, 'msg': msg})


# ---------- Group Settings API ----------

@app.route('/api/conversations/<int:conv_id>/settings')
def get_group_settings(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    if not db.is_member(conv_id, session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    settings = db.get_group_settings(conv_id, session['user_id'])
    if not settings:
        return jsonify({'ok': False, 'msg': '群聊不存在'}), 404
    return jsonify({'ok': True, 'settings': settings})


@app.route('/api/conversations/<int:conv_id>/name', methods=['PUT'])
def update_group_name(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    new_name = (request.json or {}).get('name', '').strip()
    if not new_name:
        return jsonify({'ok': False, 'msg': '群名不能为空'})
    ok, msg = db.update_group_name(conv_id, session['user_id'], new_name)
    if ok:
        socketio.emit('group_updated', {'conversation_id': conv_id, 'name': new_name},
                      room=f'conv_{conv_id}')
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/members', methods=['POST'])
def add_group_member(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    new_member_id = (request.json or {}).get('user_id')
    if not new_member_id:
        return jsonify({'ok': False, 'msg': '无效用户'})
    ok, msg = db.add_group_member(conv_id, session['user_id'], new_member_id)
    if ok and new_member_id in online_users:
        for sid in online_users[new_member_id]:
            socketio.emit('conversation_created', {'conversation_id': conv_id}, room=sid)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/members/<int:member_id>', methods=['DELETE'])
def remove_group_member(conv_id, member_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.remove_group_member(conv_id, session['user_id'], member_id)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/members/<int:member_id>/role', methods=['PUT'])
def set_member_role(conv_id, member_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    role = (request.json or {}).get('role', '')
    ok, msg = db.set_member_role(conv_id, session['user_id'], member_id, role)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/leave', methods=['POST'])
def leave_group(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.leave_group(conv_id, session['user_id'])
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/transfer', methods=['POST'])
def transfer_group_owner(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    new_owner_id = (request.json or {}).get('user_id')
    if not new_owner_id:
        return jsonify({'ok': False, 'msg': '无效用户'})
    ok, msg = db.transfer_group_owner(conv_id, session['user_id'], new_owner_id)
    return jsonify({'ok': ok, 'msg': msg})

@socketio.on('connect')
def on_connect():
    uid = session.get('user_id')
    if not uid:
        return False
    if uid not in online_users:
        online_users[uid] = set()
    online_users[uid].add(request.sid)
    # Join all conversation rooms
    convs = db.get_user_conversations(uid)
    for conv in convs:
        join_room(f'conv_{conv["id"]}')


@socketio.on('disconnect')
def on_disconnect():
    uid = session.get('user_id')
    if uid and uid in online_users:
        online_users[uid].discard(request.sid)
        if not online_users[uid]:
            del online_users[uid]


@socketio.on('join_conversation')
def on_join(data):
    conv_id = data.get('conversation_id')
    uid = session.get('user_id')
    if uid and conv_id and db.is_member(conv_id, uid):
        join_room(f'conv_{conv_id}')


@socketio.on('send_message')
def on_send(data):
    uid = session.get('user_id')
    if not uid:
        return
    conv_id = data.get('conversation_id')
    content = data.get('content', '').strip()
    if not conv_id or not content:
        return
    if not db.is_member(conv_id, uid):
        return
    # Enforce max message length from system settings
    settings = db.get_system_settings()
    max_len = int(settings.get('max_message_length', '2000'))
    if len(content) > max_len:
        return
    msg = db.save_message(conv_id, uid, content)
    msg['sender_name'] = session.get('username')
    emit('new_message', msg, room=f'conv_{conv_id}')


# ---------- Admin ----------

ADMIN_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin_config.json')


def verify_admin_password(password):
    if not os.path.exists(ADMIN_CONFIG_PATH):
        return False
    with open(ADMIN_CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = json.load(f)
    ph = PasswordHasher()
    try:
        return ph.verify(config['admin_password_hash'], password)
    except VerifyMismatchError:
        return False


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'ok': False, 'msg': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/admin')
def admin_page():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login_page'))
    return render_template('admin.html')


@app.route('/admin/login')
def admin_login_page():
    return render_template('admin_login.html')


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    password = request.json.get('password', '')
    if verify_admin_password(password):
        session['is_admin'] = True
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'msg': '密码错误'})


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    return jsonify({'ok': True})


@app.route('/api/admin/users')
@require_admin
def admin_get_users():
    users = db.get_all_users()
    return jsonify({'ok': True, 'users': users})


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@require_admin
def admin_delete_user(user_id):
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'ok': False, 'msg': '用户不存在'})
    db.delete_user(user_id)
    return jsonify({'ok': True, 'msg': f'用户 {user["username"]} 已删除'})


@app.route('/api/admin/users/<int:user_id>/ban', methods=['PUT'])
@require_admin
def admin_ban_user(user_id):
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({'ok': False, 'msg': '用户不存在'})
    ban = (request.json or {}).get('ban', True)
    db.ban_user(user_id, ban)
    action = '封禁' if ban else '解封'
    return jsonify({'ok': True, 'msg': f'用户 {user["username"]} 已{action}'})


@app.route('/api/admin/groups')
@require_admin
def admin_get_groups():
    groups = db.get_all_groups()
    return jsonify({'ok': True, 'groups': groups})


@app.route('/api/admin/groups/<int:conv_id>', methods=['PUT'])
@require_admin
def admin_rename_group(conv_id):
    new_name = request.json.get('name', '').strip()
    if not new_name:
        return jsonify({'ok': False, 'msg': '群名不能为空'})
    db.rename_group(conv_id, new_name)
    return jsonify({'ok': True, 'msg': '群名已更新'})


@app.route('/api/admin/groups/<int:conv_id>', methods=['DELETE'])
@require_admin
def admin_delete_group(conv_id):
    db.delete_group(conv_id)
    return jsonify({'ok': True, 'msg': '群聊已删除'})


@app.route('/api/admin/stats')
@require_admin
def admin_get_stats():
    stats = db.get_admin_stats()
    return jsonify({'ok': True, 'stats': stats})


@app.route('/api/admin/system-settings')
@require_admin
def admin_get_system_settings():
    settings = db.get_system_settings()
    return jsonify({'ok': True, 'settings': settings})


@app.route('/api/admin/system-settings', methods=['PUT'])
@require_admin
def admin_update_system_settings():
    data = request.json or {}
    allowed_keys = ('registration_enabled', 'max_message_length', 'system_name',
                    'allow_friend_requests')
    for key in allowed_keys:
        if key in data:
            db.update_system_setting(key, str(data[key]))
    return jsonify({'ok': True, 'msg': '系统设置已更新'})


if __name__ == '__main__':
    db.init_db()
    if not os.path.exists(ADMIN_CONFIG_PATH):
        print('⚠️  未找到管理员配置，请先运行: python admin_setup.py')
    print('='*50)
    if DEBUG:
        print('  模式: Development Server (debug)')
        print('  聊天室已启动: http://127.0.0.1:5000')
    else:
        print('  模式: Production (gevent WSGI)')
        print('  聊天室已启动: http://0.0.0.0:5000')
    print('  管理后台: http://127.0.0.1:5000/admin')
    print('='*50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=DEBUG)
