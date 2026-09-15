# Complex Requirement Development Workflow

一个面向 Codex 的复杂软件需求研发 Skill。它结合原始需求、用户确认、设计事实、仓库代码和项目规则，将尚未完全收敛的需求依次转化为可执行的 Spec、测试方案和最小实施方案，并在明确授权后实现代码。

## 适用范围

适合已有代码仓库、需要产品决策与技术依据的复杂需求，例如跨界面状态、生命周期、接口集成、实验、Figma 或必须串行落地的多模块改造。它不绑定 Android、iOS、Web 或后端技术栈。

以下任务通常应使用更轻量的流程：

- 孤立 Bug 或单一 owner 内的局部修改；
- 仅按设计稿直接实现 UI；
- 产品行为、修改范围和实施方式均已明确的任务；
- 脱离现有代码仓库的纯产品脑暴或绿地架构设计。

## 工作流

Skill 按七个阶段推进：

1. 界定问题与方案方向；
2. 只读走查仓库，确认契约、实现约束和最小修改假设；
3. 编写并确认 `spec.md`；
4. 编写并确认 `test.md`；
5. 编写并确认 `implementation.md`；
6. 由独立 Sub Agent 只读审阅三份文档；
7. 在用户再次明确授权后实现、验证，并由全新 Sub Agent 完成终结只读代码审阅。

方向、每份文档和产品代码实施分别确认。阶段 6 的明确编辑性修正可在已有文档授权内直接完成并展示差异，随后重新审阅；实质返修仍需处置决定和文档重批。回复“继续”只恢复流程，不会被解释为批准。

每轮文档返修都在被修改文档内追加“修订记录”，写明日期、原因、涉及章节或既有 ID 和具体改动摘要，并保留已有记录。此要求覆盖编辑性修正与实质返修，检查点或对话说明不能替代文内记录。

已授权实施中的必要局部补充可以继续并记录实际输出，不因增加配套文件重复审批；实质技术方案或产品边界变化才暂停受影响工作并返回对应阶段。代码实施完成后的验证失败或条件不足只记录，不自动修复，也不阻止终结代码审阅，最后与审阅结果一并报告。检查点以 `implemented` 表示代码完成且验证结果已记录，以 `verified` 表示验证全部通过；两者均可进入审阅并完成流程。

## 安装

将整个目录复制或克隆到 Codex 的个人 Skills 目录，并保持目录名不变：

```text
~/.codex/skills/complex-requirement-development-workflow/
```

完整工作流需要运行环境支持 Codex Skills 和 Sub Agent。辅助脚本需要 Python 3.10+ 与 PyYAML；严格串行工作区门禁脚本使用 `fcntl`，当前支持 macOS 和 Linux。

```bash
python3 -m pip install -r requirements.txt
```

## 使用

在 Codex 中显式调用 Skill，并提供原始需求文本或 PRD：

```text
$complex-requirement-development-workflow

请基于当前仓库处理以下需求：……
```

首次创建交付目录前，按业务内容识别总需求与可独立验收的模块，无需标注编号或模块标题。“先做发布页，搜索以后做”这类描述即可限定当前范围；只给整个 PRD 时先梳理模块和建议顺序，范围有歧义才询问。总目录和模块目录默认使用业务语义名。可以同时提供 Figma 链接、相关任务、期望交付目录，以及模块列表和执行顺序。默认交付物位于：

```text
<git-root>/docs/spec/<feature-slug>/                              # 单模块
<git-root>/docs/spec/<requirement-slug>/modules/<module-slug>/    # 多模块
```

本地 PRD 位于新建总需求目录外时，会先展示目录结构与源/目标路径，再一次性询问归档选择。已有选择直接复用；待确认时原地引用并继续分析，得到授权后才移动并更新链接与绑定。所有模块共享同一权威 PRD，不创建模块副本。

阶段产出以 `需求代号 · 阶段 N/7 · 名称 · 状态` 开头。首次阶段产出保存检查点（位于 `docs/spec/.workflow/`），后续可以说 `继续 <需求代号>`；多模块使用 `继续 <总需求代号>/<模块代号>`。多模块目录不自动启用串行；仅技术依赖或用户安排要求同一工作树逐个完成时创建 `workspace.yaml`，此后由唯一活动槽控制顺序。细则见[需求工作区](references/requirement-workspace.md)。

运行时直接提供当前对话可靠的上下文窗口统计时，可在阶段输出末尾展示；否则省略。

## 目录结构

```text
.
├── SKILL.md                 # 适用范围、核心授权规则与按阶段路由
├── agents/openai.yaml       # Codex UI 元数据
├── assets/                  # 检查点、工作区与技术依据模板
├── references/              # 技术依据、文档写作、独立审阅、实施及续接规则
└── scripts/                 # 只读状态校验与串行工作区门禁，以及对应测试
```

只保留两类脚本辅助：`check_resume_state.py --context <state.yaml>` 校验并返回当前阶段输入，需要计算摘要时加 `--fingerprints`；`assert_serial_workspace.py` 仅用于多模块工作区的原子抢占、校验和释放。状态由主 Agent 按事实直接编辑，操作见 [续接参考](references/cross-conversation-resumption.md#更新与校验)。批准、返修和阶段推进由 Skill 规则负责，同一对话内复用未变化且仍可用的材料。

## 验证

从 Skill 根目录运行：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

如果本机安装了 Codex 的 `skill-creator` 系统 Skill，还可以使用其中的 `quick_validate.py` 检查 Skill 的名称、frontmatter 和脚手架完整性。

## License

本项目采用 [MIT License](LICENSE)。
