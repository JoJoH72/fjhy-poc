-- 0045 操作日志（2026-10-27 M2 管理员新功能）
-- 企业级化规划的动作留痕红线配套展示面：谁、何时、对什么做了什么。
-- 只 INSERT 不 UPDATE/DELETE——审计性质，业务数据可清理而台账不可删
-- （与 agent_signal_log 同一口径，见 recorder.py 文件头说明）。
CREATE TABLE IF NOT EXISTS sys_op_log (
    id          BIGSERIAL PRIMARY KEY,
    acted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT NOT NULL,              -- 账号（linjia / admin…）
    actor_name  TEXT NOT NULL DEFAULT '',   -- 真名（账号被删后日志仍可读）
    role_key    TEXT NOT NULL DEFAULT '',   -- 动作发生时的角色
    category    TEXT NOT NULL,              -- login / iam / publish / review
    action      TEXT NOT NULL,              -- 登录 / 新增角色 / 停用账号 / 签字发布…
    target      TEXT NOT NULL DEFAULT '',   -- 对象标识（账号 / 角色 / 版本号 / 单号）
    detail      TEXT NOT NULL DEFAULT '',   -- 人话摘要（不含敏感值）
    ok          BOOLEAN NOT NULL DEFAULT TRUE,
    ip          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_op_log_at    ON sys_op_log (acted_at DESC);
CREATE INDEX IF NOT EXISTS idx_op_log_actor ON sys_op_log (actor);
