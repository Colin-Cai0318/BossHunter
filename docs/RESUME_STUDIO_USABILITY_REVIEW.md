# 简历工作室虚构资料验收与修复

本次用“林知遥（虚构测试人物）”和“星河测试数据工具（虚构项目）”验证材料上传、提取、审核修改、补充问答、主简历生成、历史版本启用及下载。开发分支为 `codex/resume-studio-usability`，基线为 `966f5ac`。

## 数据隔离

- 可复用材料在 `tests/fixtures/resume_studio/`，姓名、学校、公司、项目均明确标注为虚构，邮箱使用 `example.com`。
- 浏览器脚本为每次运行创建独立的 `output/resume-studio-*` 工作目录，使用独立 SQLite、材料目录、简历版本目录和配置。
- 默认替换 AI 调用边界；浏览器、HTTP 接口、数据库、上传文件、生成文件、下载和启用逻辑均执行真实实现。
- `--allow-external-ai` 会把这些虚构材料发送到当前配置的 AI 服务。凭据仅在内存中读取，生成及启用只修改隔离目录内的配置。
- 运行报告核对真实 `config.yaml` 的 SHA-256。已有 `resume_markdown/` 和 `start-bosshunter.cmd` 未修改。

## 修复记录

| 场景 | 原问题 | 修复后的行为 |
| --- | --- | --- |
| 编辑事实后生成主简历 | 生成提示词只取旧结构，遗漏用户修改的正文 | 简历字段使用审核后的值；STAR 传入审核正文；编辑后不再把旧动作、成果和技术字段作为生成依据 |
| 校验和保守生成 | 原证据仍可能支持已经删除的成果，保守分支复用旧结构 | 编辑后的事实以审核正文校验；保守分支不恢复旧动作、结果和技术列表 |
| 中文量化成果 | 原正则未覆盖“20 名用户”等数字 | 补充数字检测，回归测试拒绝将已改为 12 的人数恢复为 20 |
| 接受或保存事实 | PATCH 返回结构字段的 JSON 字符串，GET 返回对象 | 两者返回相同的解析结果和证据列表；保存后 STAR 详情保持显示 |
| 只切换审核状态 | 未提交 content 时清除已有编辑 | 保留已保存的编辑；原有显式空白恢复原文行为保持兼容 |
| 重复提取 | 删除已拒绝、已编辑或被历史记录引用的非接受事实 | 保留这些事实及 ID，避免审核选择丢失和引用悬空；相同候选不重复插入 |
| 补充问题 | 审核后需要手动刷新才显示问题 | 审核接口同步返回当前问题；页面支持查看历史回答、修改回答、恢复忽略的问题 |
| 冲突选择 | “保留选项 1”缺少选项映射，多个相同回答被合并 | 生成提示词携带原问题、选项值及对应事实 ID，不同冲突保持独立 |
| 批量上传部分失败 | 已成功材料未刷新，其余文件停止处理 | 继续处理后续文件，立即显示成功材料，报告成功数量和各失败文件原因 |
| 加载失败 | 页面像空工作室且没有重试入口 | 显示可读 HTTP 错误和“重新加载工作室”按钮 |
| 历史版本 | 只能下载，无法在页面预览或重新启用；同秒版本顺序随机 | 可切换预览及启用；同秒按插入顺序排列；新版本名称包含秒和短 ID |
| 窄屏 | 固定侧栏挤压工作区，内容被裁切 | 小屏采用顶部导航，工作区、按钮组自适应；浏览器检查 main 的实际内容宽度 |

原始提取结构仍作为审核证据显示，界面明确说明修改后以审核正文为准。已有生成版本是历史快照，修改事实或回答后需要重新生成。

## 验证方法

在项目根目录执行：

```powershell
uv run --with pytest --with ruff python -m pytest -q -p no:cacheprovider
uv run --with ruff ruff check src/bosshunter/resume_builder/service.py src/bosshunter/resume_builder/store.py tests/test_resume_builder.py scripts/verify_resume_studio_browser.py
uv run python scripts/verify_resume_studio_browser.py
# 使用本地配置的真实 AI，只有虚构测试材料会进入此流程：
uv run python scripts/verify_resume_studio_browser.py --allow-external-ai
```

浏览器脚本需要已安装 Google Chrome。脚本输出独立工作目录，其中保留 `report.json`、桌面及手机截图、Markdown/JSON 下载文件和演示数据库，可供复核。

前端构建从 `src/bosshunter/web/frontend` 执行 `npm run build`；仓库随附的 `dist` 已同步更新。

浏览器验收包含 10 组检查：首次加载失败重试、部分上传失败、重复上传、两类材料提取、保存后 STAR 结构、忽略恢复及回答重开、连续生成及历史启用下载、刷新后持久化、手机工作区完整显示、无浏览器脚本异常。

## 验证结果

- Windows / CPython 3.14.6：最终 Python 全量 **562 passed，23 subtests passed**，耗时 269.44 秒；简历工作室定向测试 **45 passed，4 subtests passed**。
- TypeScript + Vite 生产构建、目标 Ruff、`git diff --check` 通过。
- 固定 AI 回放通过全部 10 组浏览器验收，真实 HTTP/SQLite/文件读写参与执行。
- 真实 AI 复测通过全部 10 组浏览器验收：提取 18 条事实，连续生成 2 个版本，切换旧版本启用并成功下载 Markdown/JSON，未发生浏览器脚本异常；两轮提取各自动重试一次后成功。
- 真实 AI 下载版本的事实引用覆盖率为 18/18，校验警告为 0，质量报告仍列出 1 个未解决问题；这表示引用覆盖完整，不表示内容已完成所有人工确认。生成后页面重新加载问题队列，与质量报告保持同步。
- 本机最终真实 AI 报告位于 `output/resume-studio-93yyq4xn/report.json`，同目录有 `虚构主简历.md`、`虚构主简历.json` 和截图；运行报告确认真实配置 SHA-256 未改变。
- 演示 SQLite 的 `PRAGMA foreign_key_check` 无违规记录，2 个版本中仅 1 个启用。最终前端固定回放报告位于 `output/resume-studio-3jtz1doh/report.json`。

## 边界

这些用例验证了短篇中文材料和当前配置的模型，不代表所有长文档、扫描 PDF 或其他模型都能达到相同提取质量。数字与技术词校验也不等同于完整语义事实核验；生成后仍需按证据审核正文。演示验收不会投递简历或联系招聘方。

开发验收完成后，通过 `codex/resume-studio-usability` 分支向 `Colin-Cai0318/BossHunter` 的 `main` 提交 PR，代码和前端构建产物一起交付。该仓库未启用 Issues，问题说明和验证记录集中在 PR 与本文中。实际合并及 CI 状态以 GitHub PR 记录为准；这次交付不包含运行服务部署。
