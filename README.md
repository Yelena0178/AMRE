# AMRE

AMRE（Autonomous Mathematical Research Engine）面向数学、理论物理和广义相对论研究，
首个领域重点是引力临界坍缩（Critical Collapse）。研究以有明确命题、假设、依赖和验收条件的
Research Problem 为单位。

v1.0 提供研究合同、数学依赖图、持久研究记忆和带预算的连续深度会话。
默认使用离线 Mock。文献、GR 工具、只读 Dashboard 和可重演基准共用这些记录。

## Windows PowerShell 安装

需要 Python 3.11+ 和 Git。进入 AMRE 仓库根目录后执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\amre.exe --help
.\.venv\Scripts\amre.exe version
.\.venv\Scripts\amre.exe doctor
.\.venv\Scripts\amre.exe status
```

可以直接调用虚拟环境里的可执行文件，无需更改 PowerShell 执行策略。
也可使用 `.\.venv\Scripts\python.exe -m amre` 加相同的命令参数。
Codex CLI 是可选探测项。项目命令和 CI 均可在没有 Codex 账号或 API 密钥的环境中运行。

## 配置

`doctor` 和 `status` 默认读取当前目录中的 `amre.yaml`，文件可以省略。
用 `--config` 显式指定文件时，选项放在子命令前，且文件必须存在。
优先级由低到高为内置默认值、YAML、对应的环境变量。YAML 使用 UTF-8，支持 BOM。

| YAML 字段 | 环境变量 | 默认值 |
| --- | --- | --- |
| `workspace_root` | `AMRE_WORKSPACE_ROOT` | 配置文件所在目录，无配置文件时为当前目录 |
| `log_level` | `AMRE_LOG_LEVEL` | `INFO` |
| `codex_executable` | `AMRE_CODEX_EXECUTABLE` | `codex` |

相对的 `workspace_root` 以配置文件所在目录为基准，无文件时以当前目录为基准。
`log_level` 可用值为 `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。
`codex_executable` 可使用 PATH 上的命令名或绝对路径。

```powershell
$env:AMRE_LOG_LEVEL = 'DEBUG'
.\.venv\Scripts\amre.exe --config .\examples\amre.yaml status
Remove-Item Env:AMRE_LOG_LEVEL
```

`doctor` 检查 Python、SQLite 内存查询、工作区目录、Git 和 Git 工作树。
必需检查失败返回 1，配置或命令用法错误返回 2，已处理的运行中断返回 130。
`status` 只读展示配置和工作区，工作区不可用时仍会输出诊断信息。

## 研究合同

从仓库根目录运行以下命令。第一组读取双路线示例，第二组在当前工作区创建和修改合同。

```powershell
.\.venv\Scripts\amre.exe --config examples/and_or/amre.yaml problem validate P-T
.\.venv\Scripts\amre.exe --config examples/and_or/amre.yaml problem show P-T

.\.venv\Scripts\amre.exe problem create examples/and_or/new-problem.yaml --statement examples/and_or/research/statements/T.tex
.\.venv\Scripts\amre.exe problem list
.\.venv\Scripts\amre.exe problem show P-Quadratic
.\.venv\Scripts\amre.exe problem validate P-Quadratic
.\.venv\Scripts\amre.exe problem update P-Quadratic examples/and_or/status-patch.yaml
```

`update` 以顶层字段为单位合并补丁。传入的嵌套对象和列表整体替换，省略的字段保留。
可用 `--statement <file.tex>` 单独替换陈述。创建和更新都支持 `--graph <graph.yaml>`，
用于在同一事务中更新引用的图节点。该文件使用 `GraphDocument` 格式，提供完整图快照。
`validate` 检查整个工作区，附带问题编号时还检查该合同是否存在。

工作区的研究数据保存在 `research/research.yaml` 和其引用的 LaTeX、证据文件中。
通过 `ResearchStore` 修改已有数据会记录需要重新检查的版本。查询、修改和证据登记示例见
[双路线示例](examples/and_or/README.md)。

## 材料导入与任务书草案

`amre intake` 接收自然语言目标与文本、Markdown、LaTeX 材料，输出有出处、可修订的
任务书草案。`prepare` 不启动模型；可导入当前核心模型的答复，或显式选择
`generate --adapter codex --live`。草案不自动进入研究图，也不启动研究执行。
命令、预算、版本与恢复说明见 [Intake v1](docs/INTAKE.md)。

