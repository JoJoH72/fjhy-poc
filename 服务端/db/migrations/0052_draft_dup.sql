-- ============================================================================
-- 0052 · 草稿重复检测落库（B-P1-D，2026-10-28）
-- ----------------------------------------------------------------------------
-- run_ai 出口对每条可规则化草稿做确定性比对（vs rule 表 version=1 全部规则），
-- 检出结果随草稿行存两列——审定页「疑似与 TR-xx 重复」徽标直接读它，
-- 存量草稿由 list_drafts 出口现算兜底并回写（不改 review_state）。
--
-- 只标疑似不写死结论：把握度（高/低）+ 相似点由三个确定性信号组合
--（来源条款相同 / 名称相似≥60% / 阈值要素重合≥2），草稿作废、仍采纳、
-- 替换库内，是审定人的裁决（复用驳回原因留痕），不是检测器的。
-- ============================================================================

ALTER TABLE doclib_draft
    ADD COLUMN IF NOT EXISTS dup_of     JSONB,
    ADD COLUMN IF NOT EXISTS dup_signals TEXT;

COMMENT ON COLUMN doclib_draft.dup_of IS
  '重复检测结果：{matches:[{rule_id,rule_name,strength(高|低),name_ratio,'
  'shared_numbers,signals_desc}], signals_desc}。NULL=无比对命中（含检测'
  '上线前的存量行，list_drafts 出口现算回写）。检测纯只读、不调模型。';
COMMENT ON COLUMN doclib_draft.dup_signals IS
  '最强一条相似点的人话摘要（徽标 tooltip 用），如「名称相似 80%；阈值要素重合（80元）」。';
