-- 0047 退回原因分类（打磨规划 P1-4，2026-10-27）
--
-- 退回理由此前是自由文本：每次退回都要现编一段话，且「为什么退」没有
-- 结构化维度——偏差与回流页永远只能按规则聚合，不能按退回原因出分布。
-- 分类随留痕落库（缺附件/超标准/信息有误/疑似重复/其他），字段可空：
-- 存量行没有分类，不回填猜测值。
ALTER TABLE manual_review
    ADD COLUMN IF NOT EXISTS return_category TEXT;

COMMENT ON COLUMN manual_review.return_category IS
    '退回原因分类（缺附件/超标准/信息有误/疑似重复/其他）。可空：存量与'
    'agree 类决策没有分类。结构化后偏差页可按退回原因出分布。';
