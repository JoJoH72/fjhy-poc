-- ============================================================================
-- 0051 · 规则修订与版本历史（B-P1-E，2026-10-28）
-- ----------------------------------------------------------------------------
-- 现状：rule 表主键就是 (rule_id, version)，多版本基础设施在；但 82/82
-- 全是 version=1，没有「修改规则 → 写 version+1」的动作，RuleDetail 也没有
-- 版本历史卡。改规则的唯一途径是重跑装载脚本。
--
-- 修订纪律（与驳回/裁决同源）：修订必须给理由——三个月后看这条规则的人
-- 只看得到这段话。本迁移给 rule 表补四列修订元数据（新版本行携带，
-- 旧行 NULL=装载基线）：
--   revised_by     修订人账号（hetech / admin…）
--   revised_label  修订人真名（展示署名，*_label 口径不打星）
--   revised_reason 修订理由（必填，前端表单强校验 + 后端 400 双闸）
--   revised_from   上一版本号（v3 的行写 2——历史卡按它串链）
--
-- 修订产生的 v2 行 status='draft'，不进执行集（装载的 v1 行原样不动，
-- 天然留痕）；发布工作区冻结时快照含全部版本行，diff 的 modified 才非零。
-- 「v2 随哪个 release 发布」由 release_item 关联回答，本迁移不重复存。
--
-- compare 声明（A-P1-1 契约）不另起列：它属于规则资产 logic 的一部分
--（logic.compare = [{label, actual_key, limit_key, unit}]），修订表单里
-- 编辑、随版本行快照——单实体单入口，出口层与前端都只消费。
-- ============================================================================

ALTER TABLE rule
    ADD COLUMN IF NOT EXISTS revised_by     TEXT,
    ADD COLUMN IF NOT EXISTS revised_label  TEXT,
    ADD COLUMN IF NOT EXISTS revised_reason TEXT,
    ADD COLUMN IF NOT EXISTS revised_from   INTEGER;

COMMENT ON COLUMN rule.revised_by IS
  '修订人账号。NULL=装载基线（asset_loader 建的 v1）或采纳入本体的规则。';
COMMENT ON COLUMN rule.revised_label IS
  '修订人真名（展示用，*_label 署名口径不被脱敏打星）。';
COMMENT ON COLUMN rule.revised_reason IS
  '修订理由（必填）。与驳回原因/裁决结论同一条纪律：没有理由的修订'
  '在三个月后无法向看这条规则的人交代。';
COMMENT ON COLUMN rule.revised_from IS
  '修订自哪个版本（v3 行写 2）。版本历史卡按 (rule_id, version) 全列 +
  本列串链，配合 release_item 回答「v2 随 rs-xxx 发布」。';

-- 版本历史卡按 (rule_id, version DESC) 查，主键已是 (rule_id, version)，
-- 走主键索引即可，不另建索引。