## 研究记忆

在有研究合同的工作区中，通过 YAML 写明下一步目标，随后保存、恢复并构建 Active Context。
以下命令使用问题 `P-T`，完整示例使用 [checkpoint-notes.yaml](examples/and_or/checkpoint-notes.yaml)。

```powershell
.\.venv\Scripts\amre.exe checkpoint create P-T path/to/checkpoint-notes.yaml
.\.venv\Scripts\amre.exe checkpoint show P-T
.\.venv\Scripts\amre.exe checkpoint restore P-T
.\.venv\Scripts\amre.exe context build P-T --max-bytes 16000
.\.venv\Scripts\amre.exe memory inspect
.\.venv\Scripts\amre.exe memory rebuild
```

显示、恢复和构建上下文默认读取最后发布的检查点，可用 `--checkpoint <id>` 指定历史版本。
`restore` 返回结构化快照和 `workspace_matches`，用于比较当前相关研究文件与历史快照。
上下文预算以 UTF-8 字节计数。必需段落放不下时报告所需预算，可选段落按优先级整块装入。

研究清单、事件、检查点和内容寻址工件位于 `research/`。将这些文件纳入 Git 可保存版本历史。
独立 Git 工作区需保留 `.gitattributes` 中对归档字节关闭换行转换的规则。
`.amre/research-index.sqlite3` 可从源文件重建。保存和恢复研究状态可独立于索引运行。

## 执行研究

在已有合同的可写工作区中执行以下命令。默认 `deep` 在一次连续派发中研究同一合同，
允许模型分解引理、调整方法并保存重要语义事件。可复制
[双路线示例](examples/and_or/README.md)到临时工作区试运行。

```powershell
.\.venv\Scripts\amre.exe run --problem P-T
.\.venv\Scripts\amre.exe run --problem P-T --workflow short
.\.venv\Scripts\amre.exe session inspect <SESSION_ID>
.\.venv\Scripts\amre.exe session checkpoint <SESSION_ID>
.\.venv\Scripts\amre.exe session pause <SESSION_ID>
.\.venv\Scripts\amre.exe session resume <SESSION_ID>
.\.venv\Scripts\amre.exe session stop <SESSION_ID>
.\.venv\Scripts\amre.exe checkpoint restore P-T
.\.venv\Scripts\amre.exe budget show
.\.venv\Scripts\amre.exe run --program Algebra --dry-run
.\.venv\Scripts\amre.exe pause --problem P-T
.\.venv\Scripts\amre.exe resume --problem P-T
.\.venv\Scripts\amre.exe stop --problem P-T
.\.venv\Scripts\amre.exe status
```

Program 由 `research/programs/<NAME>.yaml` 定义。可在 `research/execution.yaml`
覆盖 `workspace`、`program`、`problem`、`attempt` 的 `attempts`、`seconds` 和
`tool_calls` 上限，以及 `max_context_bytes` 和 `max_output_bytes`。单工作区并发上限为一。
`deep.session_seconds` 是同一深度会话所有派发的累计上限，`deep.max_semantic_events`
限制每次派发的模型语义事件。默认 `deep.budget_target_percent: 85` 用于比较深度执行时间占比，
可调整。`budget show` 同时报告目标和实际占比。

运行账本位于 `.amre/execution.yaml`，包含已预留及已结算的工作区预算。
研究尝试回执和候选工件位于 `research/attempts/`，深度会话与原始语义工件位于 `research/deep/`。
恢复可显示 `CHECKPOINT_RECONSTRUCTION` 和上下文预算省略的完整区块。
退出后可运行 `resume` 结算原尝试，
完成的尝试重复恢复会返回既有状态。中断且缺少回执的尝试按预留时长保守结算。
研究合同变动后应先保存新的 Checkpoint。执行状态 `COMPLETED` 表示单次工作已记账。
数学结论继续由图谱中的候选状态及独立证据描述。

在独立的 PowerShell 会话中，`run --problem P-T --adapter codex --live` 才会请求
实际 Codex 派发。该路径会探测本机能力并拒绝嵌套执行。默认使用 `--ephemeral`。
显式设置 `deep.native_codex_sessions: true` 允许 Codex 在自己的会话目录持久化历史，
在运行时探测到匹配的 CLI 接口且保存有会话 ID 时优先原生接续。
本地验收使用 Mock 和替身传输，
真实服务、认证、Windows 沙箱组合与远端执行的终止行为仍待验证。
CLI 明确返回的 token 计数用于记账和结果检查，费用或缺失用量显示为 `UNKNOWN`。
真实会话恢复需再次显式选择 `--adapter codex --live`。

