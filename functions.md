# 项目架构与功能实现说明

## 🏗️ 技术栈

| 层 | 技术 |
|---|------|
| 后端框架 | Flask |
| 实时通信 | Flask-SocketIO（WebSocket） |
| 安全防护 | Flask-Limiter (速率限制) |
| 本地日志 | Python logging (RotatingFileHandler) |
| 数据库 | SQLite3（WAL 模式，10s 超时） |
| 密码哈希 | argon2id（用户密码 + 管理员密码） |
| 生产 WSGI | gevent + gevent-websocket |
| 前端 | 原生 HTML/CSS/JS + Socket.IO 客户端 |

---

## 1. 🔐 用户认证（注册/登录）

**流程：**
- **注册**：`POST /api/register` → 校验用户名长度(2-20)、密码长度(≥4)及用户名只能含字母数字 → `argon2id` 哈希密码 → 写入 `users` 表
- **登录**：`POST /api/login` → 速率限制 (20/min) → `argon2.verify()` 验证 → 写入 Flask `session`
- **封禁检查**：`before_request` 钩子中每个 `/api/` 请求都会检查 `is_banned` 字段，被封禁用户立即清除 session 并返回 403
- **注册开关**：管理员可通过 `system_settings` 表的 `registration_enabled` 关闭注册

---

## 2. 💬 会话系统（私聊/群聊/备忘录）

数据库有三张核心表：
- `conversations` — 会话（字段 `is_group`, `is_self_chat` 区分类型）
- `conversation_members` — 成员关系（含 `role`: admin/member）
- `messages` — 消息

**三种会话类型：**

| 类型 | `is_group` | `is_self_chat` | 创建方式 |
|------|-----------|----------------|---------|
| 私聊 | 0 | 0 | `create_private_conversation()` — 用 `BEGIN IMMEDIATE` 防竞态；创建后 WebSocket 通知对方刷新列表 |
| 群聊 | 1 | 0 | `create_group_conversation()` — 创建者自动获得 `admin` 角色 |
| 备忘录 | 0 | 1 | `create_self_conversation()` — 同样用 `BEGIN IMMEDIATE` 保证唯一 |

**权限控制：**
- 读消息前检查 `is_member(conv_id, user_id)`
- 群管理操作（改名/加人/踢人）需 `role='admin'` 或群主（`created_by`）
- 群主转让：`transfer_group_owner()` — 旧群主降为 member，新群主升为 admin
- 群主不能退群，必须先转让
- **私聊限制**：仅好友之间可发起私聊（`create_private` 检查 `can_start_private_chat`）

---

## 3. ⚡ 实时消息（WebSocket）

**机制：**
```
客户端 Socket.IO  ←→  Flask-SocketIO 服务端
```

- **连接时**（`on_connect`）：将用户 `sid` 记入 `online_users` 字典，并 `join_room` 加入所有会话房间（`conv_{id}`）
- **会话更新**：收到 `conversation_created` 事件后，客户端自动 `join_conversation` 加入新房间并刷新列表
- **发消息**（`on_send`）：
  1. 校验：是否登录 → 是否被封禁 → `msg_type` 白名单 → `media_url` 所有权及路径验证 → 是否是会话成员 → 文本长度限制
  2. `db.save_message()` 写入数据库
  3. `emit('new_message', ..., room=f'conv_{id}')` 广播给房间内所有成员
    4. **私聊创建通知**：创建私聊时向目标用户广播 `conversation_created`，对方前端收到后自动 `join_room` 并刷新列表
- **断开时**（`on_disconnect`）：从 `online_users` 移除 sid

**前端**：收到 `new_message` 事件后追加消息气泡并滚动到底部。

---

## 4. 🔔 消息弹出通知

前端实现（`chat.js`）：
- 全局变量 `notificationsEnabled` 控制开关
- 收到 `new_message` 时，如果消息不是自己发的，且当前不在该会话或页面不可见，则调用 `showToast()` 在右上角显示弹出通知
- Toast 5秒自动消失，点击可跳转到对应会话
- 通过侧边栏 🔔 按钮切换开关

---

## 5. 📁 文件上传与存储配额

**上传流程：**
```
前端选文件 → POST /api/upload → 服务端校验大小/类型 → uuid重命名存盘
→ BEGIN IMMEDIATE 原子配额检查+记录 → 返回 URL
→ 前端通过 WebSocket send_message 发送 media_url
```

