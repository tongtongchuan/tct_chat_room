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
let recordMode = false;
let speechRec = null;
let isSpeaking = false;
let activeSidebarTab = 'chats';
let selectionMode = false;
let selectedMessageIds = new Set();
let pinnedMessageIds = new Set();
let contextMenuPayload = null;
let replyToId = null;
let favoritesCursor = null;
let favoritesHasMore = false;
let favoritesLoading = false;

const FAVORITES_PAGE_SIZE = 30;

const AVATAR_EMOJIS = ['😊','😎','🤩','🥳','😄','🦊','🐼','🐨','🦁','🐯',
                       '🦄','🐸','🦋','⭐','🌸','🎯','🚀','💎','🎸','🏆'];

// ===== Helpers =====
function setAvatarEl(el, avatarUrl, avatarEmoji, username) {
    if (avatarUrl) {
        el.innerHTML = `<img src="${escapeAttr(avatarUrl)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
        el.title = username || '';
    } else {
        const token = avatarEmoji && avatarEmoji !== '😊'
            ? avatarEmoji
            : getNameFallback(username);
        el.textContent = token;
    }
}

function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
    }
}

function getNameFallback(username) {
    return (username || '?').slice(0, 1).toUpperCase();
}

function getAvatarToken(avatarEmoji, username) {
    return avatarEmoji && avatarEmoji !== '😊' ? avatarEmoji : getNameFallback(username);
}

function userAvatarHtml(user, className = 'avatar', style = '') {
    const styleAttr = style ? ` style="${style}"` : '';
    if (user?.avatar_url) {
        return `<span class="${className}"${styleAttr}><img src="${escapeAttr(user.avatar_url)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></span>`;
    }
    return `<span class="${className}"${styleAttr}>${escapeHtml(getAvatarToken(user?.avatar_emoji, user?.username))}</span>`;
}

function groupAvatarGridHtml(members) {
    const shown = (members || []).slice(0, 4);
    if (!shown.length) return '<div class="conv-avatar">群</div>';
    const cells = shown.map((member) => {
        if (member.avatar_url) {
            return `<span class="conv-avatar-cell" style="background-image:url('${escapeAttr(member.avatar_url)}')"></span>`;
        }
        return `<span class="conv-avatar-cell-text">${escapeHtml(getAvatarToken(member.avatar_emoji, member.username))}</span>`;
    }).join('');
    return `<div class="conv-avatar conv-avatar-group"><div class="conv-avatar-grid">${cells}</div></div>`;
}

function conversationAvatarHtml(conv) {
    if (!conv.is_group) {
        if (conv.is_self_chat) return `<div class="conv-avatar">${getNameFallback(conv.display_name)}</div>`;
        const other = (conv.members || []).find(m => m.id !== currentUser.id);
        if (other?.avatar_url) {
            return `<div class="conv-avatar"><img src="${escapeAttr(other.avatar_url)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></div>`;
        }
        return `<div class="conv-avatar">${escapeHtml(getAvatarToken(other?.avatar_emoji, other?.username || conv.display_name))}</div>`;
    }
    if (conv.avatar_url) {
        return `<div class="conv-avatar"><img src="${escapeAttr(conv.avatar_url)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%"></div>`;
    }
    return groupAvatarGridHtml(conv.members || []);
}

function hideContextMenu() {
    const menu = document.getElementById('contextMenu');
    if (menu) menu.style.display = 'none';
    contextMenuPayload = null;
}

function setReplyTo(messageId) {
    const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
    if (!row) return;

    replyToId = messageId;
    const user = row.dataset.senderName;
    const type = row.dataset.msgType;
    let text = row.querySelector('.msg-bubble').innerText;
    
    // Clean up text for preview
    if (type === 'image') text = '[图片]';
    else if (type === 'audio') text = '[语音]';
    else if (type === 'video') text = '[视频]';
    else if (type === 'file') text = '[文件]';

    document.getElementById('replyToUser').textContent = user || '未知用户';
    document.getElementById('replyToText').textContent = text;
    document.getElementById('replyContainer').style.display = 'flex';
    document.getElementById('msgInput').focus();
    renderIcons();
}

function cancelReply() {
    replyToId = null;
    document.getElementById('replyContainer').style.display = 'none';
}

function showContextMenu(x, y, items, payload = null) {
    const menu = document.getElementById('contextMenu');
    if (!menu) return;
    contextMenuPayload = payload;
    menu.innerHTML = items.map(item => `
        <button class="item" type="button" onclick="${item.action}">
            <i data-lucide="${item.icon}"></i><span>${escapeHtml(item.label)}</span>
        </button>
    `).join('');
    menu.style.display = 'block';
    const maxX = window.innerWidth - menu.offsetWidth - 8;
    const maxY = window.innerHeight - menu.offsetHeight - 8;
    menu.style.left = `${Math.max(8, Math.min(x, maxX))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, maxY))}px`;
    renderIcons();
}

