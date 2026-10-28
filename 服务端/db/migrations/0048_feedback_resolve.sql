-- 0048 异议处置留痕（打磨规划 B-P0-1，2026-10-27）
--
-- 客户排期表 #2 后半句「意见回流至规则库侧汇总处理」的处置侧落库。
-- 审核岗勾「不认可这条命中」回流 feedback_log（finding_vs_human /
-- false_positive，source=human_review）此前只写不读——规则管理员侧零呈现，
-- 这条回流链断在中间。B-P0-1 在规则库加「异议收件箱」页签：按规则聚合、
-- 点开看明细、处置两个动作（确认为规则问题 / 维持规则）。
--
-- 处置痕迹落在 feedback_log 本身而不是新表：一条异议的「提出」与「处置」
-- 是同一实体的两个生命周期阶段，分两张表则聚合口径会漂移（与 Deviation 页
-- 同源双视图的纪律冲突）。只加三列不改存量行——历史行 NULL = 待处置。
-- feedback_log 是审计表：加列走迁移，台账只 INSERT 的纪律不破坏——
-- UPDATE 只发生在处置动作上，且每次处置同步写 sys_op_log（谁、何时、
-- 对哪条异议、维持还是修订），审计链不断。
ALTER TABLE feedback_log
    ADD COLUMN IF NOT EXISTS resolved_by  TEXT,
    ADD COLUMN IF NOT EXISTS resolved_note TEXT,
    ADD COLUMN IF NOT EXISTS resolved_at  TIMESTAMPTZ;

COMMENT ON COLUMN feedback_log.resolved_by IS
  '处置人（账号）。NULL=待处置。处置动作见 sys_op_log（category=rule）。';
COMMENT ON COLUMN feedback_log.resolved_note IS
  '处置说明。维持规则时必填（为什么维持——提出异议的审核员看得到这段话）；'
  '确认为规则问题时填修订去向摘要。';
COMMENT ON COLUMN feedback_log.resolved_at IS
  '处置时间。与 resolved_by 同生同灭：三列要么全 NULL（待处置）要么全有值。';

-- 聚合查询按 (metric, deviation_kind, 规则号) 走，给异议页的分组查询加一条索引
CREATE INDEX IF NOT EXISTS ix_feedback_metric_kind_time
    ON feedback_log (metric, deviation_kind, observed_at DESC);
