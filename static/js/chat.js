// ===== State =====
let currentUser = null;
let currentConvId = null;
let currentConvIsGroup = false;
let conversations = [];
let notificationsEnabled = true;
let selectedGroupMembers = [];
let socket = null;
let userProfile = null;
let selectedAvatarEmoji = '😊';
let selectedAvatarUrl = null;   // custom image avatar URL
let currentTheme = 'light';
let currentFontSize = 'medium';
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let speechRec = null;
let isSpeaking = false;

const AVATAR_EMOJIS = ['😊','😎','🤩','🥳','😄','🦊','🐼','🐨','🦁','🐯',
                       '🦄','🐸','🦋','⭐','🌸','🎯','🚀','💎','🎸','🏆'];

// ===== Helpers =====
function setAvatarEl(el, avatarUrl, avatarEmoji, username) {
    if (avatarUrl) {
        el.innerHTML = `<img src="${avatarUrl}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
        el.title = username || '';
    } else {
        el.textContent = avatarEmoji || (username ? username[0].toUpperCase() : '?');
    }
}

// ===== Init =====
document.addEventListener('DOMContentLoaded', async () => {
    const res = await fetch('/api/me');
    const data = await res.json();
    if (!data.ok) {
        window.location.href = '/login';
        return;
    }
    currentUser = data.user;
    document.getElementById('myAvatar').textContent = currentUser.username[0].toUpperCase();
    document.getElementById('myName').textContent = currentUser.username;

    await loadProfile();
    await loadConversations();
    await loadContacts();
    initSocket();

    // ── Drag-drop for file upload ────────────────────────────────────────
    const app = document.querySelector('.app');
    app.addEventListener('dragover', e => {
        e.preventDefault();
        if (currentConvId) document.getElementById('dropOverlay').style.display = 'flex';
    });
    app.addEventListener('dragleave', e => {
        if (!e.relatedTarget || !app.contains(e.relatedTarget))
            document.getElementById('dropOverlay').style.display = 'none';
    });
    app.addEventListener('drop', e => {
        e.preventDefault();
        document.getElementById('dropOverlay').style.display = 'none';
        if (!currentConvId) return;
        const files = e.dataTransfer.files;
        if (files.length) [...files].forEach(uploadAndSendFile);
    });
});

// ===== Profile =====
async function loadProfile() {
    const res = await fetch('/api/profile');
    const data = await res.json();
    if (!data.ok) return;
    userProfile = data.profile;
    selectedAvatarEmoji = userProfile.avatar_emoji || '😊';
    selectedAvatarUrl = userProfile.avatar_url || null;
    currentTheme = userProfile.theme || 'light';
    currentFontSize = userProfile.font_size || 'medium';
    // Apply avatar
    const myAvatarEl = document.getElementById('myAvatar');
    setAvatarEl(myAvatarEl, selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    applyTheme(currentTheme);
    applyFontSize(currentFontSize);
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
}

function applyFontSize(size) {
    document.documentElement.setAttribute('data-font', size);
    const prev = document.getElementById('fontPreview');
    if (prev) {
        const sizes = { small: '12px', medium: '14px', large: '16px' };
        prev.style.fontSize = sizes[size] || '14px';
    }
}

// ===== Sidebar Tabs =====
function switchSidebarTab(tab) {
    document.getElementById('tab-chats').classList.toggle('active', tab === 'chats');
    document.getElementById('tab-contacts').classList.toggle('active', tab === 'contacts');
    document.getElementById('panel-chats').style.display = tab === 'chats' ? 'flex' : 'none';
    const contactsPanel = document.getElementById('panel-contacts');
    if (tab === 'contacts') {
        contactsPanel.style.display = 'flex';
        contactsPanel.style.flexDirection = 'column';
        contactsPanel.style.flex = '1';
        contactsPanel.style.overflow = 'hidden';
        loadContacts();
    } else {
        contactsPanel.style.display = 'none';
    }
}

// ===== Socket.IO =====
function initSocket() {
    socket = io();

    socket.on('new_message', (msg) => {
        if (msg.conversation_id === currentConvId) {
            appendMessage(msg);
            scrollToBottom();
        }
        // Toast notification
        if (notificationsEnabled && msg.sender_id !== currentUser.id) {
            if (msg.conversation_id !== currentConvId || document.hidden) {
                showToast(msg.sender_name, msg.content, msg.conversation_id);
            }
        }
        // Update conversation list
        loadConversations();
    });

    socket.on('conversation_created', () => {
        loadConversations();
    });

    socket.on('group_updated', (data) => {
        if (data.conversation_id === currentConvId && data.name) {
            document.getElementById('chatTitle').textContent = data.name;
        }
        loadConversations();
    });

    socket.on('friend_request', (data) => {
        const friendReqNotif = document.getElementById('friendReqNotifToggle');
        if (!friendReqNotif || friendReqNotif.checked) {
            showToast('新好友请求', `${escapeHtml(data.from_name)} 请求添加你为好友`, null, '👥');
        }
        loadContacts();
    });
}

// ===== Conversations =====
async function loadConversations() {
    const res = await fetch('/api/conversations');
    const data = await res.json();
    if (!data.ok) return;
    conversations = data.conversations;
    renderConversations();
}

function renderConversations() {
    const list = document.getElementById('convList');
    if (!conversations.length) {
        list.innerHTML = '<div class="empty-hint">暂无会话，点击上方开始聊天</div>';
        return;
    }
    list.innerHTML = conversations.map(c => {
        const isActive = c.id === currentConvId;
        const isGroup = c.is_group;
        const isSelf = c.is_self_chat;
        const initial = c.display_name[0].toUpperCase();
        const icon = isSelf ? '📝' : (isGroup ? '👥' : initial);
        const lastMsg = c.last_message;
        const lastText = lastMsg
            ? (isGroup && !isSelf ? `${lastMsg.sender_name}: ` : '') +
              (lastMsg.msg_type && lastMsg.msg_type !== 'text' ? `[${{'image':'图片','audio':'语音','file':'文件'}[lastMsg.msg_type] || '附件'}]` : lastMsg.content)
            : '暂无消息';
        const lastTime = lastMsg ? formatTime(lastMsg.timestamp) : '';
        return `
            <div class="conv-item ${isActive ? 'active' : ''} ${isGroup ? 'group' : ''}"
                 onclick="openConversation(${c.id})">
                <div class="conv-avatar">${icon}</div>
                <div class="conv-info">
                    <div class="conv-name">${escapeHtml(c.display_name)}</div>
                    <div class="conv-last">${escapeHtml(lastText)}</div>
                </div>
                <div class="conv-time">${lastTime}</div>
            </div>
        `;
    }).join('');
}

async function openConversation(convId) {
    currentConvId = convId;
    const conv = conversations.find(c => c.id === convId);
    if (!conv) return;
    currentConvIsGroup = !!conv.is_group;

    document.getElementById('chatPlaceholder').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'flex';
    document.getElementById('chatTitle').textContent = conv.display_name;

    const memberNames = conv.members.map(m => m.username).join(', ');
    document.getElementById('chatMembers').textContent =
        conv.is_group ? `${conv.members.length}人 · ${memberNames}` : '';

    // Show/hide group settings button
    document.getElementById('groupSettingsBtn').style.display = conv.is_group ? 'flex' : 'none';

    // Close group settings panel when switching conversations
    closeGroupSettings();

    socket.emit('join_conversation', { conversation_id: convId });

    // Load messages
    const res = await fetch(`/api/messages/${convId}`);
    const data = await res.json();
    if (!data.ok) return;

    const msgContainer = document.getElementById('messages');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    msgContainer.innerHTML = '';
    msgContainer.appendChild(loadMoreBtn);
    loadMoreBtn.style.display = data.messages.length >= 50 ? 'block' : 'none';

    let lastDate = '';
    data.messages.forEach(msg => {
        const msgDate = new Date(msg.timestamp * 1000).toLocaleDateString();
        if (msgDate !== lastDate) {
            appendTimeDivider(msgDate);
            lastDate = msgDate;
        }
        appendMessage(msg, false);
    });

    scrollToBottom();
    renderConversations();
    document.getElementById('msgInput').focus();
}

async function loadMore() {
    if (!currentConvId) return;
    const firstMsg = document.querySelector('.msg-row');
    if (!firstMsg) return;
    const firstTimestamp = firstMsg.dataset.timestamp;

    const res = await fetch(`/api/messages/${currentConvId}?before=${firstTimestamp}`);
    const data = await res.json();
    if (!data.ok || !data.messages.length) {
        document.getElementById('loadMoreBtn').style.display = 'none';
        return;
    }

    const msgContainer = document.getElementById('messages');
    const loadMoreBtn = document.getElementById('loadMoreBtn');
    const fragment = document.createDocumentFragment();

    data.messages.forEach(msg => {
        const row = createMessageElement(msg);
        fragment.appendChild(row);
    });

    msgContainer.insertBefore(fragment, loadMoreBtn.nextSibling);
    if (data.messages.length < 50) loadMoreBtn.style.display = 'none';
}

// ===== Messages =====
function appendMessage(msg, animate = true) {
    const msgContainer = document.getElementById('messages');
    const row = createMessageElement(msg, animate);
    msgContainer.appendChild(row);
}

function createMessageElement(msg, animate = true) {
    const isMine = msg.sender_id === currentUser.id;
    const row = document.createElement('div');
    row.className = `msg-row ${isMine ? 'mine' : 'other'}`;
    row.dataset.timestamp = msg.timestamp;
    if (!animate) row.style.animation = 'none';

    const senderHtml = !isMine ? `<div class="msg-sender">${escapeHtml(msg.sender_name)}</div>` : '';
    const timeStr = new Date(msg.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    let bubbleContent;
    const msgType = msg.msg_type || 'text';
    if (msgType === 'image' && msg.media_url) {
        bubbleContent = `<img src="${escapeHtml(msg.media_url)}" class="msg-image" alt="图片"
            onclick="window.open('${escapeHtml(msg.media_url)}','_blank')">`;
    } else if (msgType === 'audio' && msg.media_url) {
        bubbleContent = `<audio controls src="${escapeHtml(msg.media_url)}" class="msg-audio"></audio>`;
    } else if (msgType === 'video' && msg.media_url) {
        bubbleContent = `<video controls src="${escapeHtml(msg.media_url)}" class="msg-image" style="max-height:200px"></video>`;
    } else if (msgType === 'file' && msg.media_url) {
        bubbleContent = `<a href="${escapeHtml(msg.media_url)}" download class="msg-file">📎 ${escapeHtml(msg.content)}</a>`;
    } else {
        bubbleContent = escapeHtml(msg.content);
    }

    row.innerHTML = `
        ${senderHtml}
        <div class="msg-bubble">${bubbleContent}</div>
        <div class="msg-time">${timeStr}</div>
    `;
    return row;
}

function appendTimeDivider(text) {
    const div = document.createElement('div');
    div.className = 'time-divider';
    div.textContent = text;
    document.getElementById('messages').appendChild(div);
}

function scrollToBottom() {
    const el = document.getElementById('messages');
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
}

// ===== Send =====
function sendMessage() {
    const input = document.getElementById('msgInput');
    const content = input.value.trim();
    if (!content || !currentConvId) return;
    socket.emit('send_message', { conversation_id: currentConvId, content });
    input.value = '';
    input.style.height = 'auto';
    input.focus();
}

function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
    // Auto-resize textarea
    requestAnimationFrame(() => {
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
    });
}

// ===== Search & Create =====
let searchTimer = null;

async function searchUsers() {
    const q = document.getElementById('searchUserInput').value.trim();
    const container = document.getElementById('searchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        container.innerHTML = data.users.map(u => `
            <div class="search-item" onclick="startPrivateChat(${u.id})">
                <span class="avatar">${u.username[0].toUpperCase()}</span>
                <span>${escapeHtml(u.username)}</span>
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
    }, 300);
}

