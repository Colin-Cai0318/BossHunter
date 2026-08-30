---
name: resume-profile-curator
description: "从 BossHunter Resume Studio 已接受的简历字段和 STAR 事实构建或刷新可追溯中文职业简历档案；适用于把同一项目下的多条技术贡献整理为多个 STAR、维护 Known Gaps、贡献边界与少量高价值补充问题，不用于直接针对 JD 改写或视觉排版。"
---

# Resume Profile Curator

## 入口条件

- 只读取 status=accepted 的事实和用户明确确认的补充回答。
- 不把 pending、rejected 或分类失败材料用于档案正文。
- 没有已接受事实时停止，并引导用户先在 Resume Studio 审核。

## 工作流

1. 在 Resume Studio 刷新补充问题。
2. 每轮最多处理 5 个最高优先级问题，顺序为个人贡献边界、冲突字段、结果与数字口径、缺失任务背景、派生技能。
3. 把回答保存为独立确认记录；不要覆盖原材料或原文证据。
4. 用 group_id、项目标题和来源文件归并项目；一个项目可以包含多个较小 STAR。
5. 先生成证据安全的 Career Profile：投递简历正文只包含可提交内容；Known Gaps、表达边界、未使用事实和被剔除表达放在独立审核面板。
6. 用户按项目 review 草稿；每轮只追问会改变贡献等级、结果或项目排序的高价值问题，把回答保存为新证据后重新生成。
7. 检查质量报告、证据覆盖率和目标岗位取舍；只有用户审阅后才把版本启用为当前求职主简历。

## 生成约束

- 每个正文、项目、STAR、缺口或批准表达至少引用一个有效 fact_id 或已回答的 clarification_id。
- 面向中文母语用户时，所有问题使用自然简体中文，并明确指出正在核对的材料、事实和差异；不得向用户暴露内部字段名或只说“内容重复/冲突”。
- 项目标题可以使用受管 `source_filename` 中明确出现的仓库/材料名；文件名仅支撑标题实体，不能支撑技术、职责或成果。
- 绑定到具体事实的 clarification 只有在该事实仍为 accepted 时才可进入 Prompt 和校验；拒绝或删除事实后，其历史回答不能单独支撑正文。
- 新数字、日期、链接、技术名或实体名称必须阻断生成。
- ownership_level=unknown 时不能使用“负责、推动、主导、独立完成”等升级贡献的措辞。
- 不完整 STAR 保留为事实或 Known Gap，不补写缺失的结果。
- 项目标题只输出一次；同一项目的通信、感知、算法、控制、工程化等贡献分别作为 stars，不复制项目标题。
- 每个 STAR 必须有 Action，并优先表达为“使用/基于工具、技术、方法、流程或专业能力，解决/完成问题或任务；已证实结果”。结果无证据时省略并加入审核报告的 Known Gap。
- situation、task、action、result 用于内部校验，Markdown 不机械输出 S/T/A/R 标签。
- 无来源数字、英文技术名、实体或贡献动词必须从正文剔除。一次完整修复后仍不合格时，保留警告并使用已接受 STAR 的原文 Action/Result 生成保守条目；不要让单条越界表达导致整份档案失败。
- GitHub 仓库材料先整理成项目级技术摘要，再拆分部署、核心能力、可靠性和验证等 STAR；不要把任意代码分片直接当成不同项目。
- 总结材料或作品集的单个语义分片最多提取 6 条不重复 STAR。按职业选择关键职责、问题解决、流程改进、交付、客户/用户影响、质量安全、采用范围或真实案例闭环；不要只按技术复杂度选择。
- 分片合并后再次按 Action 做来源级近重复检查；长 Action 相同或互为包含时，即使模型给出不同标题也只保留信息更完整的一条。
- 生成前把 resume_field 按来源、实体和 group_id 压缩为实体记录，把内容相同的确认回答合并传输；必须保留全部原始 ID 以便校验，不能用无依据截断换取速度。
- 指定目标岗位时，基本信息、工作、教育、奖项和技术总结 STAR 全部保留；原简历中的客户项目最多选择 4 个信息完整且与目标相关的代表组。只用于说明“多个值均保留”的 conflict 回答不进入生成 Prompt。
- Career Profile 按“基本经历 + 每个总结/作品来源”分段生成并分别校验：resume_field 只生成 sections，每个 star_story 来源只生成自己的 projects；同一来源默认归并为一个项目，标题作为项目内 STAR。各部分确定性合并，不发送跨来源的大上下文修复请求。
- 已确认的 derived_skill 在 Prompt 中只传输“认可的技能名称”与全部 clarification_id，解释细节仍保留在数据库用于校验，避免重复描述占用上下文。
- 补充了本人动作、任务、结果、量化口径或贡献等级的已确认回答必须进入对应 section 或 STAR 正文并引用 clarification_id；模型遗漏时只允许一次修复，修复后仍遗漏则阻止生成。纯“无”、未确认或边界回答不强制写正文。
- 目标岗位是可选的编辑偏好，只能改变已证实事实的选择、排序和篇幅；不得把岗位名称或关键词当作新证据。
- 派生技能在用户确认前不能当作已证明能力。
- Career Profile 的 JSON 是长期事实与审核底稿；Markdown 是清洁的投递正文，不渲染 Known Gaps 或表达边界。JD 定制和视觉排版继续由后续流程处理。

处理字段、分组和证据身份时读取 [fact-contract.md](references/fact-contract.md)。判断贡献动词时读取 [ownership-levels.md](references/ownership-levels.md)。生成或检查文字时读取 [honesty-rules-cn.md](references/honesty-rules-cn.md)。设计追问时读取 [clarification-policy.md](references/clarification-policy.md)。
