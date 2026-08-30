# Resume Studio 交互与生成稳定性修复 Review

## 用户反馈

1. 接受事实时整个页面刷新并抖动。
2. 构建、预览与启用区域在回答问题、生成和启用时也出现相同现象。
3. Profile 因一条内容引用不完整，报告一组无来源英文词并导致整份生成失败。
4. 需要先从两个 GitHub 仓库形成项目级技术摘要，再结合原简历生成草稿，用户 review 和回答问题后再确定最终流程。

## 交互修复

### 根因

前端所有操作共用 `run()`，动作成功后无条件重新请求 `/api/resume-studio` 并替换整个 `workspace`。同时成功/失败消息位于页面正常布局中，每次显示都会把下面内容整体下推。

### 当前行为

- 接受、拒绝和退回事实：只替换对应 fact，并在本地重算该来源的 accepted/pending 计数。
- 回答、忽略和刷新问题：只更新 `clarifications`。
- 生成 Profile：只把新版本插入 `profile_versions` 首位。
- 生成 Profile 前可填写目标岗位；该字段只影响事实选择、排序和篇幅，并记录在质量报告中。
- 启用 Profile：只更新版本状态。
- 删除来源、清空工作室：直接更新本地相关集合。
- 上传和提取仍保留服务端刷新，因为这两个动作会同时改变来源分类、事实集合和统计。
- 消息改为右上角 fixed toast，不再参与页面布局。

结果是高频审核和 review 操作不再执行全工作区 GET，也不会因为消息条出现造成整体位移。当前筛选为“待审核”时，接受事实后该卡片按筛选语义消失，这是局部列表变化，不是整页刷新。

## 无来源事实修复

### 原因

`Authorization`、`Cookie`、`JSON.parse` 等词可能确实存在于某个已接受事实，但模型生成条目没有把正确 `fact_id` 引用到该条文字上。确定性校验因此正确阻止了越权表达；问题在于第二次 AI 修复仍失败后，异常会中止整个 Profile。

### 当前策略

1. 首份 JSON 仍执行严格 token、数字、实体和贡献动词校验。
2. 失败后仍允许一次完整 AI 修复，不放宽证据规则。
3. 修复后仍失败时，不进行第三次外部 AI 调用：
   - 剔除不合格条目；
   - 将原因写入 `quality_report.validation_warnings`；
   - 使用已接受的 `star_story` Action/Result 和自身 fact_id 生成保守项目条目；
   - 只有完全没有可追溯正文时才继续报错。
4. UI 显示“已剔除 N 条无来源表达”，供用户 review。

这保证错误内容不会进入简历，同时避免单条引用错误让整份生成失败。

## 仓库摘要与简历草稿

本轮通过 GitHub 当前主分支、完整提交历史、代码树、测试文件和 npm registry 元数据形成两份项目技术摘要。没有调用 BossHunter 配置中的外部 AI。

私有输出保存在未跟踪的 `resume_markdown` 目录：

- 两份仓库级技术总结；
- 一份 2026.08 项目增强简历草稿；
- 一份 8 个待确认问题和四阶段推荐流程。

技术摘要以一个仓库为一个 project，再按部署、核心能力、可靠性/安全、验证/交付拆成多个 STAR。未核验的使用人数、效率提升、Dump 数量和“独立主导”等内容只作为问题，不写入正文。

## 验证

- Career Profile 定向测试：26 passed。
- 完整 Python 测试：346 passed，11 subtests passed，239.85 秒。
- 前端：TypeScript 与 Vite 生产构建通过。
- `service.py` 与定向测试 Ruff：通过。完整扫描历史 `server.py` 仍有 38 个既有告警，本功能没有顺带做大范围格式化或异常处理重构。
- Skill：`Skill is valid!`。
- `git diff --check`：通过。

新增回归使用用户报告的同类词组验证：修复响应连续两次包含无来源英文词时，最终 Markdown 不包含这些词，质量报告记录警告，并保留由已接受 STAR 生成的证据安全项目内容。

## Review 重点

1. `ResumeStudioPage.tsx`：`run(..., {refresh:false})` 和各动作的局部状态更新。
2. `service.py`：`allow_partial` 校验、warning 汇总和 accepted STAR 保守回退。
3. `test_resume_builder.py`：越界数字和用户报告词组的降级测试。
4. `resume-profile-curator` Skill：草稿 review 后再追问和仓库级摘要策略。
5. 目标岗位链路：前端输入、API 长度约束、Prompt 事实边界和质量报告回显。

## 当前边界

- 没有浏览器自动化用例；页面行为通过状态流审查和 TypeScript 生产构建验证。
- 保守回退优先保证真实性，文字质量低于正常 AI 生成；用户回答高价值问题并重新生成后，正常项目条目会替换保守条目。
- 本轮未提交、推送或创建 PR。

