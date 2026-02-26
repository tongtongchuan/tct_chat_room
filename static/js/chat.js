// ===== State =====
let currentUser = null;
let currentConvId = null;
let conversations = [];
let notificationsEnabled = true;
let selectedGroupMembers = [];
let socket = null;

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

    await loadConversations();
    initSocket();
});

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
        const initial = c.display_name[0].toUpperCase();
        const lastText = c.last_message
            ? (isGroup ? `${c.last_message.sender_name}: ` : '') + c.last_message.content
            : '暂无消息';
        const lastTime = c.last_message ? formatTime(c.last_message.timestamp) : '';
        return `
            <div class="conv-item ${isActive ? 'active' : ''} ${isGroup ? 'group' : ''}"
                 onclick="openConversation(${c.id})">
                <div class="conv-avatar">${isGroup ? '👥' : initial}</div>
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

    document.getElementById('chatPlaceholder').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'flex';
    document.getElementById('chatTitle').textContent = conv.display_name;

    const memberNames = conv.members.map(m => m.username).join(', ');
    document.getElementById('chatMembers').textContent =
        conv.is_group ? `${conv.members.length}人 · ${memberNames}` : '';

    socket.emit('join_conversation', { conversation_id: convId });

    // Load messages
    const res = await fetch(`/api/messages/${convId}`);
    const data = await res.json();
    if (!data.ok) return;

    const msgContainer = document.getElementById('messages');
    // Keep load-more button
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

    row.innerHTML = `
        ${senderHtml}
        <div class="msg-bubble">${escapeHtml(msg.content)}</div>
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
}

function showToast(title, body, convId) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `
        <div>
            <div class="toast-title">${escapeHtml(title)}</div>
            <div class="toast-body">${escapeHtml(body)}</div>
        </div>
    `;
    toast.onclick = () => {
        openConversation(convId);
        toast.remove();
    };
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
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
