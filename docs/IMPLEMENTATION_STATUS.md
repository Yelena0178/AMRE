# AMRE 实施状态

阶段状态为 PLANNED、IN_PROGRESS、BLOCKED 或 DONE。DONE 需要对应验收通过并记录命令与结果。
Deep Research 新增验收以 [阶段计划](PHASE_PLAN.md)为准。阶段 04–05 的既有回归证据仍适用。

| 阶段 | 交付 | 状态 | 验收标准 | 阻碍或前置 | 下一步 |
| --- | --- | --- | --- | --- | --- |
| 01 | 工程和运行框架 | DONE | 安装后 `amre --help`、`amre doctor`、`pytest`、`ruff check` 均成功 | 本地验收无阻碍 | 保持回归测试 |
| 02 | Research Contract、研究图、证据和假设格 | DONE | 真实 CLI 创建、查询、校验和更新，文件重载、AND/OR 及归约证据校验、假设关系、失效传播、阶段 01 回归与 Ruff 均通过 | 本地验收无阻碍 | 保持回归测试 |
| 03 | 持久研究记忆、检查点和 Context Builder | DONE | 跨进程冷重启、事件重放、崩溃恢复、引用与版本检查、索引重建、上下文预算和全量回归 | 本地验收无阻碍 | 保持回归测试 |
| 04 | 默认深度研究与执行编排 | DONE | 连续 Mock、运行中保存、暂停与停止、崩溃恢复、硬超时和重复结算验收 | 真实 Codex 服务与原生接续 UNVERIFIED | 保持离线回归 |
| 05 | 会话内数学 Skill 与障碍记忆 | DONE | 七种 Skill 及一次派发内的引理、方法切换与原始工件保存 | 真实服务策略表现 UNVERIFIED | 提供会话内文献工具 |
| 06 | 宏观分支搜索和预算调度 | DONE | A/B/C、路线保护、停滞、预算、派生、确定性重放与中断恢复 | 本地 Mock 验收 | 将文献证据纳入研究上下文 |
| 07 | 文献智能与自主选题 | DONE | 书目去重、来源核对、图谱失效、四类选题及同一会话离线查询 | 在线服务与真实 Codex 工具传输 UNVERIFIED | 保持离线回归 |
| 08 | GR 专业计算与物理一致性 | DONE | 曲率、波动能量、数值检验、证明义务、同会话工件与证据 | 外部高级后端与严格区间计算未配置 | 将计算档案纳入 Dashboard |
| 09 | 研究 Dashboard 与 GitHub Pages | DONE | 公开快照、交互图谱、深度会话、脱敏、静态构建与浏览器验收 | GitHub Pages 实际部署未运行 | 保持本地回归 |
| 10 | 策略基准与 v1.0 验收 | DONE | 三题四组对照、策略评估与回退、跨进程恢复、报告、静态导出和本地发行 | 真实模型与远程发布 UNVERIFIED | 见发行验收 |
| Intake v1 | 材料导入与研究任务书草案 | DONE | 原件归档、出处校验、两版草案、预算与恢复、CLI 和全量回归 | 真实 Codex 服务单独 UNVERIFIED | 保持回归；真实服务须单独授权 |

## 2026-11-11 · Intake v1 实施记录

授权范围见 [阶段计划](PHASE_PLAN.md) 的 Intake v1。新增 `src/amre/intake/`，复用现有
ModelAdapter，只在任务中新增默认 research 的 purpose 字段，并为 intake 选择草拟指令。
原研究提示、沙箱和执行行为保留，相关参数回归同时覆盖两种用途。

交付与恢复入口：

- `amre intake import/prepare/accept/generate/show`，详见 [使用说明](INTAKE.md)。
- 原件、来源版本和逐行引文；独立草案结构与四类来源归属；不升级任何证明证据。
- 不可变版本、前后字段变化、过期拒绝及原子发布恢复。
- `draft.yaml`、`statement.tex`、`review.md` 和可恢复请求/答复/派发回执。
- ADR-002 用途修订、ADR-024 流程边界及同步的 AGENTS.md。

运行环境：Windows PowerShell、项目 `.venv`、Python 3.13.5；未安装新依赖。

### 真实数学问题的准备流程验收

