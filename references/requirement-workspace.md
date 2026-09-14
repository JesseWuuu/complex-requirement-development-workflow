# 严格串行的需求工作区

本参考文件只在用户要求创建、加入、恢复或扩展需求工作区，或者多个模块必须依次落地时读取。工作区成员是完整且平级的需求工作流，不是 Sub Agent 任务包。

## 适用范围

使用需求工作区需要同时满足：

- 一个大需求包含两个或更多可以分别形成 Spec、测试和实施文档的模块；
- 模块存在明确先后顺序，后续模块必须等前序模块结束后再开始；
- 用户希望在同一工作树中按模块逐个完成。

可以独立完成的技术面继续使用实施编排或 Sub Agent 任务包，不为它们创建串行工作区。

## 工作区只负责调度

工作区只保存：

- 共享原始 PRD 的路径；
- 模块顺序和状态；
- 当前活动模块、持有该模块的任务标识和取得时间；
- 下一步提示。

工作区不证明代码来源，不维护祖先文件摘要、输出清单、导出索引、决策账本或跨模块内容指纹。模块自己的 `state.yaml` 继续负责七阶段进度、批准、技术依据、实施结果和终结代码审阅；Git 与当前工作树负责呈现实际代码状态。后续模块需要理解前序结果时，直接读取当前代码以及必要的前序正式文档，不从工作区复制产品或技术契约。

## 默认路径

以下展示新建交付物的默认目录；已有本地 PRD 可以留在原路径，由工作区和全部模块共同引用。

```text
<spec-root>/
├── <workspace-slug>/
│   ├── prd.md
│   └── modules/
│       ├── <module-a>/
│       │   ├── spec.md
│       │   ├── test.md
│       │   └── implementation.md
│       └── <module-b>/
│           └── ...
└── .workflow/
    └── <workspace-slug>/
        ├── workspace.yaml
        └── modules/
            ├── <module-a>/state.yaml
            └── <module-b>/state.yaml
```

`workspace.yaml` 基于 [工作区模板](../assets/workspace.yaml)。每个模块的 `state.yaml` 基于父级状态模板，`inputs.prd` 与工作区 `prd.path` 指向同一权威 PRD，既可以是原有本地路径，也可以是工作区根部归档路径；不得拆分或复制模块级 PRD。相对工作区路径以 `workspace.yaml` 所在目录为基准解析。

## 成员状态

成员状态只使用：

- `queued`：已登记，尚未取得活动槽；
- `in_progress`：当前活动模块；同一工作区只能有一个；
- `blocked`：模块尚未完成，需要恢复或解决阻塞；
- `complete`：模块自己的代码实施、验证结果记录和终结只读审阅已完成，七阶段流程已经结束；验证失败或条件不足的项目保留在模块检查点和最终报告中，不阻止流程收尾及活动槽释放。

未来的 `queued` 模块虽然尚未完成，但不会阻止排在它之前的模块执行。要激活某个模块，必须同时满足：

1. 所有序号更小的模块均为 `complete`；
2. 没有其他活动模块；
3. 当前任务原子地取得活动槽。

模块从首次激活起一直持有活动槽，包括等待文档确认、跨对话暂停和阶段 7 实施期间；只有模块完整结束后才释放。这样不会在同一需求链中穿插执行后续模块。

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

`complete` 只核对当前 owner、模块身份和模块状态中的顶层 `complete`，然后更新工作区成员并释放活动槽；它不重复模块自己的批准、摘要和代码审阅校验。

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

新工作区按模块依赖顺序登记成员，序号从 1 连续递增。将既有单模块工作流升级为工作区时，保留原交付目录和状态路径，把它登记为第一个成员，再登记后续模块；只有用户明确授权时才移动既有产物。移动已绑定输入或产物后按模块续接协议核对路径、摘要及受影响的批准；不能仅刷新绑定保留已经失效的授权。

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
