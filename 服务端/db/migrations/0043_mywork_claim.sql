-- 0043 个人工作台（客户待办 #1，2026-10-26）：认领池 + 账号单位范围
--
-- 客户原话：「个人工作台：根据账号权限匹配对应待审工单；展示当前账号下历史审核数据」。
-- 对位客户在用共享系统（中兴新云）的派工机制：拉式抢单池 + 按单位分审核专业组。
--
-- 三件事：
--   1) review_assignment  —— 工单认领留痕。一张单同时最多一个有效认领
--      （部分唯一索引），释放不删行、写 released_at，认领历史可审计。
--      assignee 存**账号**（linjia），不存姓名——姓名可改、账号不可改；
--      展示时 JOIN sys_user 取 user_name（出口字段名用 claim_label，
--      不叫 reviewer：mask M01 按字段名打星，打星的署名=签名作废）。
--   2) sys_user_org       —— 账号↔核算主体（org 表）。空 = 不限单位（全池）；
--      挂了 = 工单按单位过滤（审核专业组）。org 表里已有 10 个单位实体。
--   3) 审核岗角色         —— perms 增加 mywork、落地页改个人工作台。
--      perms 是 jsonb，用 jsonb_agg(DISTINCT) 去重，迁移可重复执行。

CREATE TABLE review_assignment (
    id          BIGSERIAL PRIMARY KEY,
    bill_no     TEXT        NOT NULL REFERENCES expense_bill(bill_no) ON DELETE CASCADE,
    assignee    TEXT        NOT NULL,                            -- 认领人账号（sys_user.account）
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,                                     -- NULL = 认领中；释放只盖时间不删行
    release_note TEXT                                                   -- 释放原因（演示期可空）
);

COMMENT ON TABLE review_assignment IS
  '工单认领留痕（2026-10-26 客户待办#1）。认领即锁定：有效认领（released_at IS NULL）'
  '期间他人不可提交该单复核；释放写 released_at 不删行，认领历史可审计。';
COMMENT ON COLUMN review_assignment.assignee IS
  '认领人账号。存账号不存姓名：姓名可改、可重名，账号是唯一键。';

-- 一张单同时最多一个有效认领。并发双击认领时靠它兜底（第二个 INSERT 违反唯一约束）。
CREATE UNIQUE INDEX uq_review_assignment_active
    ON review_assignment (bill_no) WHERE released_at IS NULL;
CREATE INDEX idx_review_assignment_assignee ON review_assignment (assignee, released_at);


CREATE TABLE sys_user_org (
    account    TEXT NOT NULL REFERENCES sys_user(account) ON DELETE CASCADE,
    org_id     TEXT NOT NULL REFERENCES org(org_id)       ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account, org_id)
);

COMMENT ON TABLE sys_user_org IS
  '账号的核算主体范围（审核专业组）。空 = 不限单位看全池；挂了 = 工单列表只看所挂单位。'
  '任何角色挂了都按单位过滤（数据范围语义）；领导/管理员正常不挂，即看全部。';


-- 审核岗：加「个人工作台」菜单 + 落地页改它。幂等：重复执行不会长出重复菜单项。
UPDATE sys_role
   SET perms = (SELECT COALESCE(jsonb_agg(DISTINCT e), '[]'::jsonb)
                  FROM jsonb_array_elements(perms || '["mywork"]'::jsonb) AS e),
       home  = '/review/my'
 WHERE role_key = 'auditor';