async function startPrivateChat(userId) {
    const res = await fetch('/api/conversations/private', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
    });
    const data = await res.json();
    if (data.ok) {
        hideModal(null, 'newChat');
        await loadConversations();
        openConversation(data.conversation_id);
        switchSidebarTab('chats');
    }
}

async function searchGroupUsers() {
    const q = document.getElementById('searchGroupUserInput').value.trim();
    const container = document.getElementById('groupSearchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        const filtered = data.users.filter(u => !selectedGroupMembers.find(m => m.id === u.id));
        container.innerHTML = filtered.map(u => `
            <div class="search-item" onclick="addGroupMember(${u.id}, '${escapeHtml(u.username)}')">
                <span class="avatar">${u.username[0].toUpperCase()}</span>
                <span>${escapeHtml(u.username)}</span>
                <span style="margin-left:auto;color:#667eea;font-size:18px">+</span>
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
    }, 300);
}

function addGroupMember(id, username) {
    if (selectedGroupMembers.find(m => m.id === id)) return;
    selectedGroupMembers.push({ id, username });
    renderSelectedMembers();
    document.getElementById('searchGroupUserInput').value = '';
    document.getElementById('groupSearchResults').innerHTML = '';
}

function removeGroupMember(id) {
    selectedGroupMembers = selectedGroupMembers.filter(m => m.id !== id);
    renderSelectedMembers();
}

function renderSelectedMembers() {
    document.getElementById('selectedMembers').innerHTML = selectedGroupMembers.map(m => `
        <span class="member-tag">
            ${escapeHtml(m.username)}
            <span class="remove" onclick="removeGroupMember(${m.id})">×</span>
        </span>
    `).join('');
}

async function createGroup() {
    const name = document.getElementById('groupName').value.trim();
    if (!name) { alert('请输入群名称'); return; }
    if (!selectedGroupMembers.length) { alert('请至少选择一个成员'); return; }

    const res = await fetch('/api/conversations/group', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, member_ids: selectedGroupMembers.map(m => m.id) })
    });
    const data = await res.json();
    if (data.ok) {
        hideModal(null, 'newGroup');
        selectedGroupMembers = [];
        await loadConversations();
        openConversation(data.conversation_id);
    }
}

// ===== Contacts / Friends =====
async function loadContacts() {
    const res = await fetch('/api/contacts');
    const data = await res.json();
    if (!data.ok) return;

    // Update badge
    const badge = document.getElementById('contactsBadge');
    if (data.pending_count > 0) {
        badge.textContent = data.pending_count;
        badge.style.display = 'inline-flex';
    } else {
        badge.style.display = 'none';
    }

    renderFriendsList(data.friends);
    await loadFriendRequests();
}

async function loadFriendRequests() {
    const res = await fetch('/api/contacts/requests');
    const data = await res.json();
    if (!data.ok) return;

    const section = document.getElementById('friendRequestsSection');
    const list = document.getElementById('friendRequestsList');
    const badge = document.getElementById('reqBadge');

    if (data.requests.length > 0) {
        section.style.display = 'block';
        badge.textContent = data.requests.length;
        list.innerHTML = data.requests.map(r => `
            <div class="contact-item">
                <span class="avatar" style="width:34px;height:34px;font-size:13px">${r.username[0].toUpperCase()}</span>
                <span class="contact-name">${escapeHtml(r.username)}</span>
                <div class="contact-actions" style="opacity:1">
                    <button class="contact-btn accept" onclick="acceptRequest(${r.id})">同意</button>
                    <button class="contact-btn reject" onclick="rejectRequest(${r.id})">拒绝</button>
                </div>
            </div>
        `).join('');
    } else {
        section.style.display = 'none';
        list.innerHTML = '';
    }
}

function renderFriendsList(friends) {
    const list = document.getElementById('friendsList');
    if (!friends.length) {
        list.innerHTML = '<div class="empty-hint">暂无好友，点击上方添加</div>';
        return;
    }
    list.innerHTML = friends.map(f => `
        <div class="contact-item">
            <span class="avatar" style="width:34px;height:34px;font-size:13px">${f.username[0].toUpperCase()}</span>
            <span class="contact-name">${escapeHtml(f.username)}</span>
            <div class="contact-actions">
                <button class="contact-btn chat" onclick="chatWithFriend(${f.id})">发消息</button>
                <button class="contact-btn remove" onclick="removeFriend(${f.id}, '${escapeHtml(f.username)}')">删除</button>
            </div>
        </div>
    `).join('');
}

async function searchFriendUsers() {
    const q = document.getElementById('searchFriendInput').value.trim();
    const container = document.getElementById('friendSearchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        container.innerHTML = data.users.map(u => `
            <div class="search-item">
                <span class="avatar">${u.username[0].toUpperCase()}</span>
                <span>${escapeHtml(u.username)}</span>
                <button style="margin-left:auto" class="contact-btn chat"
                        onclick="sendFriendRequest(${u.id}, this)">添加好友</button>
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
    }, 300);
}

