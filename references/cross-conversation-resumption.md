# 精简跨对话续接

本协议只在工作预计跨对话、正在从本地状态恢复，或上下文丢失已经影响连续性时使用。它面向同一用户在不同 Codex 对话中继续同一需求：只持久化可复用的决定和结果，不保存 Agent 调度历史。

## 保存什么

默认布局：

```text
<spec-root>/
├── <feature-slug>/
│   ├── prd.md
│   ├── spec.md
│   ├── test.md
│   └── implementation.md
└── .workflow/<feature-slug>/
    ├── state.yaml
    └── grounding-pack.md  # 证据面复杂时可选
```

`state.yaml` 基于 [状态模板](../assets/workflow-state.yaml)，只保存：当前阶段、下一步、阻塞项、方向摘要、三份正式文档的批准与摘要、技术依据指纹、实施授权、实施结果以及两次强制审阅的有效结论。

不保存以下运行时信息：规划或实施任务包清单、任务包配额、Sub Agent 历史、替代关系、双向引用、进度消息和原始工具日志。任务可以在新对话中重建；只有绑定当前输入的完整结论可以复用。

方向不创建单独文件。阶段 1–2 只在 `direction.summary` 保存已呈现给用户的精简方向；`spec.md` 批准后将方向状态改为 `superseded`。在阶段 3 或更晚才启用检查点时，不回填方向文件或历史批准链。

需求工作区仍由 `workspace.yaml` 独立维护模块顺序和唯一活动模块。模块 `state.yaml.workspace` 只引用工作区路径与模块名，不复制 owner 或接管历史。

## 初始化与更新

首次确认需要跨对话时：

1. 归档原始 PRD，并创建 `state.yaml`；
2. 记录 PRD、适用项目规则和会使当前结论失效的最小仓库目标及其 SHA-256；
3. 记录当前阶段、下一步、阻塞项和已经明确取得的批准；
4. 三份文档均批准且技术依据有效时，计算唯一的 `plan_fingerprint_sha256`；
5. 证据面复杂且后续重新扫描成本明显时才创建 `grounding-pack.md`。

只在以下时机更新状态：准备跨对话暂停、用户明确批准、阶段或阻塞变化、正式文档或技术依据变化、实施开始或完成、独立审阅开始或返回完整结论。纯校验成功不重写文件。

文档检查点状态使用 `not_started | awaiting_approval | approved | reopened`。方向另允许 `superseded`。顶层状态使用 `in_progress | awaiting_approval | blocked | complete | invalidated`。时间使用带时区的 ISO 8601。

批准只记录产物路径、当前 SHA-256、时间和一句精简说明。编辑已经批准的文件后，不区分“编辑性刷新”旁路：更新摘要前先按核心 Skill 判断是否需要重新批准；需要时改为 `reopened`，不需要时直接更新摘要并在当前对话说明。

## 指纹

`repo_grounding.fingerprint_sha256` 由以下当前输入组成：

- PRD：`prd | <path> |  | <sha256>`；
- 项目规则：`<role> | <path> |  | <sha256>`；
- 关键仓库目标：`<role> | <path> | <symbol-or-empty> | <sha256>`。

路径使用状态中记录的字符串去除首尾空白。将 UTF-8 单行按整行排序、用换行连接后计算 SHA-256。可选 grounding pack 记录自己的内容摘要，并用 `input_fingerprint_sha256` 绑定该技术依据指纹。

三份文档均为 `approved` 且技术依据为 `current` 时，将下列映射按键排序、紧凑 JSON 编码后计算 `plan_fingerprint_sha256`：

```json
{"grounding":"<sha256>","implementation":"<sha256>","spec":"<sha256>","test":"<sha256>"}
```

该值是阶段 6 结论、实施授权与实施结果唯一需要引用的规划输入，不再复制四项摘要。

`implementation_result.changes` 只记录实际持久化产品目标：`present` 目标保存当前摘要，`removed` 目标的摘要为 `null`。将每项规范化为 `kind | path | role | sha-or-empty`，排序并以换行连接后计算 `output_fingerprint_sha256`。

终结代码审阅的 `input_fingerprint_sha256` 使用紧凑 JSON 的 SHA-256，输入为当前 `plan_fingerprint_sha256`、`output_fingerprint_sha256` 和 `verification` 列表。这样验证证据或输出变化都会使旧结论失效。

## 恢复算法

新对话按以下顺序恢复：