使用 `examples/intake/old-proof.md` 中平方根 2 无理性的既有反证法笔记。
原件导入后，当前核心模型阅读导出的请求，生成首版，再依据一条演示用自然语言修订生成第二版。
两次 `intake accept` 和 `intake show --version 2` 实际执行成功；输入及原始答复均保留。
这不是用户私人研究的导入，也不声称用户确认了演示内容。

产物见 [验收说明与两版草案](../examples/intake/README.md)。逐项核对结果：

| 要求 | 当前证据 |
| --- | --- |
| 原件和来源版本 | 原始笔记按字节归档；来源 ID、SHA-256、行号与引用原文一致 |
| 目标与假设不被悄悄改变 | 两版的 Goal、ContradictionAssumption 和 OldArgument 完全一致 |
| 建议与未决项明确区分 | 首版验收条件为 model_suggestion，验收方式问题为 unresolved |
| 自然语言修订有效 | 第二版细化 ParityGap，按指令调整 CompleteProof，移除已解决的 ReviewMethod |
| 不把缺口升级为假设或已证结论 | 奇偶性引理仅在 gaps；两个版本均为 DRAFT_UNVERIFIED |
| 三文件与历史 | 两版均有三文件，清单包含工件哈希及父版本，自动测试重新核对 |
| 研究边界 | 正式 research 目录、研究图与证据未被 Intake 写入；测试明确断言隔离 |

模型路径为当前会话的外部答复导入，身份按声明记录。没有启动嵌套 Codex 或新增付费模型调用。
`generate --adapter codex --live` 的真实服务运行保持 UNVERIFIED；其提示、参数、共享锁、
超时/失败回执与预算拒绝使用离线替身和已有子进程传输测试验证，不冒充服务端联调证据。

### 验证记录

- 初次全量回归：`python -m pytest -q --tb=short`，146 passed in 353.77s。
- 后续补充文本编码、原件换行保护、适配器用途分支及示例验证；相关命令
  `pytest tests/test_intake.py tests/test_execution.py::test_codex_arguments_and_jsonl_with_fake_transport tests/test_cli.py -q --tb=short`，34 项通过。
- `ruff check`、受影响文件 `ruff format --check`、`pip check` 均通过。
- 最终全量回归：`python -m pytest -q --tb=short`，153 passed in 338.22s。
- 已安装入口：`.venv/Scripts/amre.exe intake show --draft examples/intake/acceptance --version 2`，
  成功读取第二版；两版工件哈希及逐行引用再次通过核对。
- `git check-attr text` 确认原件与已发布草案均禁用换行转换；`git diff --check` 通过。

Intake v1 按其授权边界验收完成，标为 DONE。真实服务联调未作为本次验收的替代或隐含结论。

后续实际服务运行需要单独授权并在独立终端执行；供应商 token 上限只能事后检查，
外部答复的身份与使用量无法由本地程序认证。草案的出处校验不能替代数学含义审阅。
当前改动未提交、未推送，也没有启动正式研究或网站发布。

## 阶段 01 开发记录

- 工作区从空目录初始化为本地 Git 仓库。
- 工程入口为 `src/amre/cli.py`，环境探测和配置加载由独立模块负责。示例、夹具和自动化验收入口分别在 `examples/`、`tests/fixtures/` 和 `.github/workflows/ci.yml`。
- 研究类型及 12 个模块边界已声明。阶段 02 可从 `ResearchContractLoader.load(Path) -> ResearchProblem` 进入，复用 `ResearchProblem`、`ProblemId`、`Assumption` 和 `EvidenceLevel`。
- 恢复入口为 README 的本地安装和验收命令、总体架构及设计决策。阶段 02 等待授权。

### 2026-11-10 本地验收

环境为 Windows PowerShell、Python 3.13.5、Git 2.55.0.windows.5。使用项目内的
`.venv` 安装 `.[dev]`，安装版本为 `1.0.0a1`。

| 实际命令 | 结果 |
| --- | --- |
| `amre --help` | 退出码 0，显示三个子命令 |
| `amre version`、`python -m amre version` | 退出码 0，输出 `1.0.0a1` |
| `amre doctor` | 退出码 0，必需项均通过，SQLite 3.49.1 内存查询成功，并发现可选 Codex CLI |
| `amre status`、`amre --config examples/amre.yaml status` | 退出码 0，均识别工作区与 Git 工作树，后者显示样例配置来源 |
| `pytest` | 15 项通过 |
| `ruff check` | 全部通过 |
| `python -m pip check` | 安装依赖完整 |