async function sendFriendRequest(userId, btn) {
    const res = await fetch('/api/contacts/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
    });
    const data = await res.json();
    if (data.ok) {
        if (btn) { btn.textContent = '已发送'; btn.disabled = true; }
    } else {
        alert(data.msg);
    }
}

async function acceptRequest(requestId) {
    const res = await fetch(`/api/contacts/requests/${requestId}/accept`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) loadContacts();
}

async function rejectRequest(requestId) {
    const res = await fetch(`/api/contacts/requests/${requestId}/reject`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) loadContacts();
}

async function chatWithFriend(userId) {
    const res = await fetch('/api/conversations/private', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId })
    });
    const data = await res.json();
    if (data.ok) {
        await loadConversations();
        openConversation(data.conversation_id);
        switchSidebarTab('chats');
    }
}

async function removeFriend(friendId, name) {
    if (!confirm(`确定删除好友「${name}」？`)) return;
    const res = await fetch(`/api/contacts/${friendId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) loadContacts();
}

function toggleFriendRequests() {
    const list = document.getElementById('friendRequestsList');
    const chevron = document.getElementById('reqChevron');
    const visible = list.style.display !== 'none';
    list.style.display = visible ? 'none' : 'block';
    chevron.textContent = visible ? '▶' : '▼';
}

// ===== Group Settings Panel =====
async function openGroupSettings() {
    if (!currentConvId || !currentConvIsGroup) return;
    const panel = document.getElementById('groupSettingsPanel');
    panel.style.display = 'flex';
    await renderGroupSettingsPanel();
}

function closeGroupSettings() {
    document.getElementById('groupSettingsPanel').style.display = 'none';
}

async function renderGroupSettingsPanel() {
    const res = await fetch(`/api/conversations/${currentConvId}/settings`);
    const data = await res.json();
    if (!data.ok) return;
    const s = data.settings;
    const isAdmin = s.my_role === 'admin';
    const isCreator = s.created_by === currentUser.id;

    const body = document.getElementById('gspBody');
    body.innerHTML = `
        <div class="gsp-section">
            <div class="gsp-section-title">群名称</div>
            <div class="gsp-input-row">
                <input type="text" id="gspGroupName" value="${escapeHtml(s.name || '')}"
                       ${isAdmin ? '' : 'readonly'} placeholder="群名称">
                ${isAdmin ? `<button class="gsp-btn" onclick="saveGroupName()">保存</button>` : ''}
            </div>
        </div>

        <div class="gsp-section">
            <div class="gsp-section-title">群成员 (${s.members.length})</div>
            ${s.members.map(m => {
                const isOwner = m.id === s.created_by;
                const isMe = m.id === currentUser.id;
                const canRemove = isAdmin && !isOwner && !isMe;
                const canSetRole = isCreator && !isOwner && !isMe;
                return `
                <div class="gsp-member">
                    <div class="mini-av">${m.username[0].toUpperCase()}</div>
                    <div class="member-name">${escapeHtml(m.username)}</div>
                    ${isOwner ? '<span class="role-badge creator">群主</span>' :
                      m.role === 'admin' ? '<span class="role-badge">管理员</span>' : ''}
                    ${isMe ? '<span style="font-size:11px;color:var(--text-3)">(我)</span>' : ''}
                    <div class="member-actions">
                        ${canSetRole ? (m.role === 'admin'
                            ? `<button class="gsp-small-btn" onclick="setMemberRole(${m.id}, 'member')">取消管理</button>`
                            : `<button class="gsp-small-btn" onclick="setMemberRole(${m.id}, 'admin')">设为管理</button>`) : ''}
                        ${canRemove ? `<button class="gsp-small-btn danger" onclick="removeMember(${m.id})">移除</button>` : ''}
                    </div>
                </div>`;
            }).join('')}
            ${isAdmin ? `
            <div class="gsp-add-member">
                <div class="gsp-section-title" style="margin-bottom:6px">添加成员</div>
                <div class="gsp-input-row">
                    <input type="text" id="gspAddMemberInput" placeholder="搜索用户..." oninput="searchGspMember()">
                    <button class="gsp-btn" onclick="doAddGspMember()">添加</button>
                </div>
                <div id="gspMemberResults" style="margin-top:6px"></div>
            </div>` : ''}
        </div>

        ${isCreator ? `
        <div class="gsp-section">
            <div class="gsp-section-title">群主管理</div>
            <div style="font-size:12px;color:var(--text-3);margin-bottom:8px">转让群主后您将变为普通成员</div>
            <select id="gspTransferSelect" style="width:100%;padding:7px 10px;border:1.5px solid var(--border);border-radius:7px;background:var(--bg);color:var(--text-1);font-size:13px;margin-bottom:8px">
                <option value="">选择新群主...</option>
                ${s.members.filter(m => m.id !== currentUser.id).map(m =>
                    `<option value="${m.id}">${escapeHtml(m.username)}</option>`
                ).join('')}
            </select>
            <button class="gsp-btn" style="width:100%" onclick="transferOwner()">转让群主</button>
        </div>` : ''}

        <div class="gsp-section">
            <button class="gsp-btn danger" style="width:100%" onclick="leaveGroupConv()">退出群聊</button>
        </div>
    `;
}

let gspSelectedMemberId = null;
let gspSearchTimer = null;

async function searchGspMember() {
    const q = document.getElementById('gspAddMemberInput').value.trim();
    const container = document.getElementById('gspMemberResults');
    if (!q) { container.innerHTML = ''; gspSelectedMemberId = null; return; }

    clearTimeout(gspSearchTimer);
    gspSearchTimer = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        container.innerHTML = data.users.map(u => `
            <div class="gsp-member" style="cursor:pointer;border-radius:7px;padding:5px 4px"
                 onclick="selectGspMember(${u.id}, '${escapeHtml(u.username)}', this)">
                <div class="mini-av">${u.username[0].toUpperCase()}</div>
                <div class="member-name">${escapeHtml(u.username)}</div>
            </div>
        `).join('') || '<div style="font-size:12px;color:var(--text-3);padding:4px">未找到用户</div>';
    }, 300);
}

function selectGspMember(id, username, el) {
    gspSelectedMemberId = id;
    document.querySelectorAll('#gspMemberResults .gsp-member').forEach(e => {
        e.style.background = e === el ? 'var(--primary-light)' : '';
    });
    document.getElementById('gspAddMemberInput').value = username;
    document.getElementById('gspMemberResults').innerHTML = '';
}

async function doAddGspMember() {
    const input = document.getElementById('gspAddMemberInput');
    if (!gspSelectedMemberId && !input.value.trim()) return;
    let memberId = gspSelectedMemberId;
    if (!memberId) {
        // Try searching for exact match
        const q = input.value.trim();
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        const exact = data.users && data.users.find(u => u.username === q);
        if (!exact) { alert('请先从搜索结果中选择用户'); return; }
        memberId = exact.id;
    }
    const res = await fetch(`/api/conversations/${currentConvId}/members`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: memberId })
    });
    const data = await res.json();
    if (data.ok) {
        gspSelectedMemberId = null;
        input.value = '';
        await renderGroupSettingsPanel();
        await loadConversations();
    } else {
        alert(data.msg);
    }
}

async function saveGroupName() {
    const name = document.getElementById('gspGroupName').value.trim();
    if (!name) { alert('群名不能为空'); return; }
    const res = await fetch(`/api/conversations/${currentConvId}/name`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.ok) {
        document.getElementById('chatTitle').textContent = name;
        loadConversations();
    } else {
        alert(data.msg);
    }
}

async function removeMember(memberId) {
    if (!confirm('确定移除该成员？')) return;
    const res = await fetch(`/api/conversations/${currentConvId}/members/${memberId}`, {
        method: 'DELETE'
    });
    const data = await res.json();
    if (data.ok) {
        await renderGroupSettingsPanel();
        await loadConversations();
    } else {
        alert(data.msg);
    }
}

async function setMemberRole(memberId, role) {
    const res = await fetch(`/api/conversations/${currentConvId}/members/${memberId}/role`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role })
    });
    const data = await res.json();
    if (data.ok) {
        await renderGroupSettingsPanel();
    } else {
        alert(data.msg);
    }
}

async function leaveGroupConv() {
    if (!confirm('确定退出该群聊？')) return;
    const res = await fetch(`/api/conversations/${currentConvId}/leave`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
        currentConvId = null;
        currentConvIsGroup = false;
        closeGroupSettings();
        document.getElementById('chatContainer').style.display = 'none';
        document.getElementById('chatPlaceholder').style.display = 'flex';
        await loadConversations();
    } else {
        alert(data.msg);
    }
}

async function transferOwner() {
    const select = document.getElementById('gspTransferSelect');
    const newOwnerId = parseInt(select.value);
    if (!newOwnerId) { alert('请选择新群主'); return; }
    if (!confirm('确定转让群主？此操作不可撤销。')) return;
    const res = await fetch(`/api/conversations/${currentConvId}/transfer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: newOwnerId })
    });
    const data = await res.json();
    if (data.ok) {
        await renderGroupSettingsPanel();
    } else {
        alert(data.msg);
    }
}