## 真实材料生成容量优化

- 技术总结单分片候选上限由 3 调整为 6，保持原有最多 4 次分片调用；明确要求保留有证据的团队采用、项目覆盖与真实问题闭环，避免只返回实现细节。
- 多分片候选在来源级再次按长 Action 近重复合并，解决同一 Rootless/NoC/故障案例被模型换标题重复返回的问题。
- Profile Prompt 不再逐条重复 resume_field 的来源、类型和实体元数据，而是按来源、entity_type 和 group_id 压缩；相同确认回答合并传输，但保留全部 fact_id 与 clarification_id。
- 绑定到 rejected/deleted fact 的历史回答不再进入生成 Prompt，也不能通过确定性引用校验，避免旧材料回答绕过当前事实审核状态。
- 质量报告新增 `prompt_char_count`，用于复核上下文优化是否真正生效。
- 指定目标岗位时，Prompt 保留全部核心实体与技术总结 STAR，原简历客户项目按可读性、岗位相关性、字段完整度和时间选择最多 4 组；质量报告同时记录 `prompt_fact_count`。
- conflict 回答只用于审核流程，不再占用生成上下文；resume entity fields 改为 `[fact_id, field_name, value]` 紧凑编码。
- Profile 改为两阶段生成：resume_field→sections、star_story→projects。两部分使用独立 Prompt 和确定性校验后合并；同一技术总结来源默认归并为一个项目，避免一条贡献被误拆成一个项目。
- 质量报告新增 `prompt_request_count`；正常请求数为“存在基本经历时 1 次 + 技术总结来源数”，本轮两份技术总结共 3 次。
- 项目标题校验将受管 `source_filename` 纳入实体证据，允许模型把同一技术总结归并成仓库项目；技术、职责和成果仍必须由事实正文或回答支撑。
- 命名 token 比较会去除文件名分词产生的尾部 `_`、`.`、`/`、`-`，解决 `crash-mcp-private_技术总结.md` 与项目标题 `crash-mcp-private` 的假阳性；中间字符和正文 token 不放宽。

## 按来源拆分的最终生成流程

- 初版两阶段流程把两份技术总结合并到一次 projects 请求，项目 Prompt 约 11k 字符；真实调用连续两次在外部服务端超时。
- 最终流程改为 `resume_field → sections`，随后按 `source_id` 为每份技术总结分别生成 projects；每个项目请求最大输出由 4000 调整为 3200 token。
- 本轮材料因此执行 3 次顺序请求：1 次基本经历、1 次 crash-mcp-private、1 次 opengrok-mcp-server。不同来源不会在模型上下文中互相串项目，合并后仍执行统一的事实 ID、确认回答、数字、实体和贡献动词校验。
- 新增回归测试验证两份技术来源产生两次独立项目请求、单次 token 上限为 3200，且最终引用两份来源事实。

## 真实材料最终结果

- 生成文件：`data/career_profiles/career_profile_6849b1ea7d18.md`。
- 目标方向：Linux 内核稳定性 / Android BSP。
- Prompt：3 次请求、60 条提示事实、20,745 字符。
- 结果：58 条事实、20 条已回答问题进入档案；开放问题 0；确定性验证告警 0；证据覆盖率 79.45%。
- 项目结构：`crash-mcp-private` 与 `opengrok-mcp-server` 各自作为一个项目，每个项目包含 5 条 STAR。
- 已进入正文的关键结果包括：Qualcomm/MTK Dump 统一会话、ARM64 证据化分析、团队近一周完成 10+ 次分析、空指针定位与 patch 建议、Web-only 重构、三套环境覆盖小米客户全部项目。
- Web-only 的客户约束、分析层与 vmcore 客制化加载的个人职责，以及其他客户项目未验证等内容保留在“已确认表达边界”，避免在贡献等级不充分时使用主导或独立完成。

## 质量差距复测

固定 100 分量表如下：事实准确与证据安全 30；目标岗位相关性 20；项目级多 STAR 结构 20；已确认关键结果覆盖 20；可读性与投递就绪度 10。

| 维度 | 人工优化基准 | 当前功能生成 | 说明 |
| --- | ---: | ---: | --- |
| 事实准确与证据安全 | 30 | 30 | 两稿均不写无证据量化结果；生成稿额外通过零告警确定性校验。 |
| 目标岗位相关性 | 19 | 18 | 生成稿覆盖 Linux/Android、Dump、ARM64、NoC；技能摘要不如人工稿集中。 |
| 项目级多 STAR 结构 | 19 | 19 | 两个仓库均保持一个项目下 5 条 STAR。 |
| 已确认关键结果覆盖 | 19 | 17 | 10+ 分析、空指针 patch、三环境覆盖已进入正文；Web-only 原因和分析层职责仍在边界区。 |
| 可读性与投递就绪度 | 9 | 7 | 正文可 review，但工作经历较长，且 Known Gaps/边界区属于审核信息，投递前应移除。 |
| **总分** | **96** | **91** | **相差 5 分，即 5.21%，低于 10% 目标。** |