## 数学推理 Skill 和障碍记忆

在 [双路线示例](examples/and_or/README.md) 的可写副本中加载 `P-T` 的 Checkpoint，
然后运行以下离线序列。将查询得到的编号填入 `<ID>`。

```powershell
.\.venv\Scripts\amre.exe run --problem P-T --mode PROOF_CONSTRUCTION --adapter mock
.\.venv\Scripts\amre.exe obstacle list P-T
.\.venv\Scripts\amre.exe obstacle recommend <ID>
.\.venv\Scripts\amre.exe run --problem P-T --mode METHOD_INNOVATION --obstacle <ID> --adapter mock
.\.venv\Scripts\amre.exe run --problem P-T --mode PROOF_ROUTE_EXPLORATION --adapter mock
.\.venv\Scripts\amre.exe attempt list P-T
.\.venv\Scripts\amre.exe attempt show P-T <ATTEMPT_ID>
.\.venv\Scripts\amre.exe failure list P-T --obstacle <ID>
```

其余模式为 `LEMMA_DECOMPOSITION`、`COUNTEREXAMPLE_SEARCH`、`OBSTACLE_DIAGNOSIS` 和
`MATHEMATICAL_SYNTHESIS`。障碍诊断和方法创新需要可用的结构化障碍，综合需要已登记的证据，
路线探索需要已有的前置断言。每种模式有独立的输入选择、结果结构和自检字段。

障碍保存在现有图谱节点中，包含数学步骤、范围、尝试、证据和版本。
`obstacle list P-T --query trace` 返回文本相关的候选关联。
使用 `obstacle resolve <ID> --evidence <EVIDENCE_ID>` 登记独立核查的解除证据，
其范围需精确标为 `obstruction:<ID>`。图谱会重新评估受影响路线的阻碍。
确认新图谱后可保存新的 Checkpoint。旧回执和失败工件继续可查询。

## 研究路线调度

```powershell
.\.venv\Scripts\amre.exe routes list P-T
.\.venv\Scripts\amre.exe scheduler explain --problem P-T --seed 19
.\.venv\Scripts\amre.exe scheduler simulate --problem P-T --steps 3 --seed 19
.\.venv\Scripts\amre.exe scheduler run --problem P-T --steps 3 --seed 19
.\.venv\Scripts\amre.exe portfolio status --problem P-T
```

调度器在会话之间决策，保留当前路线的有源进展，也给刚遇到一个局部障碍的路线继续研究的机会。
配置文件 `research/portfolio.yaml` 可设置分配比例、探索限额、停滞窗口及冷却窗口。
决策和结果写入 `research/scheduler/`，可以按决策 ID 重放。派生新合同使用
`portfolio derive --help` 查看父问题、障碍、路线和精确陈述的参数。

## 文献与候选选题

在示例研究工作区中导入明确标为合成数据的元数据文件。后续编号可从命令输出取得。

```powershell
.\.venv\Scripts\amre.exe literature import examples/literature/synthetic-papers.yaml
.\.venv\Scripts\amre.exe literature search --query boundary
.\.venv\Scripts\amre.exe literature show <PAPER_ID>
.\.venv\Scripts\amre.exe literature link P-T <PAPER_ID> --scope "Boundary background"
.\.venv\Scripts\amre.exe discover --metadata examples/literature/model-extension.yaml --statement examples/literature/model-extension.tex
.\.venv\Scripts\amre.exe candidates list --parent P-T
.\.venv\Scripts\amre.exe candidates promote <CANDIDATE_ID> --author "Researcher"
```

`literature import` 的 `--statement` 接受包含来源指纹和定位的定理元数据与 LaTeX。
使用 `--source-excerpt` 与 `--checked-by` 可记录对授权摘录的核对。
`SOURCE_CONFIRMED` 只确认陈述出处。相关来源有新版本时，图谱将引用标记为待复核。
网络查询需在 `research/literature.yaml` 配置 `enabled_providers`，并明确指定
`literature search --provider arxiv --online --query <QUERY>`。搜索只获取元数据。
会话内的 `literature.search` 通过宿主工具回调读取离线目录。
启用需在 `deep.tools` 列出该工具并配置 `deep.session_tool_calls`、工作区及问题的
`tool_calls` 限额。Codex 工具模式使用本地鉴权 MCP 入口。替身验收覆盖实际回环传输，
真实 Codex 服务端工具协商仍待验证。