// ===== Settings Modal =====
function openSettings(tab) {
    const modal = document.getElementById('settingsModal');
    modal.classList.add('show');
    // Populate settings
    const settingsAvatarEl = document.getElementById('settingsAvatar');
    setAvatarEl(settingsAvatarEl, selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    const previewAvatarEl = document.getElementById('previewAvatar');
    if (previewAvatarEl) setAvatarEl(previewAvatarEl, selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    document.getElementById('settingsUsername').textContent = currentUser.username;

    // Populate avatar picker
    const picker = document.getElementById('avatarPicker');
    picker.innerHTML = AVATAR_EMOJIS.map(e => `
        <div class="avatar-option ${e === selectedAvatarEmoji && !selectedAvatarUrl ? 'selected' : ''}"
             onclick="selectAvatar('${e}', this)">${e}</div>
    `).join('');

    // Populate bio
    if (userProfile) {
        document.getElementById('profileBio').value = userProfile.bio || '';
        document.getElementById('profileUsername').textContent = userProfile.username || currentUser.username;
    }

    // Apply appearance state
    updateThemeUI(currentTheme);
    updateFontSizeUI(currentFontSize);
    document.getElementById('notifToggle').checked = notificationsEnabled;

    switchSettingsTab(tab || 'profile');
}

function closeSettings() {
    document.getElementById('settingsModal').classList.remove('show');
}

function closeSettingsIfOverlay(e) {
    if (e.target === document.getElementById('settingsModal')) closeSettings();
}

function switchSettingsTab(tab) {
    ['profile', 'security', 'appearance', 'notification', 'storage'].forEach(t => {
        document.getElementById(`stab-${t}`).style.display = t === tab ? 'block' : 'none';
        const navItem = document.getElementById(`snav-${t}`);
        if (navItem) navItem.classList.toggle('active', t === tab);
    });
    if (tab === 'storage') loadStorageInfo();
}

async function loadStorageInfo() {
    const res = await fetch('/api/storage/usage');
    const data = await res.json();
    if (!data.ok) return;
    const usedMb = data.used_mb;
    const quotaMb = data.quota_mb;
    const percent = Math.min(data.percent, 100);

    const fmt = mb => mb >= 1024
        ? `${(mb / 1024).toFixed(2)} GB`
        : `${mb.toFixed(1)} MB`;

    document.getElementById('storageText').textContent =
        `${fmt(usedMb)} / ${fmt(quotaMb)}  (${data.percent.toFixed(1)}%)`;
    document.getElementById('storageUsed').textContent = `已用 ${fmt(usedMb)}`;
    document.getElementById('storageQuota').textContent = `总配额 ${fmt(quotaMb)}`;
    document.getElementById('quotaMbLabel').textContent = (quotaMb / 1024).toFixed(1);

    const bar = document.getElementById('storageBar');
    bar.style.width = `${percent}%`;
    bar.style.background = percent > 90 ? '#EF4444'
        : percent > 70 ? '#F59E0B'
        : 'var(--primary)';
}

function selectAvatar(emoji, el) {
    selectedAvatarEmoji = emoji;
    selectedAvatarUrl = null;   // clear custom image when emoji selected
    document.querySelectorAll('.avatar-option').forEach(e => e.classList.remove('selected'));
    el.classList.add('selected');
    setAvatarEl(document.getElementById('myAvatar'), null, emoji, currentUser?.username);
    setAvatarEl(document.getElementById('settingsAvatar'), null, emoji, currentUser?.username);
    const previewAvatarEl = document.getElementById('previewAvatar');
    if (previewAvatarEl) setAvatarEl(previewAvatarEl, null, emoji, currentUser?.username);
}

async function saveProfile() {
    const bio = document.getElementById('profileBio').value;
    const res = await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_emoji: selectedAvatarEmoji, avatar_url: selectedAvatarUrl, bio })
    });
    const data = await res.json();
    if (data.ok) {
        if (userProfile) {
            userProfile.bio = bio;
            userProfile.avatar_emoji = selectedAvatarEmoji;
            userProfile.avatar_url = selectedAvatarUrl;
        }
        showSimpleToast('个人资料已保存', 'success');
    } else {
        showSimpleToast(data.msg, 'error');
    }
}

