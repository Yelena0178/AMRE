# AMRE v1.0 总体架构

## 研究单元和控制流

Research Problem 包含精确命题、显式假设、依赖和可执行的验收条件。
Research Contract 持久记录这些字段，以及后续验收需要的证据约束。默认执行工作流为
Deep Research。单个深度会话围绕同一合同持续推理，模型可在会话内分解引理、尝试证明、
寻找反例和切换局部方法。原有单次尝试作为显式短任务工作流保留。

入口读取并验证合同。知识与证据图维护问题之间的依赖和各项证据的出处。
AND/OR 依赖采用分组表达。组内全部成员须满足，组间只需满足一个候选组合。
假设格记录假设集之间有依据的蕴涵关系，并保留未决关系。调度器从依赖和假设条件
已满足的候选分支中选择下一步，结合阻碍、检查点和剩余预算调整选择。

调度器将选中的问题交给单一核心推理模型。确定性代码负责合同和依赖检查、工件读写、
调度、预算账本及证据出处核对。Codex Runner 返回候选工件，由证据入口按已取得的
检验或确认记录分类。报告从研究图读取证据和未决条件，Dashboard 消费经过授权的发布工件。

## 研究前的材料与合同草拟

`intake/` 为独立的研究准备工作流：保存原始文本材料，使用同一 ModelAdapter 生成
带出处的草案，并按不可变版本导出 `draft.yaml`、`statement.tex`、`review.md`。
草案允许未决问题；来源核对与数学验证分开。该流程不创建正式合同、研究图或证据，
也不调用研究调度器。详见 [Intake v1](INTAKE.md) 和 ADR-002、ADR-024。

## 模块及依赖

下表的依赖表示数据来源或调用方向。调度器负责协调求解过程，Runner、图谱和领域工具
可独立测试。阶段 01 的基础设施与各研究接口分离。

| 模块 | 职责 | 主要依赖 | 计划阶段 |
| --- | --- | --- | --- |
| Foundation | CLI、配置、日志、异常、环境诊断、共享类型和接口 | Python、Pydantic、PyYAML、本地 Git、SQLite 标准库 | 01 |
| Research Contract | 加载、版本化、验证问题定义与验收条件 | Foundation、Git 中的合同 | 02 |
| Knowledge and Evidence Graph | AND/OR 依赖、来源查询、证据登记、版本传播和研究索引 | Research Contract、假设格、Git 工件、NetworkX、SQLite | 02–03 |
| Assumption Lattice | 组织假设集、记录显式蕴涵与不兼容、传播变更影响 | Research Contract、显式声明及依据 | 02 |
| Checkpoint and Memory | 保存研究快照、追加事件、归档工件并恢复数学状态 | Research Contract、图谱快照、Git、SQLite | 03 |
| Context Builder | 将精确陈述、任务、依赖与选定档案组装成有预算的输入 | Checkpoint and Memory、研究图 | 03 |
| Deep Research Runner | 保持单模型会话、增量保存语义工件、限制资源并恢复执行 | Research Contract、Checkpoint、ModelAdapter、执行账本 | 04 |
| Mathematical Reasoning Skills | 七种会话内策略、同模型自检及不同输出契约 | Context Builder、DeepResearchRunner、ModelAdapter | 05 |
| Obstruction Intelligence | 记录失败步骤和排除范围，查询既有失败并提出方法切换 | 图谱障碍节点、尝试回执、证据、事件归档 | 05 |
| Adaptive Branch Scheduler | 会话边界上的宏观路线选择，优先维持实质进展 | 图谱、假设格、Checkpoint、Runner、Obstruction Intelligence | 06 |
| Literature Intelligence | 检索文献并记录引用、定位和出处 | Research Contract、获准的文献服务 | 07 |
| GR Domain Pack | 广义相对论计算接口和可复现检查，首批覆盖临界坍缩示例 | Research Contract、候选工件、获准的数学工具 | 08 |
| Research Reports | 生成可追溯的结果、证据、失败和未决条件报告 | Research Contract、图谱、Checkpoint、阻碍、领域和文献工件 | 10 |
| Research Dashboard | 展示研究进度、深度会话与证据，支持经授权发布到 GitHub Pages | Research Memory、执行账本、决策归档、公开快照配置 | 09 |
| Strategy Benchmark | 以固定问题集和预算比较调度策略并报告成本与产出 | Research Contract、Scheduler、Checkpoint、Research Reports | 10 |

阶段 02 将 Research Contract、AND/OR 图、证据记录和假设格接入 Foundation。
[`ResearchStore`](../src/amre/research/store.py) 负责验证并发布工作区快照，
[`ResearchGraph`](../src/amre/research/graph.py) 负责图查询与路线评估。
后续模块边界仍在 [`interfaces.py`](../src/amre/research/interfaces.py)。

## 图关系

图的边从使用者指向其输入。合同的假设、依赖、路线、障碍和证据引用投影为图边。
定理和引理也可作为独立节点登记。

| 关系 | 语义 |
| --- | --- |
| `DEPENDS_ON` | 断言或归约所需的共同前提 |
| `REQUIRES_ALL` | 一条归约路线所需的全部断言 |
| `ALTERNATIVE_ROUTE` | 断言的一个候选归约路线 |
| `USES_ASSUMPTION` | 断言或归约显式使用的假设 |
| `SUPPORTED_BY` | 关联到该断言或归约的版本化证据 |
| `BLOCKED_BY` | 当前阻碍断言或归约的障碍 |
| `DERIVED_FROM` | 归约或断言的推导来源，同样参与前提检查 |
| `CITES` | 文献来源，供溯源查询 |

