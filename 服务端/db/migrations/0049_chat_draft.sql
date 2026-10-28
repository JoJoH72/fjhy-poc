-- 0049 对话式起草（客户 #6，打磨规划 B-P1-A，2026-10-28）
--
-- 用户在对话抽屉里用自然语言描述业务要求（「住宿费超标 200 元以上要拦截」），
-- 模型追问关键要素后产八要素草稿。对话是**草稿的第五来源**（制度条款 /
-- AI草稿 / 列映射 / 场景拆条 / 对话），产物走与制度抽取完全相同的
-- 审定管道——不新增表、不新增菜单、不新增绕过审定的入库通道。
--
-- 本迁移只动一处约束：doclib_extract_task.mode 增加「对话」枚举。
-- 对话轮次的多轮上下文由前端会话持有、随请求带回（服务端无状态，
-- 每次调用的 token/时延/成败照落 llm_call_log，purpose='chat_draft'）；
-- 「转待审」时才落库：
--   doclib_extract_task 一行（mode='对话'，scope=会话主题）
--   doc_lib_file 一行（占位：对话起草会话记录，parse_state='解析失败'，
--     使 run_split 永远不会把它当制度文件重切——条款与草稿不被级联清掉）
--   doclib_clause 一行（clause_no='对话'，text_source='对话'，
--     body=用户原话——审定页「条款原文」栏显示的就是用户当时怎么说）
--   doclib_draft 一行（review_state='待审'，审定链零改动）
-- text_source / split_strategy 无枚举约束，'对话' 取值不需要迁移。

ALTER TABLE doclib_extract_task DROP CONSTRAINT IF EXISTS ck_dlx_mode;
ALTER TABLE doclib_extract_task ADD CONSTRAINT ck_dlx_mode
    CHECK (mode IN ('条款切分', 'AI草稿', '对话'));

COMMENT ON COLUMN doclib_extract_task.mode IS
  '条款切分 | AI草稿 | 对话（对话式起草的转待审批次，2026-10-28 B-P1-A）';