// ── Avatar upload / clear ────────────────────────────────────────────────────
async function uploadAvatar(input) {
    if (!input.files.length) return;
    const file = input.files[0];
    const formData = new FormData();
    formData.append('file', file);
    showSimpleToast('上传中...', 'info');
    const res = await fetch('/api/upload/avatar', { method: 'POST', body: formData });
    const data = await res.json();
    if (!data.ok) { showSimpleToast(data.msg || '上传失败', 'error'); return; }
    selectedAvatarUrl = data.url;
    setAvatarEl(document.getElementById('myAvatar'), selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    setAvatarEl(document.getElementById('settingsAvatar'), selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    const previewAvatarEl = document.getElementById('previewAvatar');
    if (previewAvatarEl) setAvatarEl(previewAvatarEl, selectedAvatarUrl, selectedAvatarEmoji, currentUser?.username);
    // Persist immediately
    await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_url: selectedAvatarUrl })
    });
    if (userProfile) userProfile.avatar_url = selectedAvatarUrl;
    showSimpleToast('头像已更新', 'success');
    input.value = '';
}

async function clearAvatarUrl() {
    selectedAvatarUrl = null;
    setAvatarEl(document.getElementById('myAvatar'), null, selectedAvatarEmoji, currentUser?.username);
    setAvatarEl(document.getElementById('settingsAvatar'), null, selectedAvatarEmoji, currentUser?.username);
    const previewAvatarEl = document.getElementById('previewAvatar');
    if (previewAvatarEl) setAvatarEl(previewAvatarEl, null, selectedAvatarEmoji, currentUser?.username);
    await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_url: null })
    });
    if (userProfile) userProfile.avatar_url = null;
    showSimpleToast('已清除图片头像', 'success');
}