**安全措施：**
- 文件名用 `uuid4().hex` 重命名，防止路径遍历
- `_save_upload` 校验 `sub_dir` 不含 `..`/`/`/`\`
- SVG 已从允许列表移除（防 XSS）
- `media_url` 在 `send_message` 中三重校验：前缀必须是 `/static/uploads/`、不含 `..`、数据库中有当前用户的所有权记录

**存储配额系统：**
- `user_files` 表记录每个用户的每个上传文件及大小
- `record_file_upload()` 使用 `BEGIN IMMEDIATE` 事务原子地做 `SUM(file_size)` + 配额比对 + INSERT
- 配额优先级：用户个人配额 (`user_profiles.storage_quota_mb`) > 全局默认配额 (`system_settings.default_storage_quota_mb`，默认10GB)

---

## 6. 👤 个人资料与设置

`user_profiles` 表存储：
- `avatar_emoji` — 默认 😊，前端显示为头像
- `avatar_url` — 上传的自定义头像图片路径（写入前校验路径合法性+所有权）
- `bio` — 个性签名（≤100字符）
- `theme` — 主题（light/dark）
- `font_size` — 字体大小（small/medium/large）

修改密码：`change_password()` → argon2 验证旧密码 → 哈希新密码 → 更新数据库 → 清除 session 强制重新登录

---

## 7. 👥 好友系统

**数据结构：** `friends` 表，标准化存储 `(min(A,B), max(A,B))`，UNIQUE 约束防并发重复。`initiated_by` 记录谁发起的请求。

| 操作 | 实现 |
|------|------|
| 发请求 | `send_friend_request()` — 如果对方已向你发请求则自动同意 |
| 接受 | `accept_friend_request()` — 校验 `initiated_by != user_id`（只有被请求方能接受） |
| 拒绝 | `reject_friend_request()` — 删除记录 |
| 删好友 | `remove_friend()` — 直接删 |
| 实时通知 | 发请求成功后通过 `online_users` 查找目标 sid，emit `friend_request` 事件 |

管理员可通过 `system_settings.allow_friend_requests` 全局关闭好友功能。

---

## 8. 🔒 管理后台

**认证：** 独立于用户系统，密码存在 `admin_config.json`（argon2id 哈希），通过 `admin_setup.py` 交互式设置。登录后 session 设置 `is_admin=True`，`require_admin` 装饰器保护所有管理 API。

**功能：**

| 功能 | API | 说明 |
|------|-----|------|
| 用户列表 | `GET /api/admin/users` | 含 ID、用户名、注册时间、封禁状态、存储用量 |
| 创建用户 | `POST /api/admin/users` | 管理员手动创建 |
| 删除用户 | `DELETE /api/admin/users/:id` | 自动转让群主、清理孤立会话/消息 |
| 封禁/解封 | `PUT /api/admin/users/:id/ban` | 设置 `is_banned`，生效后被封用户所有 API 立即被拦截 |
| 设置配额 | `PUT /api/admin/users/:id/quota` | 单用户存储配额覆盖 |
| 群聊管理 | `GET/PUT/DELETE /api/admin/groups` | 查看、改名、删除群聊 |
| 系统设置 | `PUT /api/admin/system-settings` | 注册开关、消息长度上限、系统名称、好友开关、默认配额 |
| 统计面板 | `GET /api/admin/stats` | 用户数、群数、消息数、24h 活跃用户 |

---

## 9. 🗄️ 数据库管理

- **连接管理**：`db_conn()` 上下文管理器保证每次操作后自动关闭连接
- **初始化**：`init_db()` 使用双重检查锁（`_db_init_lock` + `_db_initialized`）保证只执行一次
- **迁移系统**：`system_settings.db_version` 记录版本号，`_init_db_locked()` 中按版本号运行增量迁移
- **幂等列添加**：`_safe_add_column()` 用 try/except 忽略已存在的列，保证重复启动不报错

---

## 10. 📝 本地日志

使用 `RotatingFileHandler` 记录关键操作到 `logs/app.log`：
- **格式**：`TIME LEVEL: MESSAGE [in FILE:LINE]`
- **轮转**：单文件最大 1MB，保留 10 个备份
- **记录内容**：
  - 系统启动
  - 用户注册/登录（成功/失败）
  - 管理员登录/操作（删除用户、封禁用户）
  - 群聊创建
  - 消息撤回
