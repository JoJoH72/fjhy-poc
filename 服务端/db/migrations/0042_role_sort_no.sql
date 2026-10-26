-- ============================================================================
-- 0042 · 角色增加显示顺序 sort_no
-- ----------------------------------------------------------------------------
-- 事故（2026-10-25，用户发现）：登录页四个角色的顺序变成「系统管理员、审核岗、
-- 知识工程师、领导查询」，原来（本地配置时代）是「审核岗、知识工程师、
-- 系统管理员、领导查询」。
--
-- 真因不在前端：`GET /api/iam/roles` 当时按 `created_at, role_key` 排序，
-- 而四个角色是 0040 一个事务里插进去的，`created_at` 完全相同——
-- 排序退化成 `role_key` 的字母序，admin 恰好排第一。
-- **把「顺序」寄托在一个恰好相等的字段上，就是这次问题的根**。
--
-- 修法：显式给一列 sort_no，排序只看它。
--   1 审核岗 / 2 知识工程师 / 3 系统管理员 / 4 领导查询（与最初一致）
--   新建角色取 max+1（默认 100），排在预置角色之后。
-- ============================================================================

ALTER TABLE sys_role ADD COLUMN IF NOT EXISTS sort_no INTEGER NOT NULL DEFAULT 100;

UPDATE sys_role SET sort_no = 1 WHERE role_key = 'auditor';
UPDATE sys_role SET sort_no = 2 WHERE role_key = 'engineer';
UPDATE sys_role SET sort_no = 3 WHERE role_key = 'admin';
UPDATE sys_role SET sort_no = 4 WHERE role_key = 'leader';

COMMENT ON COLUMN sys_role.sort_no IS
  '显示顺序（登录页角色列表、角色管理列表都按它排）。新建角色取 max+1，不靠 created_at 或 role_key 兜底';
