# 材料导入与 Intake v1

Intake 是研究前的准备工作流。用户提供自然语言目标及本地 UTF-8 的 `.txt`、`.md`、
`.markdown`、`.tex` 文件。同一核心模型把材料组织成待审阅任务书；程序保存原件、
核对引用和发布草案版本，不创建 ResearchStore、研究图、证据或研究执行任务。
PDF、OCR、正式入库和研究图分解不属于 v1。

## 操作

在项目根目录执行；`--draft` 是独立草案目录，禁止选择正式 `research/`、`.amre/`
或工作区根目录。所有输入文件路径和草案目录相对于当前目录解释。

```powershell
amre intake import --draft intake/my-problem examples/intake/old-proof.md
amre intake prepare --draft intake/my-problem --request "请整理平方根 2 无理性的研究任务书，沿用旧笔记的互素条件，保留尚未展开的步骤。"
```

`prepare` 返回 `request_id` 和可阅读的请求文件路径，不启动模型。可将请求交给当前使用的
核心模型，要求直接返回 `DraftContent` JSON，然后保存答复并导入：

```powershell
amre intake accept --draft intake/my-problem --request-id I-实际请求ID --response response.json --model 当前核心模型
amre intake show --draft intake/my-problem
amre intake prepare --draft intake/my-problem --request "目标保持不变。明确列出奇偶性引理为待完成步骤，不把旧证明当成已经核验的结果。"
```

使用新的请求 ID 和新答复再次 `accept`，得到下一版本。旧版的三个文件保持不变。
外部答复按原字节归档，模型身份仅为用户声明，使用量 UNKNOWN；该入口不伪装为实时模型执行。
`accept` 的含义是通过程序校验后接收草案，不是用户已确认合同或数学证明。

也可以在**独立终端、已有模型授权**的环境执行真实适配器：

```powershell
amre intake prepare --draft intake/my-problem --request "整理此问题" --timeout 120 --max-tokens 8000
amre intake generate --draft intake/my-problem --request-id I-实际请求ID --adapter codex --live
```

只有 `generate --adapter codex --live` 启动模型。沿用 CodexCLIAdapter 的隔离临时目录、
只读沙箱、禁用工具及禁用嵌套执行边界；与研究执行共用单工作者锁。每个请求最多派发一次，
派发前写 RUNNING 回执，中断或失败也保留记录。RUNNING 只是历史记录，不证明进程仍存活；
恢复时先检查实际进程和记录，不自动重试可能已计费的请求。

时间与输出字节由适配器限制；token 上限按服务回报在执行后检查，并非供应商硬计费上限。
token 使用未知、超限、工具调用或非法响应都拒绝发布，保留回执。未经单独授权不运行真实服务。
外部会话的预算由外部会话负责，不声称由 `accept` 强制执行。

## 草案内容与出处

`DraftContent` 与正式合同分离，可有尚未解决的假设和依赖，不伪造现有图节点 ID。
包含目标（允许 LaTeX）、假设、定义、已有依赖、旧材料结果、缺口、验收条件和待确认问题。
每项有稳定 ID，并标记 `user`、`material`、`model_suggestion` 或 `unresolved`。

- 用户和材料内容必须引用归档来源 ID、起止行与完整原文；程序核对原文和来源种类。
- 模型建议和未决内容必须解释原因。问题列表只能包含未决项。
- 状态固定为 `DRAFT_UNVERIFIED`，不存在设置为已证明的字段。
- 对引用的校验只证明文本出处匹配，不证明模型转述等价、材料完整或证明正确。
  审阅时仍须核对是否擅自加条件、改变量词或缩小结论。
- 新材料版本不会覆盖旧原件；同名文件不同内容保留不同哈希和来源 ID。
  有冲突时由草案指出，不能静默合并。

原始文件保存原字节（包括 BOM、CRLF）。引用行号针对 UTF-8 解码并统一换行后的文本。
每份材料最多 512 KiB，每个草案最多 100 个来源（包括修订指令）。
默认输入任务和输出分别限 256 KiB，可显式调整至 2 MiB；超出上下文预算直接报错，
不静默丢弃任何选定来源。当前版本包含此前请求和当前已导入的全部材料；大问题应拆分草案。
凭据样式内容沿用现有脱敏检查并拒绝导入/发布，识别机制不是完整的秘密扫描器。

## 文件与恢复

`manifest.yaml` 是唯一已发布版本清单。每次导出目录为 `versions/v0001-I-请求ID/`：

- `draft.yaml`：来源快照、草案内容、生成记录、父版本和程序计算的字段变化。
- `statement.tex`：目标、假设和定义，各项带来源类别注释；只是未验证片段，不自动编译。
- `review.md`：供人阅读的任务书、逐项出处、建议和问题、前后版本差异。

`sources/` 保存按 SHA-256 寻址的原件，`requests/` 保存模型实际输入，`responses/` 保存
外部原始答复，`runs/` 保存真实适配器派发回执。不要把这些私有材料自动公开。
`show --version N` 校验三个文件的哈希并查看指定版本。

程序先写不可变文件，再原子更新清单。发布中断时仍读取上一版本；未发布文件不视作成果。
新的准备请求可以恢复后续工作。旧请求基于的版本已经变更时，拒绝覆盖，要求重新准备。
变化记录由稳定 ID 对比生成，包含旧值、新值及归属类别；模型自行描述的“未改变条件”不作为依据。

## 验收入口

`pytest tests/test_intake.py -q` 覆盖归档、引用、版本修订、预算、草案边界、模型适配器回执、
单工作者互斥、中断不重复派发及发布崩溃恢复。测试中的模型使用显式替身，不作为真实服务证据。
`examples/intake/old-proof.md` 是可阅读的真实数学问题笔记示例，不是用户私人研究成果。
完整验收产物和真实服务验证状态见实施状态。