// ── Self-chat (备忘录) ────────────────────────────────────────────────────────
async function openSelfChat() {
    const res = await fetch('/api/conversations/self', { method: 'POST' });
    const data = await res.json();
    if (!data.ok) { showSimpleToast(data.msg || '创建失败', 'error'); return; }
    await loadConversations();
    await openConversation(data.conversation_id);
    closeNewChatModal && closeNewChatModal();
}

// ── File upload helpers ──────────────────────────────────────────────────────
async function handleFileSelect(input) {
    if (!input.files.length) return;
    await uploadAndSendFile(input.files[0]);
    input.value = '';
}

async function uploadAndSendFile(file) {
    if (!currentConvId) return;
    const formData = new FormData();
    formData.append('file', file);
    showSimpleToast('上传中...', 'info');
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!data.ok) { showSimpleToast(data.msg || '上传失败', 'error'); return; }
    socket.emit('send_message', {
        conversation_id: currentConvId,
        content: data.filename || file.name,
        msg_type: data.msg_type,
        media_url: data.url
    });
}

// ── Voice recording ──────────────────────────────────────────────────────────
async function toggleRecording() {
    const btn = document.getElementById('recordBtn');
    if (isRecording) {
        // Stop recording
        mediaRecorder.stop();
        isRecording = false;
        btn.classList.remove('recording');
        btn.title = '录音';
        return;
    }
    if (!navigator.mediaDevices) { showSimpleToast('浏览器不支持录音', 'error'); return; }
    if (!currentConvId) { showSimpleToast('请先选择会话', 'error'); return; }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = e => { if (e.data.size) audioChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(audioChunks, { type: 'audio/webm' });
            const formData = new FormData();
            formData.append('file', blob, `recording_${Date.now()}.webm`);
            showSimpleToast('上传录音...', 'info');
            const res = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (!data.ok) { showSimpleToast(data.msg || '上传失败', 'error'); return; }
            socket.emit('send_message', {
                conversation_id: currentConvId,
                content: '语音消息',
                msg_type: 'audio',
                media_url: data.url
            });
        };
        mediaRecorder.start();
        isRecording = true;
        btn.classList.add('recording');
        btn.title = '点击停止录音';
    } catch (err) {
        showSimpleToast('无法访问麦克风: ' + err.message, 'error');
    }
}