function bindContextMenus() {
    document.addEventListener('click', () => hideContextMenu());
    document.addEventListener('contextmenu', (event) => {
        const msgRow = event.target.closest('.msg-row');
        if (msgRow) {
            event.preventDefault();
            const messageId = Number(msgRow.dataset.messageId);
            const isMine = msgRow.classList.contains('mine');
            const isRevoked = msgRow.dataset.revoked === '1';
            const isPinned = pinnedMessageIds.has(messageId);
            const items = [];
            if (!isRevoked) {
                items.push({ icon: 'reply', label: '回复', action: `setReplyTo(${messageId});hideContextMenu();` });
                items.push({ icon: 'star', label: '收藏', action: `toggleFavoriteMessage(${messageId});hideContextMenu();` });
                items.push({ icon: 'forward', label: '转发', action: `forwardMessage(${messageId});hideContextMenu();` });
                items.push({ icon: selectionMode && selectedMessageIds.has(messageId) ? 'square' : 'check-square', label: selectionMode ? (selectedMessageIds.has(messageId) ? '取消选择' : '选择此条') : '进入多选并选择', action: `toggleMessageSelection(${messageId});hideContextMenu();` });
                items.push({ icon: isPinned ? 'pin-off' : 'pin', label: isPinned ? '取消置顶' : '置顶消息', action: `${isPinned ? `unpinMessage(${messageId})` : `pinMessage(${messageId})`};hideContextMenu();` });
            }
            if (isMine && !isRevoked) {
                items.push({ icon: 'pencil', label: '编辑', action: `editMessage(${messageId});hideContextMenu();` });
                items.push({ icon: 'undo-2', label: '撤回', action: `revokeMessage(${messageId});hideContextMenu();` });
            }
            if (!items.length) return;
            showContextMenu(event.clientX, event.clientY, items, { type: 'message', messageId });
            return;
        }
        const convItem = event.target.closest('.conv-item[data-conv-id]');
        if (convItem) {
            event.preventDefault();
            const convId = Number(convItem.dataset.convId);
            showContextMenu(event.clientX, event.clientY, [
                { icon: 'folder-open', label: '打开会话', action: `openConversation(${convId});hideContextMenu();` },
                { icon: 'refresh-cw', label: '刷新会话列表', action: 'loadConversations();hideContextMenu();' }
            ], { type: 'conversation', convId });
        }
    });
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
    setAvatarEl(document.getElementById('myAvatar'), null, null, currentUser.username);
    document.getElementById('myName').textContent = currentUser.username;

    await loadProfile();
    await loadConversations();
    await loadContacts();
    initSocket();
    bindContextMenus();
    renderIcons();

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
    selectedAvatarEmoji = userProfile.avatar_emoji || null;
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
    activeSidebarTab = tab;
    document.getElementById('nav-messages').classList.toggle('active', tab === 'chats');
    document.getElementById('nav-contacts').classList.toggle('active', tab === 'contacts');
    document.getElementById('nav-favorites').classList.toggle('active', tab === 'favorites');
    document.getElementById('panel-chats').style.display = tab === 'chats' ? 'flex' : 'none';
    const contactsPanel = document.getElementById('panel-contacts');
    const favoritesPanel = document.getElementById('panel-favorites');
    if (tab === 'contacts') {
        contactsPanel.style.display = 'flex';
        contactsPanel.style.flexDirection = 'column';
        contactsPanel.style.flex = '1';
        contactsPanel.style.overflow = 'hidden';
        loadContacts();
    } else {
        contactsPanel.style.display = 'none';
    }
    if (tab === 'favorites') {
        favoritesPanel.style.display = 'flex';
        favoritesPanel.style.flexDirection = 'column';
        favoritesPanel.style.flex = '1';
        loadFavorites();
    } else {
        favoritesPanel.style.display = 'none';
    }
}