GitHub Actions 已配置 Windows 和 Ubuntu 上的 Python 3.11、3.13 矩阵。此记录对应本地
Windows Python 3.13 验收，托管 CI 尚未运行。Git 仓库仍处于初始工作区状态。

## 阶段 02 开发记录

- `ResearchStore` 保存版本化 YAML 快照和独立 LaTeX，提供合同操作、图替换、证据登记和指定版本复核确认。
- `ResearchGraph` 提供上游、下游、候选路线、显式假设、障碍影响和当前证据查询。修改断言或假设后，
  受影响节点进入 `NEEDS_RECHECK`，旧证据保留历史版本。分支评估区分归约证据、前提和反驳。
- 恢复入口为 [双路线示例](../examples/and_or/README.md)及 [ADR-006 至 ADR-009](DECISIONS.md)。
  后续可使用 `ResearchStore.snapshot()`、`ResearchGraph.route_assessments()`、
  `evidence_for()` 和 `register_evidence()` 建立可重建索引和合同级验收。

### 2026-11-10 本地验收

环境为 Windows PowerShell、Python 3.13.5。通过 `pip install -e ".[dev]"` 安装 `1.0.0a2`。

| 实际命令 | 结果 |
| --- | --- |
| `amre --help`、`amre version`、`amre doctor`、`amre status` | 全部成功，原基础入口可用 |
| `amre problem create/list/show/validate/update` | 五个入口均通过实际可执行文件验证，临时工作区的更新重读成功 |
| `amre --config examples/and_or/amre.yaml problem validate P-T` | 1 个合同、12 个节点、18 条边通过校验 |
| `pytest` | 48 项通过，包含阶段 01 的 15 项回归测试 |
| `ruff check`、`python -m pip check` | 全部通过 |

GitHub Actions 已加入示例验证步骤，托管 CI 尚未运行。阶段 02 的证据检查基于登记记录和本地工件；
形式验证器调用、身份认证、Git 修订内容核对和合同级自动验收留待后续。阶段 03 保持 PLANNED。

## 阶段 03 开发记录

- `ResearchMemory.create_checkpoint()` 和 `restore()` 保存、校验和恢复一个问题的合同、相关图、
  假设、证据、工件、最近尝试、障碍、策略、预算和下一步。可用 `RestoredState.read_artifact()`
  读取快照中的原始工件。
- 清单提交事件 ID，按序重放不可变的事件文件。研究档案与检查点文件是可纳入 Git 的权威来源。
  `rebuild_index()` 校验当前研究文件、事件及归档后，重建本地 SQLite。
- `build_context()` 读取恢复结果，按 UTF-8 字节和完整段落装配当前任务。
  [双路线示例](../examples/and_or/README.md)与 [ADR-010 至 ADR-012](DECISIONS.md)是恢复入口。

### 2026-11-10 本地验收

Windows PowerShell、Python 3.13.5，以 `pip install -e ".[dev]"` 安装 `1.0.0a3`。

| 实际命令或场景 | 结果 |
| --- | --- |
| `amre --help`、`version`、`doctor`、`status`、示例 `problem validate P-T` | 全部成功 |
| `checkpoint create/show/restore`、`memory record/inspect/rebuild`、`context build` | 用已安装 CLI 在临时示例工作区实测成功 |
| Cold Restart Test | 不同进程保存和恢复。无 SQLite 索引时，命题、两个引理、障碍、证据、预算和下一步一致 |
| 崩溃、引用和版本 | 进程在发布清单前强制退出后仍恢复上一检查点；检测缺失文件和不一致版本；损坏的 SQLite 可重建 |
| `pytest` | 61 项通过，含阶段 01–02 的 48 项回归测试 |
| `ruff check`、`python -m pip check` | 全部通过 |

验收覆盖确定性文件恢复和上下文构建。真实模型上下文接续及指定 Git 修订的运行恢复留待后续。
阶段 04 保持 PLANNED。托管 CI 尚未运行。

## 阶段 04 开发记录

任务书使用桌面上的 `AMRE_04-10_一体化总Prompt.md` 合并版。仓库中没有
`codex-goals/` 分拆文件。阶段 01–03 的基础文档使用本仓库已有同名文件。