// ── Speech recognition ───────────────────────────────────────────────────────
function toggleSpeech() {
    const btn = document.getElementById('speechBtn');
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) { showSimpleToast('浏览器不支持语音识别', 'error'); return; }
    if (isSpeaking) {
        speechRec && speechRec.stop();
        isSpeaking = false;
        btn.classList.remove('recording');
        btn.title = '语音输入';
        return;
    }
    speechRec = new SpeechRecognition();
    speechRec.lang = 'zh-CN';
    speechRec.continuous = false;
    speechRec.interimResults = false;
    speechRec.onresult = e => {
        const text = e.results[0][0].transcript;
        const input = document.getElementById('msgInput');
        input.value += text;
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    };
    speechRec.onerror = e => { showSimpleToast('识别错误: ' + e.error, 'error'); };
    speechRec.onend = () => {
        isSpeaking = false;
        btn.classList.remove('recording');
        btn.title = '语音输入';
    };
    speechRec.start();
    isSpeaking = true;
    btn.classList.add('recording');
    btn.title = '点击停止识别';
}

async function savePassword() {
    const oldPwd = document.getElementById('oldPassword').value;
    const newPwd = document.getElementById('newPassword').value;
    const newPwd2 = document.getElementById('newPassword2').value;
    if (!oldPwd || !newPwd) { showSimpleToast('请填写完整', 'error'); return; }
    if (newPwd !== newPwd2) { showSimpleToast('两次密码不一致', 'error'); return; }
    const res = await fetch('/api/settings/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd })
    });
    const data = await res.json();
    if (data.ok) {
        showSimpleToast('密码已修改，请重新登录', 'success');
        setTimeout(() => { window.location.href = '/login'; }, 1500);
    } else {
        showSimpleToast(data.msg, 'error');
    }
}