// ===== Socket.IO =====
function initSocket() {
    socket = io();

    socket.on('connect', () => {
        if (currentConvId) {
            socket.emit('join_conversation', { conversation_id: currentConvId });
        }
    });

    socket.on('new_message', (msg) => {
        if (msg.conversation_id === currentConvId) {
            const autoScroll = shouldScroll();
            appendMessage(msg);
            if (msg.sender_id === currentUser.id || autoScroll) {
                scrollToBottom();
            }
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

    socket.on('message_revoked', (data) => {
        if (data.conversation_id !== currentConvId) return;
        const row = document.querySelector(`.msg-row[data-message-id="${data.message_id}"]`);
        if (!row) return;
        const senderName = data.sender_name || row.dataset.senderName || '该用户';
        renderRevokedMessageRow(row, senderName);
    });

    socket.on('message_edited', (msg) => {
        if (msg.conversation_id !== currentConvId) return;
        const row = document.querySelector(`.msg-row[data-message-id="${msg.id}"]`);
        if (!row) return;
        const bubble = row.querySelector('.msg-bubble');
        if (bubble) bubble.textContent = msg.content || '';
        const timeEl = row.querySelector('.msg-time');
        if (timeEl && !timeEl.querySelector('.msg-edited')) {
            const edited = document.createElement('span');
            edited.className = 'msg-edited';
            edited.textContent = '(已编辑)';
            timeEl.appendChild(edited);
        }
    });

    socket.on('group_updated', (data) => {
        if (data.conversation_id === currentConvId) {
            if (data.name) document.getElementById('chatTitle').textContent = data.name;
            if (Object.prototype.hasOwnProperty.call(data, 'announcement')) {
                const el = document.getElementById('groupAnnouncement');
                if (data.announcement) {
                    el.innerHTML = `<i class="icon" data-lucide="megaphone"></i><span>${escapeHtml(data.announcement)}</span>`;
                    el.style.display = 'flex';
                } else {
                    el.style.display = 'none';
                }
                renderIcons();
            }
        }
        loadConversations();
    });

    socket.on('pinned_updated', (data) => {
        if (data.conversation_id === currentConvId) loadPinnedMessages();
    });

    socket.on('friend_request', (data) => {
        const friendReqNotif = document.getElementById('friendReqNotifToggle');
        if (!friendReqNotif || friendReqNotif.checked) {
            showToast('新好友请求', `${data.from_name} 请求添加你为好友`, null, 'users');
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
        const lastMsg = c.last_message;
                const lastText = lastMsg
                        ? (lastMsg.is_revoked
                                ? `${lastMsg.sender_name} 撤回了一条消息`
                                : (isGroup && !isSelf ? `${lastMsg.sender_name}: ` : '') +
                                    (lastMsg.msg_type && lastMsg.msg_type !== 'text' ? `[${{'image':'图片','audio':'语音','video':'视频','file':'文件'}[lastMsg.msg_type] || '附件'}]` : lastMsg.content))
                        : '暂无消息';
        const lastTime = lastMsg ? formatTime(lastMsg.timestamp) : '';
        return `
            <div class="conv-item ${isActive ? 'active' : ''} ${isGroup ? 'group' : ''}"
                 data-conv-id="${c.id}" onclick="openConversation(${c.id})">
                ${conversationAvatarHtml(c)}
                <div class="conv-info">
                    <div class="conv-name">${escapeHtml(c.display_name)}</div>
                    <div class="conv-last">${escapeHtml(lastText)}</div>
                </div>
                <div class="conv-time">${lastTime}</div>
            </div>
        `;
    }).join('');
    renderIcons();
}

async function openConversation(convId) {
    currentConvId = convId;
    selectionMode = false;
    selectedMessageIds.clear();
    updateSelectionUI();
    const conv = conversations.find(c => c.id === convId);
    if (!conv) return;
    currentConvIsGroup = !!conv.is_group;

    document.getElementById('chatPlaceholder').style.display = 'none';
    document.getElementById('chatContainer').style.display = 'flex';
    const titleAvatarEl = document.getElementById('chatTitleAvatar');
    if (titleAvatarEl) {
        if (!conv.is_group) {
            if (conv.is_self_chat) {
                titleAvatarEl.innerHTML = escapeHtml(getNameFallback(currentUser.username));
            } else {
                const other = (conv.members || []).find(m => m.id !== currentUser.id) || {};
                if (other.avatar_url) {
                    titleAvatarEl.innerHTML = `<img src="${escapeAttr(other.avatar_url)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
                } else {
                    titleAvatarEl.innerHTML = escapeHtml(getAvatarToken(other.avatar_emoji, other.username || conv.display_name));
                }
            }
        } else if (conv.avatar_url) {
            titleAvatarEl.innerHTML = `<img src="${escapeAttr(conv.avatar_url)}" style="width:100%;height:100%;object-fit:cover;border-radius:50%">`;
        } else {
            titleAvatarEl.innerHTML = '群';
        }
    }
    document.getElementById('chatTitle').textContent = conv.display_name;

    const memberNames = conv.members.map(m => m.username).join(', ');
    document.getElementById('chatMembers').textContent =
        conv.is_group ? `${conv.members.length}人 · ${memberNames}` : '';
    const announcementEl = document.getElementById('groupAnnouncement');
    if (conv.is_group && conv.announcement) {
        announcementEl.innerHTML = `<i class="icon" data-lucide="megaphone"></i><span>${escapeHtml(conv.announcement)}</span>`;
        announcementEl.style.display = 'flex';
    } else {
        announcementEl.style.display = 'none';
    }

    // Show/hide group settings button
    document.getElementById('groupSettingsBtn').style.display = conv.is_group ? 'flex' : 'none';

    // Close group settings panel when switching conversations
    closeGroupSettings();

    socket.emit('join_conversation', { conversation_id: convId });
    await loadPinnedMessages();

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
    renderIcons();
    document.getElementById('msgInput').focus();
}

function updateSelectionUI() {
    const forwardBtn = document.getElementById('forwardSelectedBtn');
    if (!forwardBtn) return;
    forwardBtn.style.display = selectionMode ? 'inline-flex' : 'none';
    forwardBtn.title = selectionMode ? `转发选中(${selectedMessageIds.size})` : '转发选中';
}

function toggleSelectionMode() {
    selectionMode = !selectionMode;
    if (!selectionMode) selectedMessageIds.clear();
    document.querySelectorAll('.msg-row').forEach(row => {
        row.classList.toggle('selection-mode', selectionMode);
        row.classList.toggle('selected', selectedMessageIds.has(Number(row.dataset.messageId)));
    });
    updateSelectionUI();
}

function toggleMessageSelection(messageId) {
    if (!selectionMode) {
        selectionMode = true;
    }
    if (selectedMessageIds.has(messageId)) selectedMessageIds.delete(messageId);
    else selectedMessageIds.add(messageId);
    document.querySelectorAll('.msg-row').forEach(row => {
        row.classList.toggle('selection-mode', selectionMode);
        row.classList.toggle('selected', selectedMessageIds.has(Number(row.dataset.messageId)));
    });
    updateSelectionUI();
}

async function loadPinnedMessages() {
    if (!currentConvId) return;
    const strip = document.getElementById('pinnedStrip');
    const res = await fetch(`/api/conversations/${currentConvId}/pinned`);
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.items) || !data.items.length) {
        strip.style.display = 'none';
        pinnedMessageIds = new Set();
        return;
    }
    pinnedMessageIds = new Set(data.items.map(i => Number(i.message_id)));
    const top = data.items[0];
    const text = top.msg_type === 'text' ? (top.content || '') : `[${top.msg_type}] ${top.content || ''}`;
    strip.innerHTML = `
        <i class="icon" data-lucide="pin"></i>
        <span class="pin-item" onclick="jumpToMessage(${top.message_id})">${escapeHtml(text)}</span>
    `;
    strip.style.display = 'flex';
    renderIcons();
}

async function jumpToMessage(messageId) {
    let row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
    if (!row) {
        const ok = await ensureMessageVisible(messageId);
        if (!ok) return showSimpleToast('无法找到该消息', 'error');
        row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
    }
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('selected');
    setTimeout(() => row.classList.remove('selected'), 1500);
}

async function pinMessage(messageId) {
    if (!currentConvId) return;
    const res = await fetch(`/api/conversations/${currentConvId}/pinned/${messageId}`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) return showSimpleToast(data.msg || '置顶失败', 'error');
    await loadPinnedMessages();
    showSimpleToast('已置顶消息', 'success');
}

async function unpinMessage(messageId) {
    if (!currentConvId) return;
    const res = await fetch(`/api/conversations/${currentConvId}/pinned/${messageId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) return showSimpleToast(data.msg || '取消置顶失败', 'error');
    await loadPinnedMessages();
    showSimpleToast('已取消置顶', 'success');
}

function pickForwardConversations() {
    const options = conversations.map(c => `${c.id}:${c.display_name}`).join('\n');
    const input = window.prompt(`输入要转发到的会话ID（逗号分隔）:\n${options}`);
    if (!input) return [];
    return input.split(',').map(v => Number(v.trim())).filter(v => Number.isInteger(v) && v > 0);
}

async function forwardMessage(messageId) {
    const conversationIds = pickForwardConversations();
    if (!conversationIds.length) return;
    const res = await fetch(`/api/messages/${messageId}/forward`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_ids: conversationIds })
    });
    const data = await res.json();
    if (!data.ok) {
        showSimpleToast(data.msg || '转发失败', 'error');
        return;
    }
    showSimpleToast(`已转发 ${data.forwarded} 个会话`, 'success');
    loadConversations();
}

async function forwardSelectedMessages() {
    if (!selectedMessageIds.size) {
        showSimpleToast('请先选择消息', 'error');
        return;
    }
    const conversationIds = pickForwardConversations();
    if (!conversationIds.length) return;
    let total = 0;
    for (const messageId of selectedMessageIds) {
        const res = await fetch(`/api/messages/${messageId}/forward`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_ids: conversationIds })
        });
        const data = await res.json();
        if (data.ok) total += data.forwarded;
    }
    selectedMessageIds.clear();
    selectionMode = false;
    updateSelectionUI();
    document.querySelectorAll('.msg-row').forEach(row => row.classList.remove('selection-mode', 'selected'));
    showSimpleToast(`多选转发完成，共 ${total} 条`, 'success');
    loadConversations();
}

async function revokeMessage(messageId) {
    const res = await fetch(`/api/messages/${messageId}/revoke`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) {
        showSimpleToast(data.msg || '撤回失败', 'error');
        return;
    }
    const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
    if (row) {
        renderRevokedMessageRow(row, currentUser.username);
    }
}

async function editMessage(messageId) {
    const row = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
    if (!row) return;
    const oldText = row.querySelector('.msg-bubble')?.textContent || '';
    const next = window.prompt('编辑消息', oldText);
    if (next === null) return;
    const content = next.trim();
    if (!content) return;
    const res = await fetch(`/api/messages/${messageId}/edit`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content })
    });
    const data = await res.json();
    if (!data.ok) {
        showSimpleToast(data.msg || '编辑失败', 'error');
        return;
    }
    if (data.message) {
        const bubble = row.querySelector('.msg-bubble');
        if (bubble) bubble.textContent = data.message.content;
    }
}