## 广义相对论计算

安装可选 GR 依赖后，在可写工作区执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gr]"
.\.venv\Scripts\amre.exe gr info
.\.venv\Scripts\amre.exe gr run gr.curvature examples/gr/minkowski.yaml
.\.venv\Scripts\amre.exe gr run gr.curvature examples/gr/unit-sphere.yaml
.\.venv\Scripts\amre.exe gr run gr.wave_energy examples/gr/flat-wave-energy.yaml
.\.venv\Scripts\amre.exe gr replay <COMPUTATION_ID>
```

`gr run --problem <ID> --assertion <LEMMA_ID>` 将计算档案关联到合同范围内的断言。
可重演档案记录符号表达式、数值输入、工具版本、约定和检查范围。
符号结果是 `COMPUTATION_TESTED` 范围内的证据。局部波动能量恒等式的符号核验与
[带边界条件的积分论证](examples/gr/flat-wave-argument.tex)分别保存。
有限数值工具报告步长或精度及残差，严格区间计算、Lean/Mathlib 和 xAct/Wolfram 的
未配置能力返回 `UNAVAILABLE`。Critical Collapse 的
[计划模板](examples/critical_collapse/program-template.yaml)列出了需明确的模型输入和研究依赖。

## 只读 Dashboard

使用 Node.js 24 和仓库的前端锁文件。以下命令构建合成研究示例。

```powershell
npm.cmd --prefix dashboard ci
.\.venv\Scripts\amre.exe --config examples/and_or/amre.yaml dashboard export --policy examples/and_or/dashboard-public.yaml
.\.venv\Scripts\amre.exe --config examples/and_or/amre.yaml dashboard build --policy examples/and_or/dashboard-public.yaml --output .amre/dashboard/site
.\.venv\Scripts\python.exe -m http.server 4178 --bind 127.0.0.1 --directory .amre/dashboard/site
```

在浏览器中打开 `http://127.0.0.1:4178/`。真实工作区在
`research/dashboard-public.yaml` 列出允许发布的问题、可显示陈述及详情的节点，
并用 `private_artifacts` 屏蔽指定工件。没有公开选择时导出空快照。
构建输出使用新目录，便于核对将要发布的文件。Pages 工作流默认只构建示例产物，
需要从 GitHub 显式启动并选择部署才会发布。

## 离线闭环、研究报告和基准

```powershell
.\.venv\Scripts\amre.exe demo --output .amre/demo
.\.venv\Scripts\amre.exe report generate --problem <ID> --output .amre/reports
.\.venv\Scripts\amre.exe benchmark run --suite examples/benchmarks.yaml --output .amre/benchmark
.\.venv\Scripts\amre.exe strategy recommend .amre/benchmark/benchmark.json
.\.venv\Scripts\amre.exe strategy propose examples/strategy/profile.yaml
.\.venv\Scripts\amre.exe strategy evaluate <VERSION_ID> --suite examples/benchmarks.yaml --output .amre/evaluation
.\.venv\Scripts\amre.exe strategy apply <VERSION_ID> --evaluation <EVALUATION_ID>
.\.venv\Scripts\amre.exe strategy rollback
```

策略命令作用于所选工作区。评估使用隔离实验目录，记录固定种子、输入指纹和通过的门槛。
显式应用后，策略覆盖上下文预算、Prompt 版本、深度预算目标和调度参数。
回退恢复上一版本。基准中 Deep 与 Short 使用同一 Mock 和相同硬预算，
共享独立计算结果，并对照历史检索、障碍驱动和分支调度。真实模型研究效果仍待验证。
演示在新目录建立可恢复的合成研究状态，生成 Markdown、LaTeX 和 Dashboard JSON。

## 本地验证

```powershell
.\.venv\Scripts\pytest.exe
.\.venv\Scripts\ruff.exe check
```

开发入口见 [AGENTS.md](AGENTS.md)，后续模块、阶段验收和设计记录见
[总体架构](docs/ARCHITECTURE.md)、[实施状态](docs/IMPLEMENTATION_STATUS.md)和
[设计决策](docs/DECISIONS.md)。完整离线发行证据及未验证范围见
[发行验收](docs/RELEASE_READINESS.md)。
