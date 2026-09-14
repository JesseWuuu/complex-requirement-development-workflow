# 跨对话续接

首次阶段产出给出续接提示前，或预计跨对话、上下文丢失已影响连续性时，保存本地检查点。本文只定义字段记录与恢复操作；授权节点见主 Skill，文档返修见[文档独立审阅](document-review.md)，实施与完成边界见[实施规则](code-execution.md)。

## 检查点

默认路径为 `<spec-root>/.workflow/<feature-slug>/state.yaml`，结构沿用 [workflow-state.yaml](../assets/workflow-state.yaml) 的 `requirement-spec/v2`，已有有效检查点无需迁移。

- 保存当前阶段、下一步、阻塞项、三份文档的批准与摘要、技术依据、实施授权、实施结果和两次审阅结论。
- Spec 批准前，方向只在 `direction.summary` 保存精简摘要；批准后改为 `superseded`，不创建独立方向文件，也不回填历史批准链。
- 证据复杂且能明显减少重复调查时，在同目录保存可选 `grounding-pack.md`；通过 `repo_grounding.evidence_pack` 记录路径、摘要和依据指纹。
- 工作区的顺序与活动模块归 `workspace.yaml` 管理；模块检查点只记录工作区路径和模块名。

正式决策留在三份文档。检查点不保存 Agent 任务清单、替代关系、进度日志或读取历史。

## 更新与校验

主 Agent 直接按模板编辑检查点，只修改本次变化的字段。阶段、批准、阻塞、正式输入、实施结果或审阅结果变化时更新；纯读取和校验成功不重写文件。时间使用带时区的 ISO 8601。