async function toggleFavoriteMessage(messageId) {
    const res = await fetch(`/api/messages/${messageId}/favorite`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) {
        showSimpleToast(data.msg || '收藏操作失败', 'error');
        return;
    }
    showSimpleToast(data.favorited ? '已收藏' : '已取消收藏', 'success');
    if (activeSidebarTab === 'favorites') {
        loadFavorites();
    }
}

function resetFavoritesPagination() {
    favoritesCursor = null;
    favoritesHasMore = false;
}

function updateFavoritesLoadMoreBtn() {
    const btn = document.getElementById('favoritesLoadMoreBtn');
    if (!btn) return;
    btn.style.display = favoritesHasMore ? 'block' : 'none';
    btn.disabled = favoritesLoading;
    btn.textContent = favoritesLoading ? '加载中...' : '加载更多收藏';
}

function resolveConversationName(convId, fallback = '') {
    const conv = conversations.find(c => c.id === convId);
    if (conv) return conv.display_name;
    return fallback || `会话 #${convId}`;
}

function favoritePreviewHtml(msg) {
    if (msg.is_revoked) {
        return `<div class="favorite-body revoked">${escapeHtml(msg.sender_name || '该用户')} 撤回了一条消息</div>`;
    }
    const type = msg.msg_type || 'text';
    if (type === 'image' && msg.media_url) {
        return `<div class="favorite-body"><img src="${escapeAttr(msg.media_url)}" class="favorite-image" alt="图片" onclick="window.open('${escapeAttr(msg.media_url)}','_blank')"></div>`;
    }
    if (type === 'audio' && msg.media_url) {
        return `<div class="favorite-body"><audio controls src="${escapeAttr(msg.media_url)}" class="favorite-audio"></audio></div>`;
    }
    if (type === 'video' && msg.media_url) {
        return `<div class="favorite-body"><video controls src="${escapeAttr(msg.media_url)}" class="favorite-image" style="max-height:180px"></video></div>`;
    }
    if (type === 'file' && msg.media_url) {
        return `<div class="favorite-body"><a href="${escapeAttr(msg.media_url)}" download class="msg-file">${escapeHtml(msg.content || '下载文件')}</a></div>`;
    }
    return `<div class="favorite-body">${escapeHtml(msg.content || '')}</div>`;
}

