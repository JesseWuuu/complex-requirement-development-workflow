# 多模块需求组织与串行工作区

在 PRD 包含可分别验收的业务模块、用户选择其中一项或提及后续模块时读取。先确定目录归属，再判断是否启用严格串行调度。模块是完整且平级的需求工作流，不是 Sub Agent 任务包。

## 先识别总需求与当前模块

- 概览整个 PRD，依据业务目标、流程边界和可独立验收的结果识别模块，无需原文带编号或模块标题。章节数量本身不足以证明多模块，同一功能的页面、接口、测试说明也可能只是不同技术面。此步不调查未授权模块的实现。
- 根据用户给出的功能名、页面名、自然语言描述或原文位置确定本次范围，无需补编号。“先做发布页，搜索以后做”只选择当前模块，不改变其总需求归属，也不授权提前生成其他模块的三份文档。仅提供整个 PRD 时先展示识别出的模块与建议处理顺序；只有现有说明无法确定本次范围时才询问，不默认从第一段开始。
- 多模块默认使用总需求目录及 `modules/<业务模块名>/`。总需求名来自整体业务目标，模块名来自各自业务职责；范围说明引用原文标题或内容位置，已有编号可辅助映射，无需另造模块编号。命名优先级按主 Skill 执行。
- 归属无法从 PRD 和用户说明确定时，在首次写入前聚焦询问；归属明确时直接说明目录。后续才发现同属一个需求时，按下方“创建、升级和兼容”处理既有产物。

## 再判断严格串行调度

启用 `workspace.yaml` 需要同时满足：

- 一个大需求包含两个或更多可以分别形成 Spec、测试和实施文档的模块；
- 技术依赖或用户明确的执行安排要求后续模块等前序模块完整结束后再开始；
- 用户希望在同一工作树中按模块逐个完成。

“后续开发”不单独证明上述完整流程的等待要求；只有准备启用调度且依据不足时才询问成员、顺序与等待边界。目录组织不等待这项决定。未启用调度时，各模块按各自授权推进，检查点的 `workspace` 字段留空，不创建 `workspace.yaml`，也不发明新的 `execution_mode`。同一模块内可并行的技术面仍使用 Sub Agent。

以下活动槽、成员状态与接管规则仅适用于已启用的严格串行工作区。

## 工作区只负责调度

工作区只保存：

- 共享原始 PRD 的路径；
- 模块顺序和状态；
- 当前活动模块、持有该模块的任务标识和取得时间；
- 下一步提示。

工作区不证明代码来源，不维护祖先文件摘要、输出清单、导出索引、决策账本或跨模块内容指纹。模块自己的 `state.yaml` 继续负责七阶段进度、批准、技术依据、实施结果和批次用户批准；Git 与当前工作树负责呈现实际代码状态。后续模块需要理解前序结果时，直接读取当前代码以及必要的前序正式文档，不从工作区复制产品或技术契约。

## 默认路径

以下结构适用于新建多模块需求；`requirement-slug` 是总需求名，启用串行时也用作 `workspace_slug`。PRD 的首次归档询问与移动按主 Skill 的“原始需求与输出位置”执行；已确认保留原位时，省略图中的 `prd.md`，所有模块引用原路径。尚未开始的模块只需说明其位置，不预生成文档。

```text
<spec-root>/
├── <requirement-slug>/
│   ├── prd.md
│   └── modules/
│       ├── <module-a>/
│       │   ├── spec.md
│       │   ├── test.md
│       │   └── implementation.md
│       └── <module-b>/
│           └── ...
└── .workflow/
    └── <requirement-slug>/
        ├── workspace.yaml          # 仅严格串行时创建
        └── modules/
            ├── <module-a>/state.yaml
            └── <module-b>/state.yaml
```

模块检查点基于 [state 模板](../assets/workflow-state.yaml)，默认位于 `.workflow/<requirement-slug>/modules/<module-slug>/state.yaml`，`feature_slug` 使用模块名。启用串行时再基于 [工作区模板](../assets/workspace.yaml) 创建调度文件，填写模块的 `workspace` 字段；各模块 `inputs.prd` 与工作区 `prd.path` 指向同一权威 PRD。相对工作区路径以 `workspace.yaml` 所在目录为基准解析。

## 成员状态

成员状态只使用：

- `queued`：已登记，尚未取得活动槽；
- `in_progress`：当前活动模块；同一工作区只能有一个；
- `blocked`：模块尚未完成，需要恢复或解决阻塞；
- `complete`：模块的非最后批均获用户批准，最后一批实施及必要验证结果已完整记录，流程自动结束；末批失败或条件不足的验证如实保留并报告，不要求额外确认，也不阻止释放活动槽。

未来的 `queued` 模块虽然尚未完成，但不会阻止排在它之前的模块执行。要激活某个模块，必须同时满足：

1. 所有序号更小的模块均为 `complete`；
2. 没有其他活动模块；
3. 当前任务原子地取得活动槽。

模块从首次激活起一直持有活动槽，包括等待文档确认、跨对话暂停、阶段 7 实施和非最后批等待用户审阅期间；只有模块完整结束后才释放。这样不会在同一需求链中穿插执行后续模块。

## 原子活动槽

