from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import database as db
import os
import json
import uuid
import time
import mimetypes

mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')

from werkzeug.utils import secure_filename
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

DEBUG = False  # set True for development
allowed_origins_env = os.environ.get('SOCKET_ALLOWED_ORIGINS', '')
if allowed_origins_env.strip():
    socket_allowed_origins = [o.strip() for o in allowed_origins_env.split(',') if o.strip()]
else:
    socket_allowed_origins = []

if DEBUG:
    socketio = SocketIO(app, cors_allowed_origins=socket_allowed_origins or None)
else:
    socketio = SocketIO(app, cors_allowed_origins=socket_allowed_origins or None, async_mode='gevent')

# user_id -> set of sid
online_users = {}
user_msg_timestamps = {}

# ── Upload config ───────────────────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
ALLOWED_IMAGE  = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'tiff', 'ico', 'avif'}
ALLOWED_AUDIO  = {'webm', 'ogg', 'mp3', 'wav', 'm4a', 'aac', 'flac', 'opus'}
ALLOWED_VIDEO  = {'mp4', 'mkv', 'mov', 'avi', 'webm', 'flv', 'm4v'}
ALLOWED_FILE   = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'zip', 'rar', '7z', 'tar', 'gz'}
MAX_UPLOAD_MB  = 100   # single-file limit raised; quota enforced per-user
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.before_request
def before_req():
    db.init_db()
    
    # CSRF Protection for API
    if request.method in ('POST', 'PUT', 'DELETE') and request.path.startswith('/api/'):
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')
        if origin:
            if not origin.startswith(request.host_url.rstrip('/')):
                return jsonify({'ok': False, 'msg': 'CSRF detected'}), 403
        elif referer:
            if not referer.startswith(request.host_url):
                return jsonify({'ok': False, 'msg': 'CSRF detected'}), 403
        else:
            return jsonify({'ok': False, 'msg': 'CSRF detected: missing Origin/Referer'}), 403

    # Reject banned users on every request
    if 'user_id' in session and request.path.startswith('/api/') and request.path != '/api/logout':
        user = db.get_user_by_id(session['user_id'])
        if not user or db.is_user_banned(session['user_id']):
            session.clear()
            return jsonify({'ok': False, 'msg': '账号已被封禁或已删除'}), 403


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
@limiter.limit("10 per minute")
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
@limiter.limit("20 per minute")
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
    if not other_id:
        return jsonify({'ok': False, 'msg': '无效用户'})
    if other_id != session['user_id'] and not db.can_start_private_chat(session['user_id'], other_id):
        return jsonify({'ok': False, 'msg': '仅可与好友发起私聊，请先发送好友申请并通过审核'}), 403
    conv_id = db.create_private_conversation(session['user_id'], other_id)
    return jsonify({'ok': True, 'conversation_id': conv_id})


@app.route('/api/conversations/self', methods=['POST'])
def create_self_chat():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    conv_id = db.create_self_conversation(session['user_id'])
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