async function loadFavorites(options = {}) {
    const append = !!options.append;
    if (favoritesLoading) return;
    if (!append) {
        resetFavoritesPagination();
    } else if (!favoritesHasMore) {
        return;
    }

    favoritesLoading = true;
    updateFavoritesLoadMoreBtn();

    const query = new URLSearchParams({ limit: String(FAVORITES_PAGE_SIZE) });
    if (append && favoritesCursor !== null) query.set('before', String(favoritesCursor));

    const res = await fetch(`/api/favorites?${query.toString()}`);
    const data = await res.json();
    favoritesLoading = false;
    if (!data.ok) {
        updateFavoritesLoadMoreBtn();
        return;
    }

    favoritesHasMore = !!data.has_more;
    favoritesCursor = data.next_before ?? null;

    const list = document.getElementById('favoritesList');
    if (!append && !data.messages.length) {
        list.innerHTML = '<div class="empty-hint">暂无收藏消息</div>';
        updateFavoritesLoadMoreBtn();
        return;
    }

    const html = data.messages.map(m => `
        <div class="favorite-item" data-conv-id="${m.conversation_id}" data-msg-id="${m.id}">
            <div class="favorite-head">
                <span class="favorite-conv">${escapeHtml(resolveConversationName(m.conversation_id, m.conversation_name || '会话'))}</span>
                <span class="favorite-time">${formatTime(m.favorited_at || m.timestamp)}</span>
            </div>
            <div class="favorite-meta">${escapeHtml(m.sender_name)} · 收藏于 ${new Date((m.favorited_at || m.timestamp) * 1000).toLocaleString()}</div>
            ${favoritePreviewHtml(m)}
            <div class="favorite-actions">
                <button class="contact-btn chat" onclick="openFavoriteMessage(${m.conversation_id}, ${m.id})">定位到原聊天</button>
            </div>
        </div>
    `).join('');

    if (append) {
        list.insertAdjacentHTML('beforeend', html);
    } else {
        list.innerHTML = html;
    }

    updateFavoritesLoadMoreBtn();
    renderIcons();
}

async function loadFavoritesMore() {
    await loadFavorites({ append: true });
}

async function ensureMessageVisible(messageId, maxRounds = 20) {
    for (let i = 0; i < maxRounds; i += 1) {
        const existing = document.querySelector(`.msg-row[data-message-id="${messageId}"]`);
        if (existing) return true;
        const loaded = await loadMore();
        if (!loaded) return false;
    }
    return false;
}

async function openFavoriteMessage(convId, messageId) {
    switchSidebarTab('chats');
    await loadConversations();
    await openConversation(convId);
    if (!messageId) return;
    await ensureMessageVisible(messageId);
    jumpToMessage(messageId);
}