归约节点须有唯一所属断言、至少一个必要前提、自己的 LaTeX 陈述和证据记录。
路线就绪要求共同前提及分支前提全部有当前适用的支持、假设可由显式声明推出、无冲突或阻碍，
并且归约本身有完整、通过、当前版本的形式化或人工确认记录。
就绪结果供后续验收使用，保存的断言状态独立维护。

## 证据、持久化和恢复

| 证据类型 | 所需记录 |
| --- | --- |
| 候选证明 | 推理工件、问题与假设、生成来源 |
| 计算检验 | 可复现的程序、版本、输入、参数和结果 |
| 形式化证明 | 命题与形式化陈述的对应、证明工件和可信验证器记录 |
| 人工确认 | 明确的确认记录及其核对范围 |

证据记录保存来源、工件、Git 版本、工具与运行信息、支持或反驳的方向、适用范围、结果和断言版本。
`COMPUTATION_TESTED`、`FORMALLY_VERIFIED`、`HUMAN_CONFIRMED` 作为证据属性，
与候选证明、候选反驳等断言状态分开。阶段 02 进行记录级别的检查，合同验收引擎留待后续。

阶段 03 使用三级记忆：

- **Active Context** 按当前路线选择材料，优先放入完整命题、假设、定义、目标及依赖关系，
  其后加入关键节点、障碍、失败尝试、推导和文献。预算单位为 UTF-8 字节。
- **Research Checkpoint** 保存单个问题的可恢复状态，包括有关子图和版本、当前路线、最近尝试、
  结论与候选、障碍、未决义务、下一步、策略和预算快照。
- **Knowledge Archive** 保存已发布的事件、版本化检查点和以 SHA-256 标识的原始工件。
  Git 中的研究文件构成长期记录。SQLite 为可重建的查询索引。

清单的事件 ID 序列决定哪些事件和检查点已经发布。过程先写入不可变文件，再原子替换
`research/research.yaml`。复核、快照、引用与上下文装配细节见 [设计决策](DECISIONS.md)。

## 长程执行边界

阶段 04 通过 `ResearchOrchestrator` 读取已验证合同和 Checkpoint，使用原有 Context Builder
生成单次任务。执行队列预留工作区、Program、Problem 和 Attempt 的次数与时长，持久化
尝试 ID 后再派发。Mock 和 Codex CLI 实现同一 `ModelAdapter` 协议，模型输出只能登记为候选。
持久化的回执、图谱和事件允许中断后继续发布并结算原尝试。真实模型服务集成尚待验证。

阶段 05 的 `ResearchSkill` 从当前 Checkpoint 的图谱范围选择输入。模型回执记录模式、
版本、自检和结构化结果，派发、预算、恢复仍沿用阶段 04。障碍和路线进入同一个研究图。
数学进展记录包含源文件和证据指针，可供阶段 06 查询历史；相似文字只生成候选关联。

### Deep Research 会话

`DeepResearchSession` 记录问题、目标、路线、会话状态、工作上下文、已用和剩余资源、
工件、语义事件及最近 Checkpoint。`DeepResearchRunner` 在一次有界模型派发内连续接收
步骤与策略变更。模型继续推理时，语义事件可以触发异步文件保存。重要证明进展、关键障碍、
重要假设变化、新分支、预算边界、用户暂停或中断均是保存点。

任务合同、连续会话与 Program 分别设硬上限，工作区账本统一预留和结算。同一个会话恢复后
沿用其累计耗时和用量。深度研究预算占比 85% 是可调规划目标。未知 token 和费用明确标记
`UNKNOWN`。实际适配器必须满足沙箱、工具授权和中断约束。

调度器在会话之外处理重大数学事件、结构化停滞、预算边界、用户指令、会话结束或不可恢复错误。
单个引理、局部计算和一次技术障碍不构成强制切换理由。真实适配器会话 ID 与 AMRE 文件
Checkpoint 分别记录。真实会话恢复需经过适配器能力和安全检查。重建会话使用精确合同、
假设、原始论证、未决义务、阻碍、排除范围和下一步，并标记可能的上下文损失。

阶段 07–10 的会话内工具、Dashboard 和对照基准验收见 [阶段计划](PHASE_PLAN.md)。

### 会话内文献和计算工具

`ToolRegistry` 提供允许列表、请求和响应模式以及可重放的宿主回执。
工具在模型所在的会话中执行，遵守工作区、研究问题、Program、路线和会话的调用上限。
每次有效调用只结算一次，输入或输出过大、未授权工具和重复请求 ID 都有明确边界。
离线文献查询和 GR 计算由相同机制派发。GR 专用数学代码在独立 worker 内运行，
以有界表达式语法、墙钟时间和输出字节限制约束计算。

原始 GR 结果和约定保存在 `research/computations/`。会话只保存指针和可用范围，
在结果结算时向研究图注册局部计算证据或精确定位的障碍。符号校验和有限数值实验
保留验证范围，图谱仍用独立证据判断完整断言是否成立。

### 报告、基准与版本化策略

Research Reports 从当前合同、图谱、证据、失败工件与 Checkpoint 生成本地 Markdown
和 LaTeX。独立支持、模型候选、有限计算证据和待复核版本分别输出。

基准将已知数学题与参考论证分开，使用确定性 Mock 调用真实执行路径，并运行独立的
GR 工具作为固定验收条件。策略注册表从基准结果提议有限变更，隔离运行候选与基准配置。
策略需通过实验门槛并经显式激活，运行中的工作区不能切换配置。