@app.route('/api/messages/<int:message_id>/revoke', methods=['POST'])
@limiter.limit("60 per minute")
def revoke_message(message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    msg = db.get_message_by_id(message_id)
    if not msg:
        return jsonify({'ok': False, 'msg': '消息不存在'}), 404
    if not db.is_member(msg['conversation_id'], session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    ok, msg_text = db.revoke_message(message_id, session['user_id'])
    if not ok:
        return jsonify({'ok': False, 'msg': msg_text}), 400
    emit_payload = {
        'message_id': message_id,
        'conversation_id': msg['conversation_id'],
        'sender_name': msg.get('sender_name')
    }
    socketio.emit('message_revoked', emit_payload, room=f'conv_{msg["conversation_id"]}')
    return jsonify({'ok': True})


@app.route('/api/messages/<int:message_id>/edit', methods=['PUT'])
@limiter.limit("60 per minute")
def edit_message(message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.json or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'ok': False, 'msg': '内容不能为空'}), 400
    msg = db.get_message_by_id(message_id)
    if not msg:
        return jsonify({'ok': False, 'msg': '消息不存在'}), 404
    if not db.is_member(msg['conversation_id'], session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    settings = db.get_system_settings()
    max_len = int(settings.get('max_message_length', '2000'))
    if len(content) > max_len:
        return jsonify({'ok': False, 'msg': '消息过长'}), 400
    ok, msg_text = db.edit_message(message_id, session['user_id'], content)
    if not ok:
        return jsonify({'ok': False, 'msg': msg_text}), 400
    updated = db.get_message_by_id(message_id)
    socketio.emit('message_edited', updated, room=f'conv_{updated["conversation_id"]}')
    return jsonify({'ok': True, 'message': updated})


@app.route('/api/messages/<int:message_id>/forward', methods=['POST'])
@limiter.limit("30 per minute")
def forward_message(message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.json or {}
    conversation_ids = data.get('conversation_ids') or []
    if not isinstance(conversation_ids, list) or not conversation_ids:
        return jsonify({'ok': False, 'msg': '请选择目标会话'}), 400
    if len(conversation_ids) > 20:
        return jsonify({'ok': False, 'msg': '单次最多转发 20 个会话'}), 400
    original = db.get_message_by_id(message_id)
    if not original:
        return jsonify({'ok': False, 'msg': '消息不存在'}), 404
    if original.get('is_revoked'):
        return jsonify({'ok': False, 'msg': '已撤回消息不可转发'}), 400
    if not db.is_member(original['conversation_id'], session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403

    forwarded = 0
    for conv_id in conversation_ids:
        if not db.is_member(conv_id, session['user_id']):
            continue
        new_msg = db.save_message(
            conv_id,
            session['user_id'],
            original['content'],
            msg_type=original['msg_type'],
            media_url=original['media_url'],
            original_message_id=original['id']
        )
        new_msg['sender_name'] = session.get('username')
        socketio.emit('new_message', new_msg, room=f'conv_{conv_id}')
        forwarded += 1
    return jsonify({'ok': True, 'forwarded': forwarded})


@app.route('/api/messages/<int:message_id>/favorite', methods=['POST'])
@limiter.limit("120 per minute")
def toggle_favorite_message(message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    msg = db.get_message_by_id(message_id)
    if not msg:
        return jsonify({'ok': False, 'msg': '消息不存在'}), 404
    if not db.is_member(msg['conversation_id'], session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    if msg.get('is_revoked'):
        return jsonify({'ok': False, 'msg': '已撤回消息不可收藏'}), 400
    ok, favorited = db.toggle_favorite_message(session['user_id'], message_id)
    if not ok:
        return jsonify({'ok': False, 'msg': '收藏操作失败'}), 400
    return jsonify({'ok': ok, 'favorited': favorited})


@app.route('/api/favorites')
def get_favorites():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    before = request.args.get('before', type=float)
    limit = request.args.get('limit', default=30, type=int)
    result = db.get_favorite_messages(session['user_id'], limit=limit, before=before)
    return jsonify({
        'ok': True,
        'messages': result['items'],
        'has_more': result['has_more'],
        'next_before': result['next_before']
    })


@app.route('/api/users/search')
def search_users():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'ok': True, 'users': []})
    users = db.search_users_for_viewer(session['user_id'], q)
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
    avatar_url   = data.get('avatar_url')    # set to '' to clear custom avatar
    bio          = data.get('bio')
    theme        = data.get('theme')
    font_size    = data.get('font_size')
    # Validate avatar_url: only allow clearing or server-uploaded paths
    if avatar_url is not None and avatar_url != '' and avatar_url is not None:
        if not avatar_url.startswith('/static/uploads/avatars/') or '..' in avatar_url:
            return jsonify({'ok': False, 'msg': '无效的头像地址'})
        if not db.verify_file_owner(session['user_id'], avatar_url):
            return jsonify({'ok': False, 'msg': '无效的头像地址'})
    if theme and theme not in ('light', 'dark'):
        return jsonify({'ok': False, 'msg': '无效的主题'})
    if font_size and font_size not in ('small', 'medium', 'large'):
        return jsonify({'ok': False, 'msg': '无效的字体大小'})
    if bio is not None and len(bio) > 100:
        return jsonify({'ok': False, 'msg': '个性签名最多100个字符'})
    db.update_profile(session['user_id'], avatar_emoji=avatar_emoji, avatar_url=avatar_url,
                      bio=bio, theme=theme, font_size=font_size)
    return jsonify({'ok': True, 'msg': '设置已保存'})


# ── File / Avatar upload ────────────────────────────────────────────────────

def _allowed_ext(filename, allowed_set):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_set


def _save_upload(file_storage, sub_dir):
    """Save an uploaded file and return its public URL."""
    if '..' in sub_dir or '/' in sub_dir or '\\' in sub_dir:
        raise ValueError('Invalid sub_dir')
    folder = os.path.join(UPLOAD_FOLDER, sub_dir)
    os.makedirs(folder, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    ext = safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else 'bin'
    filename = f'{uuid.uuid4().hex}.{ext}'
    file_storage.save(os.path.join(folder, filename))
    return f'/static/uploads/{sub_dir}/{filename}'


@app.route('/api/upload/avatar', methods=['POST'])
@limiter.limit("10 per minute")
def upload_avatar():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'msg': '未选择文件'})
    safe_name = secure_filename(f.filename)
    if not _allowed_ext(safe_name, ALLOWED_IMAGE):
        return jsonify({'ok': False, 'msg': '仅支持图片格式'})
    f.seek(0, 2)
    file_size = f.tell()
    size_mb = file_size / (1024 * 1024)
    f.seek(0)
    if size_mb > 5:
        return jsonify({'ok': False, 'msg': '头像图片不超过 5 MB'})
        
    info = db.get_user_storage_info(session['user_id'])
    if info['used_bytes'] + file_size > info['quota_bytes']:
        return jsonify({'ok': False, 'msg': '云盘空间不足'})
        
    url = _save_upload(f, 'avatars')
    if not db.record_file_upload(session['user_id'], url, file_size if file_size else 0):
        saved_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), url.lstrip('/'))
        if os.path.exists(saved_path):
            os.remove(saved_path)
        return jsonify({'ok': False, 'msg': '云盘空间不足'})
    db.update_profile(session['user_id'], avatar_url=url)
    return jsonify({'ok': True, 'url': url})


@app.route('/api/upload', methods=['POST'])
@limiter.limit("30 per minute")
def upload_file():
    """Upload media/file for chat messages. Enforces per-user storage quota."""
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'msg': '未选择文件'})
    f.seek(0, 2)
    file_size = f.tell()
    f.seek(0)
    size_mb = file_size / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        return jsonify({'ok': False, 'msg': f'单文件不超过 {MAX_UPLOAD_MB} MB'})

    info = db.get_user_storage_info(session['user_id'])
    if info['used_bytes'] + file_size > info['quota_bytes']:
        return jsonify({'ok': False, 'msg': '云盘空间不足'})

    safe_name = secure_filename(f.filename)
    ext = safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else ''
    if ext in ALLOWED_IMAGE:
        msg_type, sub = 'image', 'images'
    elif ext in ALLOWED_AUDIO:
        msg_type, sub = 'audio', 'audio'
    elif ext in ALLOWED_VIDEO:
        msg_type, sub = 'video', 'video'
    elif ext in ALLOWED_FILE:
        msg_type, sub = 'file', 'files'
    else:
        return jsonify({'ok': False, 'msg': '不支持的文件类型'})

    url = _save_upload(f, sub)
    # Atomic quota check + record
    if not db.record_file_upload(session['user_id'], url, file_size):
        # Quota exceeded — remove the saved file
        saved_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), url.lstrip('/'))
        if os.path.exists(saved_path):
            os.remove(saved_path)
        return jsonify({'ok': False, 'msg': '云盘空间不足'})
    return jsonify({'ok': True, 'url': url, 'msg_type': msg_type,
                    'filename': safe_name or f'file.{ext}'})


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


