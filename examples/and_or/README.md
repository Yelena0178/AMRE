# 二次式严格正性的 AND/OR 示例

定理 T 主张对所有实数 x，二次式 2x² + 2x + 1 严格为正。路线 A 通过配方，需要 L1 和 L2。
路线 B 将表达式写成两个平方之和，需要 L3 和 L4。两条路线各有独立的 LaTeX 归约和候选证据记录。
示例的证据结果为 `INCONCLUSIVE`，未录入正式验证或人工确认。

`research/research.yaml` 包含合同 `P-T`、图节点、边和一个实数假设集合。
从仓库根目录运行 `amre --config examples/and_or/amre.yaml problem validate P-T` 可验证引用。
`new-problem.yaml` 和 `status-patch.yaml` 是根目录 README 中 CLI 教程的输入。

## 从 Python 查询

```python
from pathlib import Path

from amre.research.store import ResearchStore

store = ResearchStore(Path("examples/and_or"))
document, graph = store.snapshot()
assert document.contract("P-T").dependencies == ("T",)
assert graph.dependency_alternatives("T") == (("L1", "L2"), ("L3", "L4"))
assert graph.downstream("L1") == ("A", "P-T", "T")
assert graph.assumptions_for("T") == ("H-Real",)
assert graph.problems_affected_by_obstacle("O-Review") == ("P-T",)
for route in graph.route_assessments("T"):
    print(route.route_id, route.ready, route.unmet_dependencies)
```

## 从 Python 修改

先复制本目录到一个可写工作区。以下示例更新该副本中的 L1，修改会同时保存新版本和复核标记。

```python
from amre.research.models import StatementRef

store = ResearchStore(Path("path/to/example-copy"))
original = store.read().graph
text = r"\[2x^2+2x+1=2(x+1/2)^2+1/2 \quad (x\in\mathbb{R}).\]"
nodes = tuple(
    node.model_copy(update={"statement": StatementRef.from_text(text)})
    if node.id == "L1" else node
    for node in original.nodes
)
store.replace_graph(original.model_copy(update={"nodes": nodes}),
                    expected=original, statements=(text,))
graph = store.graph()
assert graph.review("T").status == "NEEDS_RECHECK"
assert graph.review("B").status == "CURRENT"
```

使用 `store.register_evidence(record)` 将 `EvidenceRecord` 关联到指定断言和版本。
复核完成时可用 `store.acknowledge_recheck(id, revision=...)` 清除指定版本的复核标记。
此操作只记录复核流程，证据资格仍由当前版本、覆盖范围、结果和来源属性分别判断。

## 保存和恢复研究记忆

在工作区副本中使用 `checkpoint create P-T <checkpoint-notes.yaml>`。示例备注选用路线 A，
保存待核查的两个义务和下一步。用 `checkpoint restore P-T` 读取快照，用
`context build P-T --max-bytes 4000` 构建紧凑上下文。

可以通过 `memory record <event.yaml>` 追加手工记录，例如切换路线：

```yaml
kind: ROUTE_SWITCHED
problem_id: P-T
target_id: B
summary: Examine the alternative sum-of-squares reduction.
```

在本例中，`ATTEMPT_STARTED` 使用尝试编号作为 `target_id`，随后使用同一编号记录
`ATTEMPT_COMPLETED`，附上 `outcome` 和 `summary`。可提供工作区相对的 `artifact_paths`
归档计算日志或失败材料。保存新检查点时会收录已登记的最近一次尝试。
