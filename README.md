# Complex Requirement Development Workflow

一个面向 Codex 的复杂软件需求研发 Skill。它结合原始需求、用户确认、设计事实、仓库代码和项目规则，将尚未完全收敛的需求依次转化为 Spec、GWT 行为场景、精简实现思路和具体实施计划，并在明确授权后实现代码。

## 适用范围

适合已有代码仓库、需要产品决策与技术依据的复杂需求，例如跨界面状态、生命周期、接口集成、实验、Figma 或必须串行落地的多模块改造。它不绑定 Android、iOS、Web 或后端技术栈。

以下任务通常应使用更轻量的流程：

- 孤立 Bug 或单一 owner 内的局部修改；
- 仅按设计稿直接实现 UI；
- 产品行为、修改范围和实施方式均已明确的任务；
- 脱离现有代码仓库的纯产品脑暴或绿地架构设计。

## 工作流

Skill 按八个阶段推进：

1. 界定问题与方案方向；
2. 只读走查仓库，确认契约、实现约束和最小修改假设；
3. 编写并确认 `spec.md`；
4. 结合项目实际，将 Spec 契约展开为 GWT 行为场景，编写并确认 `test.md`；
5. 编写并确认 `technical-design.md`，用简短实现思路解释整体路径、关键设计与取舍；
6. 编写并确认 `implementation.md`，细化具体变更、执行约束和批次；
7. 由独立 Sub Agent 只读审阅四份文档；
8. 在用户再次明确授权后按编排逐批实现、验证，非最后批停下等待用户审阅；最后一批完成并记录必要验证后自动结束，不再安排独立代码审阅。

四份文档分别负责产品规则、行为场景、方案选择和具体变更。`test.md` 补充需求理解，不直接派生测试建设；必要验证由实施计划结合项目规则与风险确定。

方向、每份文档和产品代码实施分别确认；已有明确授权直接复用。“继续”表示恢复进度，不批准待审阅内容。文档修订、联合批准与复审见[文档审阅规则](references/document-review.md)；批次修订、必要实现补充、验证遗留与自动收尾见[实施规则](references/code-execution.md)。各指南按[SKILL.md](SKILL.md)路由读取，避免在此重复执行细则。

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

阶段产出以 `需求代号 · 阶段 N/8 · 名称 · 状态` 开头。首次阶段产出保存检查点（位于 `docs/spec/.workflow/`），后续可以说 `继续 <需求代号>`；多模块使用 `继续 <总需求代号>/<模块代号>`。阶段 8 同样支持跨对话续接：检查点保存当前批次、实际输出、验证和用户确认版本；非最后批待审阅时恢复后继续等待，实施中的批次从中断处接续，不因“继续”跳过审阅；最后一批已完成且结果记录完整时直接收尾。多模块目录不自动启用串行；仅技术依赖或用户安排要求同一工作树逐个完成时创建 `workspace.yaml`，此后由唯一活动槽控制顺序。细则见[需求工作区](references/requirement-workspace.md)。

新检查点使用 `requirement-spec/v4`，记录四份文档及八阶段。v2/v3 旧检查点按原阶段和指纹读取；未完成 v3 可沿旧流程续接，采用拆分方式时再按[迁移规则](references/cross-conversation-resumption.md#旧版检查点与迁移)重新确认新增文档，不自动迁移批准。

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