评分只比较生成能力输出与本轮人工基准的核心简历内容；没有把外部服务耗时计入文字质量。Career Profile 目前是 review 稿，不自动激活；用户确认后再决定是否生成投递版并启用。

## 本轮验证补充

- Career Profile 定向测试：26 passed。
- 完整 Python 测试：346 passed，11 subtests passed；仅有 pytest 缓存目录无写权限警告。
- 前端 TypeScript/Vite 生产构建：通过。
- `service.py` 与 `test_resume_builder.py` Ruff：通过。
- 外部 AI 最终全量生成：成功，零验证告警。
- 本轮仍未提交、推送、创建 PR 或激活 Career Profile。

## 本轮：问题清晰度、答案融合与投递正文隔离

### 问题卡片

“构建、预览与启用主简历”中的问题不再只显示抽象问题与优先级，而是固定展示：

1. 问题类型与关联经历；
2. 当前已知事实；
3. 需要用户作出的单一决定，以及可直接照着回答的格式；
4. 回答后会改写简历的哪个部分。

缺少背景、任务、结果、本人贡献和量化口径分别使用专门问题模板。冲突问题会列出实际冲突值；贡献问题要求在“参与/协作/负责/主导”中选择，并补充 1-3 个本人动作。

### 回答必须进入正文

- “本人动作、任务、结果、量化口径、贡献等级”类有效回答必须进入对应 section 或 STAR，并携带 clarification_id。
- 第一稿漏掉有效回答时，系统自动进行一次定向修复；修复后仍遗漏则明确失败，不会生成一份看似成功但未使用答案的简历。
- “无、未知、未统计、无法核实”等回答只结束审核，不强行写进正文。
- conflict 与纯表达边界仍用于审计，不能成为投递内容。

### 审核数据与简历正文分离

- profile_json 保留 known_gaps 和 approved_framings，用于追溯和二次审核。
- Markdown 只渲染简历 sections/projects。
- 预览、Markdown 下载、启用主简历统一调用干净渲染器；旧版本即使历史 Markdown 含审核区块，也不会通过这些入口再次显示或启用。
- UI 将审核信息放入独立折叠面板，并明确标注不会进入预览、下载或启用正文。

## 高 Star 开源项目调研与借鉴

调研时间为 2026-08-24，Star 数为当日页面近似值：