为当前任务选择稳定的 owner 标识，优先使用 Codex task/thread ID；不可用时生成一个本模块内稳定的 run ID。owner 只记录在 `workspace.yaml.active_owner`，不复制到模块 `state.yaml`；同一任务的后续检查复用该值。

进入或恢复模块前运行：

```shell
python3 <skill-root>/scripts/assert_serial_workspace.py <workspace.yaml> \
  --module <module-slug> --action claim --owner <owner-id>
```

`claim` 在操作系统文件锁内完成“检查并占用”，避免两个任务同时看到空闲状态后一起启动。首次成功会把目标成员设为 `in_progress` 并记录 `active_module`、`active_owner` 和 `active_since`；同一 owner 再次调用是无写入的幂等恢复。退出码含义：

- `0`：成功取得或仍持有活动槽；
- `1`：前序未完成、其他模块活动或 owner 冲突；
- `2`：工作区结构或命令参数无效。

准备写产品代码或长时间暂停后继续前，再确认仍持有活动槽：

```shell
python3 <skill-root>/scripts/assert_serial_workspace.py <workspace.yaml> \
  --module <module-slug> --action check --owner <owner-id>
```

模块自己的 `state.yaml.status` 已在阶段 7 结束后记录为 `complete` 时，释放活动槽：

```shell
python3 <skill-root>/scripts/assert_serial_workspace.py <workspace.yaml> \
  --module <module-slug> --action complete --owner <owner-id>
```

`complete` 只核对当前 owner、模块身份和模块状态中的顶层 `complete`，然后更新工作区成员并释放活动槽；它不重复模块自己的批准、摘要和批次用户审阅校验。

只查看状态时使用 `--action status`，不需要 owner，也不修改工作区。

脚本会在工作区目录创建 `.workspace.yaml.gate.lock`，仅用于序列化短暂的状态更新；不要把它解释为另一个业务状态或手工删除来“解锁”。真正的持有状态在 `workspace.yaml` 的 `active_*` 字段中。

## 恢复与接管

同一 owner 可以直接恢复当前活动模块。新任务发现目标模块由旧 owner 持有时，先确认旧任务已经停止，且用户的请求确实是恢复同一模块；随后显式接管：

```shell
python3 <skill-root>/scripts/assert_serial_workspace.py <workspace.yaml> \
  --module <module-slug> --action claim --owner <new-owner-id> --takeover
```

`--takeover` 只能转移当前活动模块，不能跳过未完成的前序模块，也不能抢占另一个模块。无法确认旧任务已经停止时保持等待，不并发执行。

门禁失败只阻止当前任务开始或继续，不自动使模块的 Spec、测试、实施文档、阶段 6 结果或实施授权失效。内容是否漂移仍由模块恢复协议和技术依据校验判断，调度冲突不能冒充内容失效。

## 后续模块如何读取前序结果

成功取得活动槽后，后续模块按当前阶段需要读取：

1. 工作区中的成员顺序和当前进度；
2. 直接前序模块的当前正式文档、`state.yaml` 中的完成摘要和实际代码；
3. 共享 PRD 中与本模块有关的部分；
4. 当前模块适用的项目规则和仓库依据。

只读取形成当前判断所需的内容，不要求前序模块预先维护导出契约，也不递归验证所有祖先文件。发现前序代码或决策与当前需求实质冲突时，按当前模块的正常技术依据和确认规则处理；不要通过修改工作区摘要传播失效。

## 创建、升级和兼容

新工作区按已确定的执行顺序登记成员，序号从 1 连续递增。已有单模块流程归入总需求或启用串行时，先展示已有路径与目标结构；没有迁移授权则保留原交付目录和检查点路径，串行工作区可直接登记原路径。用户已明确要求迁移到所展示结构时直接执行，不重复索要许可。目录整理不扩展业务范围，也不批准后续模块。

迁移须覆盖 PRD、交付物和检查点中实际需要移动的目标，更新相对链接、`delivery_dir`、`workflow_dir`、成员路径和输入引用，再按续接协议校验内容摘要及受影响的绑定；保留真实批准状态，不能靠刷新绑定伪造新批准。原地保留的旧流程不因新命名或嵌套目录规则失效。

旧工作区中的 `predecessor_result_sha256`、`inherited_baseline_sha256`、`exports`、`serial_assertion` 和决策账本字段不再参与门禁判断。无需为了迁移一次性改写历史文件；后续正常编辑模板或成员时可以删除这些遗留字段。旧工作区若已经有活动模块但缺少 `active_owner`，先确认原任务状态并补充或显式迁移 owner，再执行门禁。

不同任务可以承载不同模块，但必须指向同一 `workspace.yaml` 和包含前序改动的同一工作树。任务标题、聊天消息和“前一个模块完成了”的自然语言不能替代工作区成员的 `complete` 状态。

## 建议请求

创建工作区：

```text
使用 $complex-requirement-development-workflow 创建 <workspace-slug> 需求工作区，
按 <module-a> → <module-b> 的顺序串行完成；每次开始或恢复模块前先取得工作区活动槽。
```

恢复模块：

```text
使用 $complex-requirement-development-workflow 继续 <workspace-slug> 的 <module-slug>。
先取得或核对该模块的工作区活动槽，再从模块自身最早未完成阶段继续。
```
