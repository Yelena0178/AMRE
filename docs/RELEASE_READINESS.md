# AMRE v1.0 发行验收

本文件记录离线发行范围。外部集成的状态分别列出。所有数学候选保留断言版本、证据来源和
检查范围。真实研究效果需要独立试验。

## 交付矩阵

| 阶段 | 本地功能 | 可重演证据 |
| --- | --- | --- |
| 01 | 工程、配置、日志、CLI、CI | `amre --help`、`amre version`、`amre doctor`、`amre status` |
| 02 | 合同、AND/OR、证据、假设和下游失效 | `tests/test_research_graph.py`、`tests/test_research_store.py` |
| 03 | Checkpoint、事件重放、有限上下文和重建索引 | `tests/test_research_memory.py` |
| 04 | 默认 Deep、显式 Short、预算、暂停、终止和恢复 | `tests/test_deep_research.py`、`tests/test_execution.py` |
| 05 | 七种数学策略、语义事件和障碍记忆 | `tests/test_reasoning_skills.py` |
| 06 | 宏观调度、停滞、派生、预算与重放 | `tests/test_scheduler.py`、FlatWave 分支基准 |
| 07 | 书目版本、定理出处、四类候选、会话内查询 | `tests/test_literature.py`、`tests/test_session_transport.py` |
| 08 | GR 计算、物理一致性、计算证据、临界坍缩模板 | `tests/test_gr.py`、`examples/gr/` |
| 09 | 安全公开快照、互动图谱、静态构建 | `tests/test_dashboard.py`、`dashboard/tests/` |
| 10 | 离线闭环、四组对照、策略评估和回退、研究报告 | `tests/test_release.py`、`amre demo`、`amre benchmark run` |

## 离线复现

在 PowerShell 中，从仓库根目录执行。输出目录需尚未存在。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gr]"
.\.venv\Scripts\amre.exe doctor
.\.venv\Scripts\amre.exe demo --output .amre/release-demo
.\.venv\Scripts\amre.exe benchmark run --suite examples/benchmarks.yaml --output .amre/release-benchmark --seed 19
.\.venv\Scripts\pytest.exe --tb=short
.\.venv\Scripts\ruff.exe check
```

演示生成 FlatWave 的研究工作区。文献为合成来源，Mock 在同一派发中执行离线文献查询和
实际波动能量计算。图谱保留边界障碍和候选证据。派生合同后，调度器保存切换前后的
Checkpoint。最终生成 Markdown、LaTeX 和公开 Dashboard JSON。

深度和短任务基准使用同一 Mock 实现、独立工具检验和相同的时间与次数上限。可重复的
轨迹比较候选数学步骤、原始论证区块缺失、重复障碍、分支切换、实际结算和完整恢复。
测量的本地时长随机器负载变化。固定 Mock 结果不能推断真实模型效率。

## Windows 发行检查

运行环境为 Windows PowerShell、Python 3.13.5、Node.js 24.18.0、SymPy 1.14.0 和
mpmath 1.3.0。

| 实际命令或场景 | 退出码 | 结果 |
| --- | --- | --- |
| `python -m pip install -e ".[dev,gr]"`、`pip check` | 0 | 安装 AMRE 1.0.0，依赖一致 |
| `python -m pip wheel --no-deps -w .amre/release-wheel .` | 0 | 本地生成 `amre-1.0.0-py3-none-any.whl` |
| `amre --help`、`amre version`、`amre doctor`、`amre status` | 0 | 已安装 CLI 可用 |
| `ruff check`、`ruff format --check src tests` | 0 | Python 静态及格式检查通过 |
| `pytest --tb=short` | 0 | 126 项通过，耗时 333.38 秒 |
| `npm run typecheck`、`npm test` | 0 | TypeScript 检查及 2 项前端单元测试通过 |
| `amre dashboard build`、`npm run test:browser` | 0 | Vite 生产构建和 3 项本地 Edge 测试通过 |
| 已构建的实际 FlatWave 演示 Dashboard | 0 | Edge 显示 2 次深度会话、A → B 路线事件及局部计算证据 |
| `amre demo --output .amre/v1-demo` | 0 | 派生合同、调度路线 B、可恢复 Checkpoint、报告与 JSON |
| `amre benchmark run --suite examples/benchmarks.yaml --output .amre/v1-benchmark --seed 19` | 0 | 3 题共 7 项预设工具与范围条件通过，4 组工作流对照可查 |
| `amre strategy recommend`、`evaluate`、`apply`、`rollback` | 0 | 基于完整基准轨迹提出策略，隔离评估通过，显式激活后回退基础策略 |
| `pdflatex -no-shell-escape -halt-on-error -interaction=nonstopmode` | 0 | FlatWave 报告生成三页 PDF |

LaTeX 技能脚本读取子进程输出时遇到 Windows GBK 解码错误。用 TeX Live
`pdflatex` 和正斜杠路径直接运行后完成 PDF 验证。

## 外部集成及已知限制

| 能力 | 状态 | 边界 |
| --- | --- | --- |
| Codex CLI 0.167.0 参数及 MCP 配置 | 已检查 | 核对 `exec`、`exec resume` 帮助及本机 MCP 配置读取。替身测试覆盖会话 ID 核对和鉴权回环传输 |
| 真实 Codex 认证、Windows 沙箱、原生接续与工具协商 | UNVERIFIED | 真实派发和接续均需显式 `--adapter codex --live`。原生历史还需设置 `deep.native_codex_sessions: true` |
| 提供方计量 | 部分可用 | 本地时长、次数、工具和并发有硬限额。Token 在 CLI 报告后计量，未知 token 或费用显示 `UNKNOWN` |
| 文献远端接口 | UNVERIFIED | 已执行 arXiv、Crossref 与 Semantic Scholar 的替身测试。真实请求需用户开启配置，获取范围为元数据 |
| 严格区间数值、Lean/Mathlib、xAct/Wolfram | UNAVAILABLE | 当前工作区未配置相应后端。SymPy 与 mpmath 结果保留范围和未证明义务 |
| GitHub Actions 和 Pages | UNVERIFIED | 工作流已配置。本地静态生产构建和 Edge 浏览器测试通过。远程构建与发布需独立执行 |
| 公开导出 | 需明确选择 | 凭据模式与私有路径过滤支持显式授权字段。发布前核对实际输出及所选研究内容 |

应用内预览曾超时。HTTP 服务及独立的 Edge 自动化通过。报告生成支持受限的公式语法，
超出该范围的原始 LaTeX 按文本保存。用户自定义 Unicode 内容的编译依赖系统字体与引擎。

## 下一步数学研究

从 [Critical Collapse 模板](../examples/critical_collapse/program-template.yaml)选择具体
Einstein–matter 模型，写清规范、函数空间、边界条件和尺度。先用已知背景核对曲率符号、
约束与能量恒等式，再把相似视界的正则性或谱定义拆成可独立验收的 Research Contract。
只有获得范围明确的独立证据后才更新相应断言的证据属性。
