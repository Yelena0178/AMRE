# 研究任务书草案 v2

DRAFT_UNVERIFIED：仅为待审阅草案。来源引用通过程序核对，不代表数学含义或证明已验证。确认整理准确性不构成数学证据升级。

生成方式：external_response；模型标识：codex-current-session。

## 研究目标与数学陈述

### Goal [user]

在四维球对称 Einstein–无质量标量场系统中，由半径方程与两条零方向约束推导 \(m_u,m_v\) 的精确表达式，并逐步展示抵消项。

出处：S-71f3e610ecd18445667d38f4，行 1–5

> 在四维球对称 Einstein–无质量标量场系统中，设 \\(r>0\\)、\\(\Omega>0\\)，定义 Hawking 质量\
> \\[\
> m=\frac r2\left(1+4\Omega^{-2}r_u r_v\right).\
> \\]\
> 从半径方程和两条零方向约束推导 \\(m_u\\) 与 \\(m_v\\) 的精确表达式，逐步展示抵消项。

## 假设

### PositiveRadiusLapse [user]

\(r>0,\ \Omega>0\)。

出处：S-71f3e610ecd18445667d38f4，行 1–1

> 在四维球对称 Einstein–无质量标量场系统中，设 \\(r>0\\)、\\(\Omega>0\\)，定义 Hawking 质量\

### ClassicalDifferentiation [model_suggestion]

按经典解在局部进行所需微分；不假定 r_u 或 r_v 有固定符号，也不假定它们非零。

说明：说明本次形式微分计算的适用范围，不增加单调性或非退化条件。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 8–8

> 本次按经典解作局部微分计算；不添加 r_u、r_v 的符号或非零假设。

## 定义

### HawkingMass [user]

\(m=\frac r2(1+4\Omega^{-2}r_u r_v)\)，其中下标表示相应坐标偏导。

说明：仅统一显示转义，不改变原质量定义。

出处：S-71f3e610ecd18445667d38f4，行 3–3

> m=\frac r2\left(1+4\Omega^{-2}r_u r_v\right).\

### MetricConvention [model_suggestion]

采用双零度规 \(g=-\Omega^2du\,dv+r^2d\omega_{\mathbb S^2}^2\)，即 \(g_{uv}=g_{vu}=-\Omega^2/2\)。

说明：用户授权自行选择后确定的本次约定，不是原题已经指定的内容。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 3–3

> 标量场记为 phi，采用 g=-Omega^2 du dv+r^2 d omega_{S^2}^2，因此 g_uv=g_vu=-Omega^2/2。

### ScalarNormalization [model_suggestion]

标量场记为 \(\phi\)，采用 \(\operatorname{Ric}_{ab}=2\partial_a\phi\partial_b\phi\) 的归一化，无电磁场和宇宙学常数项。

说明：本次选定的方程约定；不能与含其他耦合系数的标量场记号混用。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 4–4

> 标量场归一化取 Ric_ab=2 (partial_a phi)(partial_b phi)，不含电磁场或宇宙学常数项。

## 已有依赖

### RadiusEquation [model_suggestion]

\(r_{uv}=-\frac{\Omega^2}{4r}-\frac{r_u r_v}{r}\)。

说明：以参考文献的零电荷特例固定半径方程；作为待用输入，不是本次已完成的推导结果。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 5–5

> 半径方程：r_uv=-Omega^2/(4r)-r_u r_v/r。

### UConstraint [model_suggestion]

\(\partial_u(\Omega^{-2}r_u)=-r\Omega^{-2}(\phi_u)^2\)。

说明：与选定标量场归一化一致的 u 方向约束。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 6–6

> u 方向约束：partial_u(Omega^{-2} r_u)=-r Omega^{-2}(phi_u)^2。

### VConstraint [model_suggestion]

\(\partial_v(\Omega^{-2}r_v)=-r\Omega^{-2}(\phi_v)^2\)。

说明：与选定标量场归一化一致的 v 方向约束。

出处：S-fe97531a6db089a02aba279b，行 1–1

> 你自己定

出处：S-e262ac7a0d009fc269d693ae，行 7–7

> v 方向约束：partial_v(Omega^{-2} r_v)=-r Omega^{-2}(phi_v)^2。

## 旧材料中的结果（未由 Intake 验证）

未提供。

## 未完成步骤与缺口

### DerivationPending [user]

分别展开 m_u、m_v，代入对应零方向约束及半径方程，逐项展示 Ω 导数项、常数几何项和 r_u r_v 的乘积项如何抵消。

