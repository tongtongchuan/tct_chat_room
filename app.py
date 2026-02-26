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


# ---------- Socket.IO Events ----------

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