1. 读取当前 Skill、`state.yaml` 和恢复所需的仓库路由规则；工作区模块先按工作区协议核对活动模块；
2. 运行 `python3 <skill-dir>/scripts/check_resume_state.py <state.yaml>`；脚本只检查结构、路径、摘要、指纹和结果绑定，不裁决产品语义或 Agent 历史；
3. 返回 `fast_path` 时，只读取当前阶段正式文档、有效 grounding pack、下一步和阻塞项；不重放旧任务；
4. 返回 `targeted_revalidation` 时，只读取 `drift` 与 `errors` 指出的目标和受影响文档，由主 Agent判断变化是无关还是实质性；
5. 从最早未完成或失效阶段继续，不重复有效批准。

恢复时的最早阶段规则：PRD 或方向变化返回阶段 1；技术依据失效返回阶段 2；Spec、Test、Implementation 依次对应阶段 3、4、5；规划指纹或阶段 6 结论失效返回阶段 6；实施授权、实施结果或终结审阅未完成时保持阶段 7。

阶段 7 已授权实施会正常改变仓库目标。只要规划指纹仍等于授权输入，且变化已经准确登记在 `implementation_result.changes` 中，脚本将其视为预期输出，不把它误报为技术依据漂移；未登记变化仍需定向复核。

脚本只读并输出 JSON。退出码 `0` 表示可以快速恢复，`1` 表示存在内容漂移或结果绑定失效，`2` 表示状态结构无效。旧版 `requirement-spec/v1` 不继续兼容其任务包关系；迁移时从当前正式产物和可核实批准生成新的 v2 状态，不复制旧派发账本。

## 批准与旧任务迁移

`继续`、`接着做`、任务引用和进度询问只表示恢复，不构成新批准。

用户提供可访问的旧任务但没有检查点时，只读取足以恢复的最少回合。旧任务中的批准满足以下条件即可直接迁移，无需再要求一次“确认恢复”：

- 用户明确批准了可识别的方向或正式产物；
- 目标路径仍是当前目标；
- 当前文件摘要与批准时内容一致，或对话中能够直接证明批准针对当前内容；
- 没有更早输入变化使其失效。

批准语义含糊、目标无法对应或摘要已变化时，不迁移该批准，并从最早受影响节点继续。`recovery.migrated_from_tasks` 只记录来源任务引用，不复制对话内容。

Spec 批准后不再要求恢复或证明独立的方向检查点；Spec 是后续阶段的权威产品契约。旧工作流若使用历史顺序，只按三份正式文档的依赖关系判断：Spec → Test → Implementation；下游文档无法证明基于当前上游时重新打开对应阶段。

## 审阅与实施结果

阶段 6 和阶段 7 终结审阅各自只保留一个当前记录：状态、所绑定指纹、当前 reviewer 引用、尝试次数、结果类型、精简结论及结论摘要。运行时仍必须使用符合核心 Skill 的独立只读 Sub Agent，但不把只读、独立、任务模式等调度属性重复写入状态。阶段 6 的 `completed` 表示已有完整发现、正在等待返修决定；只有处理完发现后的 `passed` 才能授权实施。

完整结论且输入指纹未变化时直接复用。记录为 `in_progress` 的 reviewer 在新对话中不可继续、任务失败或没有完整结论时，覆盖 `reviewer_ref` 并对相同输入重新执行一次完整审阅，`attempt` 加一；不保存旧任务或替代关系。相同输入最多两次尝试，第二次仍不能形成完整结论时记为 `blocked`。输入发生变化时旧结论失效，新输入从 `attempt: 1` 重新开始。

阶段 6 的 `passed` 必须有绑定当前规划指纹的完整结论。需要修改正式文档时，仍按核心 Skill 获取返修决定；精简状态只在 `revision_decision` 保存决定及其所绑定的结论摘要。

实施授权只保存 `granted` 状态、当前规划指纹、时间和批准说明。实施结果为 `verified` 时，必须有完整变更清单、至少一个验证项且全部为 `passed`。终结审阅完成后，无论结果为 `findings` 还是 `no_findings`，都可以结束模块；后续修复仍由用户另行明确请求。

## 建议的续接请求

```text
使用 $complex-requirement-development-workflow 继续 <feature-slug>。
读取 <spec-root>/.workflow/<feature-slug>/state.yaml，
校验摘要和指纹，从最早未完成或失效阶段继续。
```

工作区模块只需再指出工作区和模块名；无需提供旧 owner 或任务包历史。