批准记录必须来自明确的用户确认，并绑定用户实际审阅的文件版本。修订稿用 `awaiting_approval` 或 `reopened`；获得批准后再记录 `approved`、时间和说明。明确的编辑性修正按[返修分级](document-review.md#返修分级)保留原批准说明和时间，并在对应 `approval_note` 中追加修正范围与等义依据，再更新文档摘要和规划指纹、使旧审阅失效；不记录不存在的新用户批准。实质修订不能仅刷新摘要保留旧批准。

更新后运行只读校验；需要计算摘要时加 `--fingerprints`：

```shell
python3 <skill-dir>/scripts/check_resume_state.py --context --fingerprints <state.yaml>
```

输出中：

| 字段 | 用途 |
| --- | --- |
| `context` | 无漂移时的当前阶段输入、下一步、阻塞项、批准与审阅引用 |
| `fingerprints.files` | 已登记路径的当前文件 SHA-256；缺失或不可读为 `null` |
| `fingerprints.recorded_inputs` | 根据检查点中**已记录的摘要**计算的技术依据、规划、输出和终结审阅输入指纹；键对应状态字段 |
| `fingerprints.review_conclusions` | 已保存审阅结论正文的 SHA-256 |

首次建档或文件变化时，按文件摘要、依据/输出指纹、规划指纹、审阅绑定的依赖顺序补齐，只重算受影响部分，最后校验。聚合值使用已记录的输入，上游字段更新后再取下游值；仅状态变化时复用未变化的摘要。计算结果不会写回文件，也不代表批准；无需另写计算脚本或读取校验器源码。

技术依据聚合 PRD、适用项目规则与最小关键仓库目标；规划指纹聚合三份已批准文档与技术依据。阶段 6 结论、实施授权和实施结果都绑定该规划指纹。终结审阅还绑定实际输出和验证结果。正常实施产生的代码变化登记在 `implementation_result.changes`，保留原规划依据；不要把 `fingerprints.files` 全量覆盖到依据记录。

退出码 `0` 表示结构与绑定校验通过，`1` 表示漂移或失效绑定，`2` 表示结构无效。发生漂移可以先如实保存阻塞与失效状态，再定向复核；校验失败时不能把旧批准当成当前授权。脚本不裁决用户意图、发现严重度或验证是否充分。

## 恢复

1. **定位。** 在已知项目的用户指定、项目约定或默认 `docs/spec/` 根目录，先检查 `<代号>/` 与 `.workflow/<代号>/state.yaml`。需要搜索时用 `rg --files --hidden --no-ignore <限定目录>`。自定义路径按 `delivery_dir` 核对；工作区按 `workspace.yaml` 的成员路径定位，并按工作区协议检查或取得活动槽。只给工作区代号时，恢复唯一活动模块或顺序上首个未完成成员。无法唯一定位时只询问项目或路径，不新建同名工作流。
2. **校验。** 读取当前 Skill、本参考与所需项目规则，运行 `check_resume_state.py --context <state.yaml>`，不默认全文读取状态。
3. **补读。** `fast_path` 时按 `context.inputs` 补齐当前阶段材料；有 `design_targets_ref`、审阅结论或结果引用时按需读取。当前对话已加载的同版材料直接复用。
4. **处理漂移。** `targeted_revalidation` 时只复核 `drift`、`errors` 与 `revalidation_inputs` 指出的目标，由主 Agent 判断影响并从最早未完成或失效阶段继续。候选阶段不是全量重跑命令。

PRD 或方向变化对应阶段 1；技术依据对应阶段 2；Spec、Test、Implementation 对应阶段 3、4、5；规划或阶段 6 结论失效对应阶段 6；实施授权、结果或终结审阅未完成对应阶段 7。已授权且准确登记的实施输出不视为依据漂移，仍须核实其符合[实施规则](code-execution.md)的授权范围。

`继续`、任务引用和进度询问只表示恢复。没有检查点或需要从旧版迁移时，只读取足以核实的旧任务记录：明确批准、目标路径和当前文件版本均能对应，且没有更早输入变化使其失效，才迁移批准；仅有文档不能证明批准。来源可记入 `recovery.migrated_from_tasks`，不复制任务历史。

## 审阅与实施记录

两次审阅分别使用 `consistency_review` 与 `post_implementation_review`。开始时记录输入指纹、reviewer、`in_progress` 和 `attempt`；返回后记录 `outcome`、完整 `conclusion`、结论摘要与完成时间。完整结论绑定当前输入时复用；失败、丢失和复审按 [委派参考的审阅恢复](subagent-delegation.md#审阅恢复) 执行。

阶段 6 的 `completed` 表示审阅已返回；满足[文档审阅通过条件](document-review.md#返回阶段与复审)后记为 `passed`，保留实际发现与结论。用户明确授权实施后记录 `implementation_authorization` 的 `granted`、当前规划指纹、时间与批准说明。

仅有明确编辑性修正时，`revision_decision.status` 保持 `not_required`；仍须按当前内容重新审阅。实质返修尚待决定时保存 `awaiting_decision`、返回阶段、受影响文档与结论摘要；明确授权或拒绝后记录 `authorized` 或 `declined`、时间与说明。`awaiting_decision` 默认不能与审阅 `passed` 或实施授权 `granted` 共存。输入漂移不清除尚待处理的返修决定或原结论，不通过重置状态绕过确认。

仅当 `current_phase: 7`、`implementation_result.status: in_progress`，原 `passed`、`granted` 与实施结果仍绑定未变化的当前规划，且尚未开始终结审阅时，可保留原审阅和授权。此时只允许[实施规则](code-execution.md#实施中的变更分级)中不依赖待决事项且原授权仍有效的工作继续；受影响范围和下一步记入现有 `open_blockers` 与 `next_action`，无法隔离影响时扩大暂停范围。阶段号或 `in_progress` 标记不能追认授权；正式规划或依据变化仍使原授权失效。

实施开始时记录 `in_progress` 及当前授权的规划指纹；`changes` 保存实际持久化产品目标，存在的目标记 `present` 与摘要，删除目标记 `removed` 与 `null`。必要实现补充在 `role` 中关联既有 `I-*` 和职责，其依据、影响与验证记入 `verification` 或引用已有工作笔记。

代码完成后补齐变更清单、输出指纹、完成时间和全部批准验证项的真实结果，并设置 `output_manifest_complete: true`。`implemented` 允许验证为 `failed` 或 `blocked`，`verified` 要求全部 `passed`。两者都进入终结审阅；有效结论返回后将流程记为 `complete`，保留失败及未验证项。旧状态仅因验证问题阻塞时，核实代码已完成后按此记录并继续审阅。

正式方案变化时保留已落盘输出、使旧授权和相关结果失效；按[文档返修规则](document-review.md)完成修订、重批、复审与新授权后再继续实施。结果重新绑定当前授权，重新记录新规划下的验证，旧验证不能冒充当前验证。

## 续接句式

首次阶段产出说明代号与交付目录，并给出 `继续 <需求代号>`；工作区模块用 `继续 <需求代号>/<模块代号>`。跨项目重名或自定义目录时补充项目、交付目录或检查点路径即可，无需提供旧 Agent 历史。
