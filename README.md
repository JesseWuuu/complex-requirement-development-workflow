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

方向、每份文档和产品代码实施分别确认。回复“继续”只恢复流程，不会被解释为批准。

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

可以同时提供 Figma 链接、相关任务、期望交付目录，以及多模块需求的模块列表和依赖顺序。默认交付物位于：

```text
<git-root>/docs/spec/<feature-slug>/
```

阶段产出以 `需求代号 · 阶段 N/7 · 名称 · 状态` 开头，普通需求代号就是交付目录名。首次阶段产出会保存精简检查点（默认位于 `docs/spec/.workflow/`），后续对话可以直接说 `继续 <需求代号>`；无法唯一定位时再补充项目或完整路径。多模块使用 `需求代号/模块代号`，严格串行，同一时刻只有一个模块可以处于实施流程中。

能读取当前对话可靠的上下文窗口统计时，每次阶段输出末尾附上 `上下文窗口已使用：约 P%（最近一次运行时统计）`；读取不到时省略，不用账户额度或估算值替代。

## 目录结构

```text
.
├── SKILL.md                 # Skill 入口、阶段和授权规则
├── agents/openai.yaml       # Codex UI 元数据
├── assets/                  # 检查点、工作区与技术依据模板
├── references/              # 按阶段加载的详细规则
└── scripts/                 # 状态校验与串行工作区门禁
```

## 验证

从 Skill 根目录运行：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

如果本机安装了 Codex 的 `skill-creator` 系统 Skill，还可以使用其中的 `quick_validate.py` 检查 Skill 的名称、frontmatter 和脚手架完整性。

## License

本项目采用 [MIT License](LICENSE)。
