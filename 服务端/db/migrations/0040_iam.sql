-- ============================================================================
-- 0040 · 用户体系：角色 / 账号 / 会话
-- ----------------------------------------------------------------------------
-- 背景（2026-10-25）：「用户与角色」原来是前端 localStorage 里的演示配置，
-- 换个浏览器、清一次缓存就回到出厂值，客户问起来只能说「那不是真功能」。
-- 现在落成三张真表：
--
--   sys_role     角色与菜单权限（perms 存菜单 key 数组，与前端 navTree 同源）
--   sys_user     账号，含 pbkdf2 密码摘要、角色、部门、启用状态、最后登录
--   sys_session  登录会话（HttpOnly Cookie 里的 token 对应这张表的一行）
--
-- 【密码为什么是 pbkdf2 而不是 sha256】
--   sha256 是快哈希，离线撞库一秒能试几亿次；pbkdf2 带盐 + 60000 轮，
--   同一份摘要泄露出去，暴力成本差好几个数量级。LLM 的 Key 用可逆加密
--   （业务上必须拿回来去调厂商），登录密码**只要校验不要还原**，所以单向摘要。
--
-- 【会话为什么放数据库】
--   放内存里，后端一重启所有人掉线，而且多进程部署时对不上号。
--   放表里，重启、迁移、查「谁现在还登录着」都有据可查。
--   过期策略：12 小时（见 app/iam_auth.py 的 SESSION_TTL_HOURS）。
--
-- 【为什么 perms 用 JSONB 而不是关联表】
--   菜单权限是「一个角色挂十几二十个菜单 key」，读取永远是整包读、整包写，
--   拆成关联表只是把读与写都变复杂，并不能多回答任何一个问题。
--
-- 本文件幂等：ON CONFLICT DO NOTHING，重跑不会把已改过的配置冲掉。
-- 改结构请新建 0041，不要改本文件（执行器会按 sha256 拦下）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS sys_role (
    role_key     TEXT PRIMARY KEY,
    role_name    TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    data_scope   TEXT NOT NULL DEFAULT 'self'
                 CHECK (data_scope IN ('all', 'dept', 'self')),
    perms        JSONB NOT NULL DEFAULT '[]'::jsonb,
    builtin      BOOLEAN NOT NULL DEFAULT FALSE,
    home         TEXT NOT NULL DEFAULT '/dash',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sys_role IS '角色与菜单权限。perms 为菜单 key 数组（与前端 navTree 同源），整包读写';
COMMENT ON COLUMN sys_role.data_scope IS '数据权限：all 全部 / dept 本部门 / self 仅本人';
COMMENT ON COLUMN sys_role.builtin IS '内置角色不可删除；系统管理员 admin 的权限矩阵在页面上只读，防止自锁';
COMMENT ON COLUMN sys_role.home IS '登录后落地页，失去该页权限时前端自动退到第一个有权限的菜单';

CREATE TABLE IF NOT EXISTS sys_user (
    account      TEXT PRIMARY KEY,
    user_name    TEXT NOT NULL,
    phone_no     TEXT NOT NULL DEFAULT '',
    dept         TEXT NOT NULL DEFAULT '',
    role_key     TEXT NOT NULL REFERENCES sys_role(role_key) ON UPDATE CASCADE,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    pwd_hash     TEXT NOT NULL,
    last_login_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sys_user IS '登录账号。密码只存 pbkdf2 摘要，任何读接口都不返回该列';
COMMENT ON COLUMN sys_user.pwd_hash IS 'pbkdf2:迭代次数:盐:hex，见 app/iam_auth.py；不回显';

CREATE TABLE IF NOT EXISTS sys_session (
    token        TEXT PRIMARY KEY,
    account      TEXT NOT NULL REFERENCES sys_user(account) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sys_session IS '登录会话。token 经 HttpOnly Cookie 下发；12 小时过期';
CREATE INDEX IF NOT EXISTS idx_sys_session_account ON sys_session(account);

-- ── 种子角色 ────────────────────────────────────────────────────────────────
-- 与前端 navTree 的静态 roles 一一对应：切到真表后，第一屏菜单必须与原来一致。
INSERT INTO sys_role (role_key, role_name, description, data_scope, perms, builtin, home) VALUES
 ('auditor', '审核岗', '复核 AI 结论、处置单据', 'all',
  '["ai-review", "workbench", "report", "trace"]', TRUE, '/review/bills'),
 ('engineer', '知识工程师', '维护制度、规则与本体资产', 'all',
  '["doc-lib", "extract", "workshop", "biz-onto", "onto", "proc", "publish", "trace"]',
  TRUE, '/asset/docs'),
 ('admin', '系统管理员', '平台、模型、账号与系统配置', 'all',
  '["agents", "llm", "startup", "users"]', TRUE, '/sys/startup'),
 ('leader', '领导查询', '只读查看整体质量与偏差', 'all',
  '["dash", "workbench", "trace", "deviation"]', TRUE, '/dash')
ON CONFLICT (role_key) DO NOTHING;

-- ── 种子账号 ────────────────────────────────────────────────────────────────
-- 演示统一密码 demo-1234（仅 POC；生产必须走「首次登录强制改密」流程）。
-- 摘要是 pbkdf2 60000 轮 + 固定演示盐，盐写在这里只为了让演示库可复现。
INSERT INTO sys_user (account, user_name, phone_no, dept, role_key, enabled, pwd_hash) VALUES
 ('linjia', '林嘉', '13800006021', '单据审核部', 'auditor', TRUE,
  'pbkdf2:60000:fjhy-demo-salt:33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577'),
 ('chenfang', '陈芳', '13900003357', '单据审核部', 'auditor', TRUE,
  'pbkdf2:60000:fjhy-demo-salt:33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577'),
 ('hetech', '何工', '13700008846', '知识运营部', 'engineer', TRUE,
  'pbkdf2:60000:fjhy-demo-salt:33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577'),
 ('wangzong', '王总', '13600001129', '集团领导', 'leader', TRUE,
  'pbkdf2:60000:fjhy-demo-salt:33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577'),
 ('admin', '系统管理', '13500000001', '信息中心', 'admin', TRUE,
  'pbkdf2:60000:fjhy-demo-salt:33b7da85b0e405ce7a3fc13a751483d6b7b128f62a3b3f8f2c6041e1a628a577')
ON CONFLICT (account) DO NOTHING;
