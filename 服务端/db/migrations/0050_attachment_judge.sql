-- ============================================================================
-- 0049 · 附件识别结果人工订正（B-P0-2，客户排期 #3，11-12 节点）
-- ----------------------------------------------------------------------------
-- 客户原文：「新增人工修改与补录（改附件类型、改识别字段），修改留痕，
-- 避免 AI 识别错误影响审核」。现状 attach_type 与抽取字段均只读，
-- AI 判错只能干看着。
--
-- 两件事、一张台账：
--   1. attachment_judge_log —— 改判台账，**只 INSERT 不删改**（审计口径，
--      与 sys_op_log / agent_signal_log 同纪律）。谁、何时、把哪个附件的
--      哪个字段从什么改成什么，前后值都在。
--   2. attachment.fields_override —— 字段的人工值。**不覆盖 AI 台账**
--      （attachment_extract 永不覆盖，人工值也不许写进去）：AI 原值与
--      人工值并列显示，谁也不吃掉谁——「AI 判了什么」和「人改成了什么」
--      是两个都要留的事实。
--
-- scope 红线（写进代码注释与台账口径）：改判只修数据与留痕，
-- **不自动重跑审核、不触发引擎**——改完类型后是否重新判定是另一个动作。
-- type_source 走既有枚举值「人工指定」（ck_att_type_source 0037 已含），
-- 不加新枚举不加旁路。
-- ============================================================================

CREATE TABLE IF NOT EXISTS attachment_judge_log (
    id            BIGSERIAL   PRIMARY KEY,
    attachment_id BIGINT      NOT NULL REFERENCES attachment(id) ON DELETE CASCADE,
    field         TEXT        NOT NULL,   -- attach_type | 字段名（place/start_date/…）
    old_value     TEXT,                   -- 改前值。NULL=原本未判/未抽
    new_value     TEXT,                   -- 改后值。NULL=清除人工值还原 AI 值
    judged_by     TEXT        NOT NULL,   -- 账号（chenfang / admin…）
    judged_label  TEXT        NOT NULL DEFAULT '',  -- 真名展示（署名口径同 user_label，不打星）
    judged_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_att_judge_att
    ON attachment_judge_log (attachment_id, judged_at DESC);

COMMENT ON TABLE attachment_judge_log IS
  '附件识别结果人工改判台账。只 INSERT 不 UPDATE/DELETE——「AI 原值」与'
  '「人工改后值」都留在这里，界面上并列显示不覆盖。改判不触发引擎重跑'
  '（scope 红线），是否重新判定是另一个动作。';

COMMENT ON COLUMN attachment_judge_log.field IS
  'attach_type=改附件类型（含未判类型的人工定类）；其余值=字段名'
  '（attachment_extract 的键：place/start_date/title/…）。';

COMMENT ON COLUMN attachment_judge_log.judged_label IS
  '操作人真名（展示用）。字段名沿用 *_label 署名口径：mask 按字段名打星，'
  'label 不在姓名策略表内，署名落台账不能被打成「陈*」。';

-- 字段人工值：只存「人改过的那几个键」，不复制 AI 全量——
-- 没改过的键继续以 AI 台账为准，改过的键界面并列展示两值。
ALTER TABLE attachment
    ADD COLUMN IF NOT EXISTS fields_override JSONB;

COMMENT ON COLUMN attachment.fields_override IS
  '识别字段的人工订正值（键=attachment_extract 字段名）。只存改过的键：'
  'AI 原值永远以 attachment_extract 台账为准，本列只叠加人工值，'
  '**不覆盖历史**。改判动作逐键落 attachment_judge_log。';