说明：正式推导尚未执行；本次 Intake 只准备有出处的任务书。

出处：S-71f3e610ecd18445667d38f4，行 5–5

> 从半径方程和两条零方向约束推导 \\(m_u\\) 与 \\(m_v\\) 的精确表达式，逐步展示抵消项。

## 拟议验收条件

### ShowCancellations [user]

分别完整展开 m 的 u、v 偏导，标明各次代入，逐项展示几何项与 Ω 导数项的抵消，保留最终标量场通量项。

出处：S-71f3e610ecd18445667d38f4，行 5–5

> 从半径方程和两条零方向约束推导 \\(m_u\\) 与 \\(m_v\\) 的精确表达式，逐步展示抵消项。

## 待确认问题

未提供。

## 本次变化（由程序比较版本生成）

### 新增 ClassicalDifferentiation

之后 [assumptions; model_suggestion]：按经典解在局部进行所需微分；不假定 r_u 或 r_v 有固定符号，也不假定它们非零。

说明：说明本次形式微分计算的适用范围，不增加单调性或非退化条件。

### 删除 Conventions

之前 [questions; unresolved]：采用哪一种双零度规及标量场归一化？半径方程和两条约束的具体形式是什么？

说明：原题没有指定，最终系数依赖这些约定。

### 新增 DerivationPending

之后 [gaps; user]：分别展开 m_u、m_v，代入对应零方向约束及半径方程，逐项展示 Ω 导数项、常数几何项和 r_u r_v 的乘积项如何抵消。

说明：正式推导尚未执行；本次 Intake 只准备有出处的任务书。

### 新增 MetricConvention

之后 [definitions; model_suggestion]：采用双零度规 \(g=-\Omega^2du\,dv+r^2d\omega_{\mathbb S^2}^2\)，即 \(g_{uv}=g_{vu}=-\Omega^2/2\)。

说明：用户授权自行选择后确定的本次约定，不是原题已经指定的内容。

### 删除 MissingEquations

之前 [gaps; unresolved]：原题没有写出半径方程及两条零方向约束，需明确后才能固定最终系数。

说明：不从系统名称静默补入方程。

### 新增 RadiusEquation

之后 [dependencies; model_suggestion]：\(r_{uv}=-\frac{\Omega^2}{4r}-\frac{r_u r_v}{r}\)。

说明：以参考文献的零电荷特例固定半径方程；作为待用输入，不是本次已完成的推导结果。

### 新增 ScalarNormalization

之后 [definitions; model_suggestion]：标量场记为 \(\phi\)，采用 \(\operatorname{Ric}_{ab}=2\partial_a\phi\partial_b\phi\) 的归一化，无电磁场和宇宙学常数项。

说明：本次选定的方程约定；不能与含其他耦合系数的标量场记号混用。

### 新增 UConstraint

之后 [dependencies; model_suggestion]：\(\partial_u(\Omega^{-2}r_u)=-r\Omega^{-2}(\phi_u)^2\)。

说明：与选定标量场归一化一致的 u 方向约束。

### 新增 VConstraint

之后 [dependencies; model_suggestion]：\(\partial_v(\Omega^{-2}r_v)=-r\Omega^{-2}(\phi_v)^2\)。

说明：与选定标量场归一化一致的 v 方向约束。

## 原始材料与版本

- S-71f3e610ecd18445667d38f4：request-I-50bc61ef294345ee9ace7967e613a5b3.txt [request]；SHA-256 `e8df12292d89bb2c516f8cc64494562facbcb0d876cf70d752c694421ee16913`；5 行。

- S-4ca47bb9d5347d91b3d618b8：conventions.md [material]；SHA-256 `0031248bb3b4c358c28591332057c454892ef6c269de22246d227d04d7260db5`；12 行。

- S-d89eac8cbd0262a0bf9fadc4：revision-I-226a126179504fb79c83cc4b1fd46708.txt [revision]；SHA-256 `4e05dd3bca53754d3397ab6403c5d728e63bbcdc2ad89018617a8c58f689d7b4`；1 行。

- S-e262ac7a0d009fc269d693ae：conventions.md [material]；SHA-256 `d6576a042037f98f61d5f1ec4ba48ed980ef60ec83d97fc843cdaf19f4620e1e`；12 行。

- S-fe97531a6db089a02aba279b：revision-I-5df289641e9a4e1cbd5586d51357c35d.txt [revision]；SHA-256 `4e05dd3bca53754d3397ab6403c5d728e63bbcdc2ad89018617a8c58f689d7b4`；1 行。