| 已有接口 | 阶段 04 接法 |
| --- | --- |
| `ResearchStore.snapshot()`、`ResearchContract`、`ResearchGraph` | 校验合同与假设，按版本和预期快照写入候选证据与障碍 |
| `ResearchMemory.restore()`、`build_context()` | 为 `ReasoningTask` 提供精确目标、当前路线和有界上下文 |
| `ResearchMemory.append_event()`、`create_checkpoint()` | 用原事件模型追踪开始、结束、候选、障碍和恢复点 |
| `ModelAdapter`、`MockModelAdapter`、`CodexCLIAdapter` | `src/amre/execution/adapters.py` 中的单模型接口 |
| `ReasoningTask`、`ReasoningResult`、`ExecutionState` | `src/amre/execution/models.py` 中的版本化数据 |
| `TaskQueue`、`BudgetManager`、`ResearchOrchestrator` | `queue.py`、`orchestrator.py` 中的预留、派发、归档和恢复 |

### 2026-11-10 本地验收

Windows PowerShell、Python 3.13.5、Codex CLI 0.167.0。所有研究执行测试使用 Mock、本地
子进程或替身传输。执行前重新运行阶段 01–03 的测试，61 项通过。

| 实际命令 | 退出码 | 结果 |
| --- | --- | --- |
| `codex --help`、`codex exec --help`、`codex features list` | 0 | 核对本机参数和特性 |
| `python -m pip install -e ".[dev]"` | 0 | 安装 `1.0.0a4` |
| `pytest` | 0 | 77 项通过，含 16 项执行测试和 61 项回归 |
| `ruff check`、执行文件 `ruff format --check`、`pip check` | 0 | 全部通过 |
| `amre --help`、`version`、`doctor`、`status` | 0 | 已安装 CLI 实测 |
| `run --program Algebra --dry-run` | 0 | 样例计划通过 |
| `run --problem P-T --adapter mock`、`checkpoint restore P-T`、`budget show` | 0 | 临时工作区核对回执、候选证据、恢复点及预留结算 |
| `pause`、`resume`、`stop` | 0 | 实测状态切换，新进程恢复及重复恢复由测试覆盖 |

核心门槛通过。真实 Codex 服务、认证、沙箱实际行为和远端终止仍为 UNVERIFIED。
工作区已有未跟踪的阶段 01–03 基线，保留当前变更，未建立阶段提交。托管 CI 尚未运行。

## 阶段 05 开发记录

- `execution/skills.py`、`skill_models.py` 为七种模式提供不同的输入、指令和输出契约。
  原执行器继续负责预算、派发和恢复，阶段 04 回执按可选字段兼容。
- `research/obstructions.py` 和现有图谱节点保存数学步骤、范围、尝试、解决证据及版本。
  `execution/failure_memory.py` 可查询历史失败、候选关联和推荐行动。
- `execution/skill_publication.py` 通过原合同事务发布候选引理、归约、障碍和证据。
  带源文件及证据 ID 的进展工件通过完成事件归档，供阶段 06 调度读取。

### 2026-11-10 本地验收

Windows PowerShell、Python 3.13.5。阶段 04 的 77 项测试在开发前重新通过。

| 实际命令或场景 | 退出码 | 结果 |
| --- | --- | --- |
| `python -m pip install -e ".[dev]"` | 0 | 安装 `1.0.0a5` |
| `pytest` | 0 | 88 项通过，含 11 项阶段 05 测试和 77 项旧回归 |
| `ruff check`、相关文件 `ruff format --check`、`pip check` | 0 | 全部通过 |
| `amre version`、`doctor`、`status`、`run --help` | 0 | 已安装 CLI 实测 |
| `run --problem P-T --mode ... --adapter mock` | 0 | 边界障碍→重载→方法切换→候选归约；自动测试覆盖全部七种模式 |
| `obstacle list/show/recommend/resolve`、`failure list`、`attempt list/show`、`checkpoint restore` | 0 | 实测查询、合成独立证据解除障碍及恢复 |

自动测试覆盖缺失工件、错误定位、未知证据、结果模式不匹配、精确重现、候选匹配、重复归约、
已有回执兼容，以及发布中断后的幂等恢复。独立证据的核查范围通过合成记录验收。
真实模型服务仍为 UNVERIFIED，托管 CI 尚未运行。保留未提交工作区。