async function loadMore() {
    if (!currentConvId) return false;
    const firstMsg = document.querySelector('.msg-row');
    if (!firstMsg) return false;
    const firstTimestamp = firstMsg.dataset.timestamp;

    const res = await fetch(`/api/messages/${currentConvId}?before=${firstTimestamp}`);
    const data = await res.json();
    if (!data.ok || !data.messages.length) {
        document.getElementById('loadMoreBtn').style.display = 'none';
        return false;
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
    return true;
}

// ===== Messages =====
function appendMessage(msg, animate = true) {
    const msgContainer = document.getElementById('messages');
    const row = createMessageElement(msg, animate);
    msgContainer.appendChild(row);
}

function renderRevokedMessageRow(row, senderName) {
    row.classList.remove('mine', 'other', 'selected');
    row.classList.add('revoked');
    row.dataset.revoked = '1';
    row.innerHTML = `<div class="msg-revoked-note">${escapeHtml(senderName || '该用户')} 撤回了一条消息</div>`;
}

function createMessageElement(msg, animate = true) {
    const isMine = msg.sender_id === currentUser.id;
    const row = document.createElement('div');
    row.className = `msg-row ${isMine ? 'mine' : 'other'}`;
    row.dataset.messageId = msg.id;
    row.dataset.conversationId = msg.conversation_id;
    row.dataset.timestamp = msg.timestamp;
    row.dataset.msgType = msg.msg_type || 'text';
    row.dataset.senderName = msg.sender_name || '';
    row.dataset.revoked = msg.is_revoked ? '1' : '0';
    if (!animate) row.style.animation = 'none';

    if (msg.is_revoked) {
        renderRevokedMessageRow(row, isMine ? '你' : msg.sender_name);
        return row;
    }

    const senderHtml = !isMine ? `<div class="msg-sender">${escapeHtml(msg.sender_name)}</div>` : '';
    const timeStr = new Date(msg.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    let replyHtml = '';
    if (msg.original_message_id) {
        let content = msg.original_content || '...';
        const oType = msg.original_msg_type;
        if (oType === 'image') content = '[图片]';
        else if (oType === 'audio') content = '[语音]';
        else if (oType === 'video') content = '[视频]';
        else if (oType === 'file') content = '[文件]';

        replyHtml = `
            <div class="msg-reply" onclick="jumpToMessage(${msg.original_message_id})">
                <div class="msg-reply-user">${escapeHtml(msg.original_sender_name || '未知用户')}</div>
                <div class="msg-reply-text">${escapeHtml(content)}</div>
            </div>
        `;
    }

    let bubbleContent;
    const msgType = msg.msg_type || 'text';
    if (msgType === 'image' && msg.media_url) {
        bubbleContent = `<img src="${escapeAttr(msg.media_url)}" class="msg-image" alt="图片"
            onclick="window.open('${escapeAttr(msg.media_url)}','_blank')">`;
    } else if (msgType === 'audio' && msg.media_url) {
        bubbleContent = `<audio controls src="${escapeAttr(msg.media_url)}" class="msg-audio"></audio>`;
    } else if (msgType === 'video' && msg.media_url) {
        bubbleContent = `<video controls src="${escapeAttr(msg.media_url)}" class="msg-image" style="max-height:200px"></video>`;
    } else if (msgType === 'file' && msg.media_url) {
        bubbleContent = `<a href="${escapeAttr(msg.media_url)}" download class="msg-file">${escapeHtml(msg.content)}</a>`;
    } else {
        bubbleContent = escapeHtml(msg.content);
    }

    const editedTag = msg.edited_at ? '<span class="msg-edited">(已编辑)</span>' : '';
    row.innerHTML = `
        <input type="checkbox" class="msg-select" ${selectedMessageIds.has(msg.id) ? 'checked' : ''}
               onchange="toggleMessageSelection(${msg.id})">
        ${senderHtml}
        <div class="msg-bubble">${replyHtml}${bubbleContent}</div>
        <div class="msg-time">${timeStr}${editedTag}</div>
    `;
    if (selectionMode) {
        row.classList.add('selection-mode');
    }
    if (selectedMessageIds.has(msg.id)) {
        row.classList.add('selected');
    }
    renderIcons();
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

function shouldScroll() {
    const el = document.getElementById('messages');
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 100;
}

// ===== Send =====
function sendMessage() {
    const input = document.getElementById('msgInput');
    const content = input.value.trim();
    if (!content || !currentConvId) return;
    socket.emit('send_message', { 
        conversation_id: currentConvId, 
        content,
        original_message_id: replyToId
    });
    input.value = '';
    input.style.height = 'auto';
    input.focus();
    cancelReply();
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
const searchTimers = {
    private: null,
    group: null,
    friend: null,
};

async function searchUsers() {
    const q = document.getElementById('searchUserInput').value.trim();
    const container = document.getElementById('searchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimers.private);
    searchTimers.private = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        container.innerHTML = data.users.map(u => `
            <div class="search-item">
                ${userAvatarHtml(u)}
                <span>${escapeHtml(u.username)}</span>
                ${u.relation === 'self'
                    ? `<button class="contact-btn chat" style="margin-left:auto" onclick="startPrivateChat(${u.id})">和自己聊天</button>`
                    : (u.can_chat
                        ? `<button class="contact-btn chat" style="margin-left:auto" onclick="startPrivateChat(${u.id})">发消息</button>`
                        : (u.relation === 'pending_out'
                            ? `<span style="margin-left:auto;font-size:12px;color:var(--text-3)">待对方审核</span>`
                            : (u.relation === 'pending_in'
                                ? `<button class="contact-btn accept" style="margin-left:auto" onclick="showModal('friendReview')">去审核</button>`
                                : `<button class="contact-btn chat" style="margin-left:auto" onclick="sendFriendRequest(${u.id}, this)">添加好友</button>`)))}
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
        renderIcons();
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
    } else {
        showSimpleToast(data.msg || '无法发起私聊', 'error');
    }
}

async function searchGroupUsers() {
    const q = document.getElementById('searchGroupUserInput').value.trim();
    const container = document.getElementById('groupSearchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimers.group);
    searchTimers.group = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        const filtered = data.users.filter(u => u.id !== currentUser.id && !selectedGroupMembers.find(m => m.id === u.id));
        container.innerHTML = filtered.map(u => `
            <div class="search-item" onclick="addGroupMember(${u.id}, '${encodeURIComponent(u.username)}')">
                ${userAvatarHtml(u)}
                <span>${escapeHtml(u.username)}</span>
                <span style="margin-left:auto;color:#667eea;font-size:18px">+</span>
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
        renderIcons();
    }, 300);
}

function addGroupMember(id, encodedUsername) {
    const username = decodeURIComponent(encodedUsername);
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
    if (badge) {
        if (data.pending_count > 0) {
            badge.textContent = data.pending_count;
            badge.style.display = 'inline-flex';
        } else {
            badge.style.display = 'none';
        }
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
                ${userAvatarHtml(r, 'avatar', 'width:34px;height:34px;font-size:13px')}
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
    renderIcons();
}

async function loadFriendReview() {
    const res = await fetch('/api/contacts/review');
    const data = await res.json();
    if (!data.ok) return;
    const incomingEl = document.getElementById('friendReviewIncoming');
    const outgoingEl = document.getElementById('friendReviewOutgoing');
    incomingEl.innerHTML = (data.incoming || []).map(r => `
        <div class="contact-item">
            ${userAvatarHtml(r, 'avatar', 'width:34px;height:34px;font-size:13px')}
            <span class="contact-name">${escapeHtml(r.username)}</span>
            <div class="contact-actions" style="opacity:1">
                <button class="contact-btn accept" onclick="acceptRequest(${r.id});loadFriendReview()">同意</button>
                <button class="contact-btn reject" onclick="rejectRequest(${r.id});loadFriendReview()">拒绝</button>
            </div>
        </div>
    `).join('') || '<div class="empty-hint" style="padding:14px">暂无待审核请求</div>';
    outgoingEl.innerHTML = (data.outgoing || []).map(r => `
        <div class="contact-item">
            ${userAvatarHtml(r, 'avatar', 'width:34px;height:34px;font-size:13px')}
            <span class="contact-name">${escapeHtml(r.username)}</span>
            <div class="contact-actions" style="opacity:1">
                <span style="font-size:12px;color:var(--text-3)">等待对方处理</span>
            </div>
        </div>
    `).join('') || '<div class="empty-hint" style="padding:14px">暂无已发出的待处理申请</div>';
}

function renderFriendsList(friends) {
    const list = document.getElementById('friendsList');
    if (!friends.length) {
        list.innerHTML = '<div class="empty-hint">暂无好友，点击上方添加</div>';
        return;
    }
    list.innerHTML = friends.map(f => `
        <div class="contact-item">
            ${userAvatarHtml(f, 'avatar', 'width:34px;height:34px;font-size:13px')}
            <span class="contact-name">${escapeHtml(f.username)}</span>
            <div class="contact-actions">
                <button class="contact-btn chat" onclick="chatWithFriend(${f.id})">发消息</button>
                <button class="contact-btn remove" onclick="removeFriend(${f.id}, '${encodeURIComponent(f.username)}')">删除</button>
            </div>
        </div>
    `).join('');
    renderIcons();
}

async function searchFriendUsers() {
    const q = document.getElementById('searchFriendInput').value.trim();
    const container = document.getElementById('friendSearchResults');
    if (!q) { container.innerHTML = ''; return; }

    clearTimeout(searchTimers.friend);
    searchTimers.friend = setTimeout(async () => {
        const res = await fetch(`/api/users/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!data.ok) return;
        container.innerHTML = data.users.map(u => `
            <div class="search-item">
                ${userAvatarHtml(u)}
                <span>${escapeHtml(u.username)}</span>
                ${u.relation === 'self'
                    ? '<span style="margin-left:auto;font-size:12px;color:var(--text-3)">这是你自己</span>'
                    : (u.relation === 'friend'
                        ? `<button style="margin-left:auto" class="contact-btn chat" onclick="chatWithFriend(${u.id})">发消息</button>`
                        : (u.relation === 'pending_out'
                            ? '<span style="margin-left:auto;font-size:12px;color:var(--text-3)">等待审核</span>'
                            : (u.relation === 'pending_in'
                                ? '<span style="margin-left:auto;font-size:12px;color:var(--text-3)">待你审核</span>'
                                : `<button style="margin-left:auto" class="contact-btn chat" onclick="sendFriendRequest(${u.id}, this)">添加好友</button>`)))}
            </div>
        `).join('') || '<div class="empty-hint">未找到用户</div>';
        renderIcons();
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
        showSimpleToast(data.msg || '发送失败', 'error');
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

async function removeFriend(friendId, encodedName) {
    const name = decodeURIComponent(encodedName);
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
                <input type="text" id="gspGroupName" value="${escapeAttr(s.name || '')}"
                       ${isAdmin ? '' : 'readonly'} placeholder="群名称">
                ${isAdmin ? `<button class="gsp-btn" onclick="saveGroupName()">保存</button>` : ''}
            </div>
        </div>

        <div class="gsp-section">
            <div class="gsp-section-title">群头像</div>
            <div style="display:flex;align-items:center;gap:10px">
                ${s.avatar_url
                    ? `<img src="${escapeAttr(s.avatar_url)}" style="width:40px;height:40px;border-radius:50%;object-fit:cover">`
                    : `<div class="mini-av" style="width:40px;height:40px">群</div>`}
                ${isAdmin
                    ? `<label class="avatar-upload-btn">上传群头像
                           <input type="file" style="display:none" accept="image/*" onchange="uploadGroupAvatar(this)">
                       </label>
                       <button class="avatar-upload-btn danger" onclick="clearGroupAvatar()">恢复默认</button>`
                    : ''}
            </div>
        </div>

        <div class="gsp-section">
            <div class="gsp-section-title">群公告</div>
            <textarea id="gspAnnouncement" class="settings-textarea" maxlength="200" ${isAdmin ? '' : 'readonly'}
                      placeholder="输入群公告（最多200字）">${escapeHtml(s.announcement || '')}</textarea>
            ${isAdmin ? '<button class="gsp-btn" style="margin-top:8px" onclick="saveGroupAnnouncement()">保存公告</button>' : ''}
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
                                        ${m.avatar_url
                                                ? `<img src="${escapeAttr(m.avatar_url)}" class="mini-av" style="object-fit:cover">`
                                                : `<div class="mini-av">${escapeHtml(getAvatarToken(m.avatar_emoji, m.username))}</div>`}
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
    renderIcons();
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
        container.innerHTML = data.users.filter(u => u.id !== currentUser.id).map(u => `
            <div class="gsp-member" style="cursor:pointer;border-radius:7px;padding:5px 4px"
                 onclick="selectGspMember(${u.id}, '${escapeHtml(u.username)}', this)">
                ${u.avatar_url
                    ? `<img src="${escapeAttr(u.avatar_url)}" class="mini-av" style="object-fit:cover">`
                    : `<div class="mini-av">${escapeHtml(getAvatarToken(u.avatar_emoji, u.username))}</div>`}
                <div class="member-name">${escapeHtml(u.username)}</div>
            </div>
        `).join('') || '<div style="font-size:12px;color:var(--text-3);padding:4px">未找到用户</div>';
    }, 300);
}

async function saveGroupAnnouncement() {
    const announcement = (document.getElementById('gspAnnouncement')?.value || '').trim();
    const res = await fetch(`/api/conversations/${currentConvId}/announcement`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ announcement })
    });
    const data = await res.json();
    if (!data.ok) return showSimpleToast(data.msg || '公告保存失败', 'error');
    showSimpleToast('群公告已更新', 'success');
    await loadConversations();
}

async function uploadGroupAvatar(input) {
    if (!input.files?.length) return;
    const formData = new FormData();
    formData.append('file', input.files[0]);
    const uploadRes = await fetch('/api/upload', { method: 'POST', body: formData });
    const uploadData = await uploadRes.json();
    if (!uploadData.ok) return showSimpleToast(uploadData.msg || '上传失败', 'error');
    const res = await fetch(`/api/conversations/${currentConvId}/avatar`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_url: uploadData.url })
    });
    const data = await res.json();
    if (!data.ok) return showSimpleToast(data.msg || '更新群头像失败', 'error');
    showSimpleToast('群头像已更新', 'success');
    await loadConversations();
    await renderGroupSettingsPanel();
}

async function clearGroupAvatar() {
    const res = await fetch(`/api/conversations/${currentConvId}/avatar`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ avatar_url: '' })
    });
    const data = await res.json();
    if (!data.ok) return showSimpleToast(data.msg || '恢复默认头像失败', 'error');
    showSimpleToast('已恢复默认群头像', 'success');
    await loadConversations();
    await renderGroupSettingsPanel();
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
        showSimpleToast(data.msg || '添加成员失败', 'error');
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
        showSimpleToast(data.msg || '保存失败', 'error');
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
        showSimpleToast(data.msg || '移除失败', 'error');
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
        showSimpleToast(data.msg || '设置失败', 'error');
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
        showSimpleToast(data.msg || '退出失败', 'error');
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
        showSimpleToast(data.msg || '转让失败', 'error');
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
    hideModal(null, 'newChat');
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
function toggleRecordMode() {
    recordMode = !recordMode;
    const input = document.getElementById('msgInput');
    const holdBtn = document.getElementById('holdRecordBtn');
    const sendBtn = document.querySelector('.send-btn');
    const speechBtn = document.getElementById('speechBtn');
    const recordBtn = document.getElementById('recordBtn');
    input.style.display = recordMode ? 'none' : 'block';
    holdBtn.style.display = recordMode ? 'block' : 'none';
    sendBtn.style.display = recordMode ? 'none' : 'inline-flex';
    speechBtn.style.display = recordMode ? 'none' : 'inline-flex';
    recordBtn.title = recordMode ? '切换输入模式' : '录音模式';
    recordBtn.innerHTML = `<i data-lucide="${recordMode ? 'keyboard' : 'mic'}"></i>`;
    renderIcons();
    if (!recordMode && isRecording) stopRecordingInternal();
}

async function startRecordingInternal() {
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
        const holdBtn = document.getElementById('holdRecordBtn');
        holdBtn.classList.add('recording');
        holdBtn.textContent = '松开发送语音';
    } catch (err) {
        showSimpleToast('无法访问麦克风: ' + err.message, 'error');
    }
}

function stopRecordingInternal() {
    if (!isRecording || !mediaRecorder) return;
    mediaRecorder.stop();
    isRecording = false;
    const holdBtn = document.getElementById('holdRecordBtn');
    holdBtn.classList.remove('recording');
    holdBtn.textContent = '按住录音';
}

function startHoldRecord(e) {
    e.preventDefault();
    if (!recordMode || isRecording) return;
    startRecordingInternal();
}

function stopHoldRecord(e) {
    e.preventDefault();
    if (!recordMode || !isRecording) return;
    stopRecordingInternal();
}

function cancelHoldRecord(e) {
    e.preventDefault();
    if (!recordMode || !isRecording) return;
    stopRecordingInternal();
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
    if (name === 'friendReview') {
        loadFriendReview();
    }
    renderIcons();
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
        <div><i data-lucide="${icon || 'message-square'}"></i></div>
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
    renderIcons();
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
    const value = String(str ?? '');
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttr(str) {
    return escapeHtml(str);
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