function setTheme(theme) {
    currentTheme = theme;
    applyTheme(theme);
    updateThemeUI(theme);
}

function updateThemeUI(theme) {
    document.getElementById('theme-light').classList.toggle('selected', theme === 'light');
    document.getElementById('theme-dark').classList.toggle('selected', theme === 'dark');
}

function setFontSize(size) {
    currentFontSize = size;
    applyFontSize(size);
    updateFontSizeUI(size);
}

function updateFontSizeUI(size) {
    ['small', 'medium', 'large'].forEach(s => {
        const btn = document.getElementById(`font-${s}`);
        if (btn) btn.classList.toggle('selected', s === size);
    });
}

async function saveAppearance() {
    const res = await fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: currentTheme, font_size: currentFontSize })
    });
    const data = await res.json();
    if (data.ok) {
        if (userProfile) { userProfile.theme = currentTheme; userProfile.font_size = currentFontSize; }
        showSimpleToast('外观设置已保存', 'success');
    } else {
        showSimpleToast(data.msg, 'error');
    }
}

function toggleNotifSetting() {
    notificationsEnabled = document.getElementById('notifToggle').checked;
    const btn = document.getElementById('toggleNotif');
    btn.classList.toggle('notif-off', !notificationsEnabled);
    btn.title = notificationsEnabled ? '消息通知已开启' : '消息通知已关闭';
}

// ===== Modal =====
function showModal(name) {
    document.getElementById(`modal-${name}`).classList.add('show');
    if (name === 'newChat') {
        document.getElementById('searchUserInput').value = '';
        document.getElementById('searchResults').innerHTML = '';
    }
    if (name === 'newGroup') {
        document.getElementById('groupName').value = '';
        document.getElementById('searchGroupUserInput').value = '';
        document.getElementById('groupSearchResults').innerHTML = '';
        selectedGroupMembers = [];
        renderSelectedMembers();
    }
    if (name === 'addFriend') {
        document.getElementById('searchFriendInput').value = '';
        document.getElementById('friendSearchResults').innerHTML = '';
    }
}

function hideModal(e, name) {
    if (e && e.target !== e.currentTarget) return;
    document.getElementById(`modal-${name}`).classList.remove('show');
}

// ===== Notifications =====
function toggleNotifications() {
    notificationsEnabled = !notificationsEnabled;
    const btn = document.getElementById('toggleNotif');
    btn.classList.toggle('notif-off', !notificationsEnabled);
    btn.title = notificationsEnabled ? '消息通知已开启' : '消息通知已关闭';
    const notifToggle = document.getElementById('notifToggle');
    if (notifToggle) notifToggle.checked = notificationsEnabled;
}

function showToast(title, body, convId, icon) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `
        <div style="font-size:20px">${icon || '💬'}</div>
        <div>
            <div class="toast-title">${escapeHtml(title)}</div>
            <div class="toast-body">${escapeHtml(body)}</div>
        </div>
    `;
    if (convId) {
        toast.onclick = () => {
            openConversation(convId);
            switchSidebarTab('chats');
            toast.remove();
        };
    } else {
        toast.onclick = () => toast.remove();
    }
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
}

function showSimpleToast(msg, type) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.style.borderLeftColor = type === 'error' ? '#EF4444' : '#22C55E';
    toast.innerHTML = `<div><div class="toast-title" style="color:${type === 'error' ? '#DC2626' : '#16A34A'}">${escapeHtml(msg)}</div></div>`;
    toast.onclick = () => toast.remove();
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// ===== Auth =====
async function doLogout() {
    await fetch('/api/logout', { method: 'POST' });
    window.location.href = '/login';
}

// ===== Helpers =====
function escapeHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function formatTime(ts) {
    const d = new Date(ts * 1000);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
    if (d.toDateString() === now.toDateString()) {
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return '昨天';
    return (d.getMonth() + 1) + '/' + d.getDate();
}

