# 研究任务书草案 v1

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

## 定义

### HawkingMass [user]

\(m=\frac r2(1+4\Omega^{-2}r_u r_v)\)，其中下标表示相应坐标偏导。

说明：仅统一显示转义，不改变原质量定义。

出处：S-71f3e610ecd18445667d38f4，行 3–3

> m=\frac r2\left(1+4\Omega^{-2}r_u r_v\right).\

## 已有依赖

未提供。

## 旧材料中的结果（未由 Intake 验证）

未提供。

## 未完成步骤与缺口

### MissingEquations [unresolved]

原题没有写出半径方程及两条零方向约束，需明确后才能固定最终系数。

说明：不从系统名称静默补入方程。

出处：S-71f3e610ecd18445667d38f4，行 5–5

> 从半径方程和两条零方向约束推导 \\(m_u\\) 与 \\(m_v\\) 的精确表达式，逐步展示抵消项。

## 拟议验收条件

### ShowCancellations [user]

分别完整展开 m 的 u、v 偏导，标明各次代入，逐项展示几何项与 Ω 导数项的抵消，保留最终标量场通量项。

出处：S-71f3e610ecd18445667d38f4，行 5–5

> 从半径方程和两条零方向约束推导 \\(m_u\\) 与 \\(m_v\\) 的精确表达式，逐步展示抵消项。

## 待确认问题

### Conventions [unresolved]

采用哪一种双零度规及标量场归一化？半径方程和两条约束的具体形式是什么？

说明：原题没有指定，最终系数依赖这些约定。

## 本次变化（由程序比较版本生成）

### 新增 Conventions

之后 [questions; unresolved]：采用哪一种双零度规及标量场归一化？半径方程和两条约束的具体形式是什么？

说明：原题没有指定，最终系数依赖这些约定。

### 新增 Goal

之后 [goal; user]：在四维球对称 Einstein–无质量标量场系统中，由半径方程与两条零方向约束推导 \(m_u,m_v\) 的精确表达式，并逐步展示抵消项。

### 新增 HawkingMass

之后 [definitions; user]：\(m=\frac r2(1+4\Omega^{-2}r_u r_v)\)，其中下标表示相应坐标偏导。

说明：仅统一显示转义，不改变原质量定义。

### 新增 MissingEquations

之后 [gaps; unresolved]：原题没有写出半径方程及两条零方向约束，需明确后才能固定最终系数。

说明：不从系统名称静默补入方程。

### 新增 PositiveRadiusLapse

之后 [assumptions; user]：\(r>0,\ \Omega>0\)。

### 新增 ShowCancellations

之后 [acceptance_criteria; user]：分别完整展开 m 的 u、v 偏导，标明各次代入，逐项展示几何项与 Ω 导数项的抵消，保留最终标量场通量项。

## 原始材料与版本

- S-71f3e610ecd18445667d38f4：request-I-50bc61ef294345ee9ace7967e613a5b3.txt [request]；SHA-256 `e8df12292d89bb2c516f8cc64494562facbcb0d876cf70d752c694421ee16913`；5 行。