| 项目 | Star | 借鉴点 | 本轮采用 |
| --- | ---: | --- | --- |
| [Reactive Resume](https://github.com/AmruthPillai/Reactive-Resume) | 41.6k | 实时预览、结构化编辑、导出、隐私与自托管 | 构建/预览/启用一致；审核信息不混入导出 |
| [Awesome-CV](https://github.com/posquit0/Awesome-CV) | 28.4k | 语义明确、可定制、投递成品与编辑数据分层 | Markdown 只保留投递语义，不展示内部审核字段 |
| [RenderCV](https://github.com/rendercv/rendercv) | 17.4k | 内容与格式分离、结构化源文件、版本控制 | JSON 作为可追溯源，Markdown 作为干净投递视图 |
| [Deedy-Resume](https://github.com/deedy/Deedy-Resume) | 5.1k | 单页、重点突出、信息密度可控 | 限制项目与 STAR 数量，问题回答优先改写正文而非追加说明区 |
| [JSON Resume](https://github.com/jsonresume/resume-schema) | 2.4k | basics/work/education/skills/projects 标准结构与校验 | 保持职业通用 section/project 模型并执行确定性证据校验 |

结论：高质量简历工具的共同点不是多写“待完善说明”，而是结构化事实源、内容与呈现分离、即时预览、可验证导出和简洁投递结果。本轮没有直接复制第三方模板或代码。

## 跨职业匿名样本评测

样本位于 tests/fixtures/resume_professions.json，均为匿名合成内容：

| 职业 | 方法/能力证据 | 已证实结果 | 最终分数 | 证据覆盖 | 验证告警 |
| --- | --- | --- | ---: | ---: | ---: |
| 产品经理 | 用户访谈、漏斗分析、A/B 测试 | 激活率 42%→55%，次周留存 +6pp | 100 | 100% | 0 |
| 企业销售 | Salesforce、客户分层、试点推进 | 18 次拜访、5 个试点、3 家签约 | 100 | 100% | 0 |
| 临床护士 | SBAR、交接核对、培训 | 完整率 81%→96%，未再发生重复用药核对遗漏 | 100 | 100% | 0 |
| 初中数学教师 | 分层教学、形成性评价 | 及格率 72%→88%，低分学生减少 9 人 | 100 | 100% | 0 |

每个职业均通过真实外部 AI 的“事实提取→接受→问题生成→Profile 生成→确定性校验”完整链路。评分检查事实抽取、Action/Result、方法进入正文、项目/STAR 结构、问题清晰度、正文隔离与零告警；80 分为通过线。该结果验证流程可迁移到非技术职业，但不代表视觉排版、ATS 命中率或所有真实职业材料均为 100 分。

首轮四职业都是 90 分，原因是模型把 *_case.md 机器文件名当成项目标题并触发安全回退。最终提示明确规定 source_filename 只用于归并，标题优先来自结构化事实或正文中的项目/案例/作品名称，修复后四职业均零告警；事实校验规则没有放宽。

评测脚本 scripts/evaluate_resume_professions.py 要求显式传入 --external-ai-consent，可用 --case-id 单独复测某个匿名职业，并把具体验证告警写入 JSON 报告。最终报告位于 tmp/resume-profession-evaluation-final/report.md。

## 最终验证

- Python 全量回归：349 passed，15 subtests passed；唯一告警为 .pytest_cache 无写权限。
- Resume Builder 定向回归：29 passed，4 subtests passed。
- 前端 TypeScript 与 Vite 生产构建：通过。
- 修改范围 Ruff 与 git diff --check：通过。
- resume-profile-curator Skill：Skill is valid。
- 四职业真实外部 AI 端到端评测：4/4 均 100 分、100% 证据覆盖、0 验证告警。
## 中文提问清晰化（2026-08-31）

### 反馈对应

- 产品默认面向中文母语用户；问题类型、字段名、说明和回答指导统一使用自然简体中文。
- 原冲突问题显示内部字段名，并用斜杠拼接多个值，没有说明材料来源和原文，导致用户不知道系统正在比较哪两条信息。
- 精确重复的值不会生成冲突问题；值不同时明确称为“不一致”，不再笼统称为“重复”。

### 当前冲突问题结构

每个不一致字段都提供：

1. 中文实体和字段名称，例如“工作经历 / 职位”；
2. 编号选项，例如“选项 1 / 选项 2”；
3. 每个选项的字段值；
4. 来源材料文件名；
5. 同组经历线索，例如公司、项目或时间；
6. 支撑该值的原文证据；
7. 可直接使用的回答格式：“保留选项 1/2”，或说明两个选项分别属于哪段经历。

前端把冲突项渲染为独立选项卡片，并将右上角来源改为“跨材料逐项核对”。普通缺失背景、个人任务、结果、贡献等级、专业能力和量化口径问题也改为“当前缺少什么 + 具体回答什么 + 回答后修改哪里”的中文句式。

### 刷新与兼容

待回答问题使用原 dedupe_key 原位更新。用户点击“刷新待确认问题”后，数据库中的 open 问题会更新为新文案和新 metadata，不需要清空 Resume Studio；已回答或已忽略问题保持历史状态，不会重新打扰用户。

### 新增验证

- 冲突问题回归明确检查两份材料、两种职位、公司线索和原文证据全部存在。
- 回归明确禁止在用户问题中出现 position、group_id 等内部字段。
- Resume Studio 定向测试：38 passed，4 subtests passed。
- 前端 TypeScript/Vite 生产构建、Ruff 和 Skill 校验通过。
## 上游同步与最终组合验证（2026-08-31）

- 上游基线：powerycy/BossHunter main 的 aa337e6。
- 独立同步 PR：https://github.com/Colin-Cai0318/BossHunter/pull/2。
- fork/main 合并提交：916362f。
- 同步冲突处理：
  - README 采用上游精简结构，同时保留简历工作室能力与文档入口；
  - 数据库同时初始化 Resume Studio 与 collection runs；
  - resume_upload 保留共享文档转换模块，其 Markdown UTF-8 校验覆盖上游修复；
  - 前端从合并后的 TypeScript 源码重新构建，不保留任一侧旧资源哈希。
- 上游同步分支验证：547 passed，19 subtests passed。
- 最终功能组合分支验证：555 passed，23 subtests passed。
- 最终前端 TypeScript/Vite 生产构建通过。
- 唯一告警为工作区 .pytest_cache 无写权限，不影响测试结果。
- resume_markdown/ 和 start-bosshunter.cmd 始终保持未跟踪，未进入提交或 PR。
