-- 0044 四角色 IA 重排（2026-10-26 晚，客户方向性要求"像企业级产品"）
--
-- 设计定稿见 设计/产品企业级化总体规划_v0.1.md §2/§3：
--   审核员   = 个人工作台/智能审核/工作台/报告/审计追溯（已就位，不动）
--   规则管理员(原知识工程师 engineer) = 制度文件/规则库/发布管理/审计追溯(只读)
--              ——业务本体三页与本体工场移出业务界面（功能不删，归 admin 知识结构）
--   系统管理员 = 用户与角色/模型配置/运行监控/系统自检/知识结构(只读,本体换形态)
--   领导     = 总览看板/偏差与回流/审核队列(只读)（不动）
--
-- perms 键沿用 navTree 的 key（前端菜单项 key 不变，变的只是组织与名称），
-- 新增 keys：rules(规则库聚合页)、knowstruct(知识结构)、ops(运行监控=原agents)。
-- 幂等：jsonb 去重聚合，重复执行不长重复项。

-- 规则管理员（engineer）：四步工作流，菜单即步骤
UPDATE sys_role
   SET role_name = '规则管理员',
       description = '维护制度文件与审核规则：上传、审定、发布',
       perms = '["doc-lib", "rules", "publish", "trace"]'::jsonb,
       home  = '/asset/docs'
 WHERE role_key = 'engineer';

-- 系统管理员：菜单名词化 + 收编知识结构
UPDATE sys_role
   SET perms = '["users", "llm", "ops", "startup", "knowstruct"]'::jsonb,
       home  = '/sys/users'
 WHERE role_key = 'admin';