## 阶段 04–06 深度会话与宏观调度验收

Windows PowerShell、Python 3.13.5。全量 `pytest --tb=short` 退出 0，104 项通过。
`ruff check`、相关文件格式检查、`pip check`、已安装 `amre --help` 和 `amre doctor` 退出 0。

- 默认深度会话在同次 Mock 派发中归档多个数学步骤和策略切换。测试覆盖运行中显式保存、
  用户暂停与恢复、停止、进程异常退出、原始推导恢复、硬超时及不重复结算。
- 本机 `codex exec resume --help` 确认按 ID 接续。替身传输覆盖相同 ID 接续、拒绝不同 ID
  与接续命令安全参数。未启动真实 Codex 模型或验证实际服务接续。
- 阶段 06 回归覆盖 A 的单个局部障碍、重复停滞、B 的已复核前置引理、C 的有界探索、
  共享依赖、下游激活、派生去重和两处崩溃注入。决策保存输入、种子、理由和对应尝试。

## 阶段 07 文献与选题验收

Windows PowerShell、Python 3.13.5。全量 `pytest --tb=short` 退出 0，111 项通过。
`ruff check` 退出 0。离线 CLI 和库测试覆盖合成来源导入、书目合并、定理定位与出处核对、
引用、来源变更失效、四类选题、父问题及边界障碍派生、会话内调用及可恢复回执。

三个公开元数据适配器通过替身传输测试，包含缓存、限流和服务拒绝场景。
尚未实测外部网络响应或真实 Codex 会话内工具传输。

## 阶段 08 GR 计算验收

Windows PowerShell、Python 3.13.5、SymPy 1.14.0、mpmath 1.3.0。全量 `pytest --tb=short`
退出 0，117 项通过。GR 专项 6 项通过。Ruff 检查、相关格式检查及 `amre gr --help`
退出 0。

基准覆盖 Minkowski 零曲率、单位二球面在指定曲率约定下的标量曲率 2、
反向曲率符号、平直标量波的局部能量恒等式、数值 ODE/积分/特征值、
非法表达式、奇异度规、边界缺失、范围受限的物理一致性和已保存工件重演。
实际深度 Mock 同会话调用后，Checkpoint 可恢复计算档案及 PARTIAL 计算证据。
外部高级后端和严格区间能力保留 UNAVAILABLE。Critical Collapse 模板没有计算成果声明。

## 阶段 09 Dashboard 验收

Windows PowerShell、Python 3.13.5、Node.js 24.18.0。全量 pytest 退出 0，121 项通过。
Ruff、TypeScript 检查、Node 单元测试 2 项和 Edge Playwright 测试 3 项均退出 0。
通过 amre dashboard build 生成静态站点，实际浏览器验证图谱、节点详情、缩放、
断裂链接、非法公式和不兼容版本。后端验证公开快照及构建目录均排除私有内容。

应用内浏览器预览连接超时。本地静态服务返回 HTTP 200，离线 Edge 自动化通过。
GitHub Actions 构建和独立 Pages 工作流已配置，未触发远程工作流或部署。

## 阶段 10 离线发行验收

- `benchmarks/`、`strategy/`、`reports.py` 和 `demo.py` 提供固定基准、版本化策略、
  Markdown/LaTeX 报告及可恢复的 FlatWave 闭环。基准的参考证明与执行输入分开保存。
- Codex 适配器的会话工具通过鉴权回环 MCP 接入同一模型派发。实测本机配置读取，
  替身测试通过鉴权、同请求去重、预算拦截与归档。真实服务端接续和工具协商仍为 UNVERIFIED。
- Windows PowerShell、Python 3.13.5。本地 `pytest --tb=short` 退出 0，126 项通过。
  `ruff check`、`ruff format --check src tests`、`pip check`、已安装 CLI 与 wheel 打包均退出 0。
- `amre demo`、`amre benchmark run` 及 `amre strategy recommend/evaluate/apply/rollback`
  均退出 0。Edge 验证实际演示快照中的会话、路线切换和计算证据。LaTeX 编译三页 PDF。

可重演命令、功能矩阵和可选集成边界见 [发行验收](RELEASE_READINESS.md)。