@app.route('/api/storage/usage')
def storage_usage():
    """Return the current user's cloud storage usage and quota."""
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    info = db.get_user_storage_info(session['user_id'])
    return jsonify({'ok': True, **info})


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


@app.route('/api/contacts/review')
def get_friend_review():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    review = db.get_friend_review(session['user_id'])
    return jsonify({'ok': True, **review})


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


@app.route('/api/conversations/<int:conv_id>/pinned')
def get_pinned(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    if not db.is_member(conv_id, session['user_id']):
        return jsonify({'ok': False, 'msg': '无权限'}), 403
    items = db.get_pinned_messages(conv_id)
    return jsonify({'ok': True, 'items': items})


@app.route('/api/conversations/<int:conv_id>/pinned/<int:message_id>', methods=['POST'])
def pin_message(conv_id, message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.pin_message(conv_id, message_id, session['user_id'])
    if ok:
        socketio.emit('pinned_updated', {'conversation_id': conv_id}, room=f'conv_{conv_id}')
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/pinned/<int:message_id>', methods=['DELETE'])
def unpin_message(conv_id, message_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    ok, msg = db.unpin_message(conv_id, message_id, session['user_id'])
    if ok:
        socketio.emit('pinned_updated', {'conversation_id': conv_id}, room=f'conv_{conv_id}')
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


@app.route('/api/conversations/<int:conv_id>/announcement', methods=['PUT'])
def update_group_announcement(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    announcement = ((request.json or {}).get('announcement') or '').strip()
    if len(announcement) > 200:
        return jsonify({'ok': False, 'msg': '群公告最多 200 字'}), 400
    ok, msg = db.update_group_announcement(conv_id, session['user_id'], announcement)
    if ok:
        socketio.emit('group_updated', {
            'conversation_id': conv_id,
            'announcement': announcement
        }, room=f'conv_{conv_id}')
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/conversations/<int:conv_id>/avatar', methods=['PUT'])
def update_group_avatar(conv_id):
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    avatar_url = ((request.json or {}).get('avatar_url') or '').strip()
    if avatar_url:
        if not avatar_url.startswith('/static/uploads/') or '..' in avatar_url:
            return jsonify({'ok': False, 'msg': '无效头像地址'}), 400
        if not db.verify_file_owner(session['user_id'], avatar_url):
            return jsonify({'ok': False, 'msg': '无效头像地址'}), 400
    ok, msg = db.update_group_avatar(conv_id, session['user_id'], avatar_url or None)
    if ok:
        socketio.emit('group_updated', {
            'conversation_id': conv_id,
            'avatar_url': avatar_url or None
        }, room=f'conv_{conv_id}')
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
    if db.is_user_banned(uid):
        return
    now = time.time()
    timestamps = user_msg_timestamps.setdefault(uid, [])
    timestamps[:] = [t for t in timestamps if now - t < 1]
    if len(timestamps) >= 6:
        return
    timestamps.append(now)
    conv_id  = data.get('conversation_id')
    content  = data.get('content', '').strip()
    msg_type = data.get('msg_type', 'text')   # 'text'|'image'|'audio'|'file'
    media_url = data.get('media_url')         # server-side path already saved by /api/upload
    filename  = data.get('filename', '')

    # Whitelist msg_type
    if msg_type not in ('text', 'image', 'audio', 'video', 'file'):
        msg_type = 'text'

    # Validate media_url: must be a local upload path owned by this user
    if media_url:
        if not isinstance(media_url, str) or not media_url.startswith('/static/uploads/') or '..' in media_url:
            return
        if not db.verify_file_owner(uid, media_url):
            return

    # For media messages content may be empty – use filename as fallback display text
    if msg_type != 'text' and not content:
        content = filename or msg_type
    if not conv_id or (not content and msg_type == 'text'):
        return
    if not db.is_member(conv_id, uid):
        return
    if msg_type == 'text':
        settings = db.get_system_settings()
        max_len = int(settings.get('max_message_length', '2000'))
        if len(content) > max_len:
            return

    orig_id = data.get('original_message_id')
    try:
        if orig_id: orig_id = int(orig_id)
    except:
        orig_id = None

    msg = db.save_message(conv_id, uid, content, msg_type=msg_type, media_url=media_url, original_message_id=orig_id)
    msg['sender_name'] = session.get('username')
    if orig_id:
        orig_msg = db.get_message_by_id(orig_id)
        if orig_msg:
            msg['original_content'] = orig_msg['content']
            msg['original_sender_name'] = db.get_username(orig_msg['sender_id'])
    if filename:
        msg['filename'] = filename
    emit('new_message', msg, room=f'conv_{conv_id}')


# ---------- Admin ----------

ADMIN_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'admin_config.json')
_admin_config_cache = {'mtime': None, 'config': None}


def verify_admin_password(password):
    if not os.path.exists(ADMIN_CONFIG_PATH):
        return False
    mtime = os.path.getmtime(ADMIN_CONFIG_PATH)
    if _admin_config_cache['config'] is None or _admin_config_cache['mtime'] != mtime:
        with open(ADMIN_CONFIG_PATH, 'r', encoding='utf-8') as f:
            _admin_config_cache['config'] = json.load(f)
            _admin_config_cache['mtime'] = mtime
    config = _admin_config_cache['config']
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
@limiter.limit("10 per minute")
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
    # Attach global quota to users who haven't overridden it
    settings = db.get_system_settings()
    default_quota_mb = int(settings.get('default_storage_quota_mb', '10240'))
    for u in users:
        if u['storage_quota_mb'] is None:
            u['storage_quota_mb'] = default_quota_mb
            u['quota_is_default'] = True
        else:
            u['quota_is_default'] = False
    return jsonify({'ok': True, 'users': users})


@app.route('/api/admin/users', methods=['POST'])
@require_admin
def admin_create_user():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'ok': False, 'msg': '用户名和密码不能为空'})
    if len(username) < 2 or len(username) > 20:
        return jsonify({'ok': False, 'msg': '用户名长度应2-20个字符'})
    if len(password) < 4:
        return jsonify({'ok': False, 'msg': '密码至少4个字符'})
    ok, msg = db.create_user(username, password)
    return jsonify({'ok': ok, 'msg': msg})


@app.route('/api/admin/users/<int:user_id>/quota', methods=['PUT'])
@require_admin
def admin_set_user_quota(user_id):
    data = request.json or {}
    quota_mb = data.get('quota_mb')
    if quota_mb is not None:
        try:
            quota_mb = int(quota_mb)
            if quota_mb < 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'ok': False, 'msg': '无效的配额值'})
    db.set_user_quota(user_id, quota_mb)
    return jsonify({'ok': True, 'msg': '用户配额已更新'})


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
                    'allow_friend_requests', 'default_storage_quota_mb')
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
