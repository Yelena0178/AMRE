const rows = (value) => Array.isArray(value) ? value.filter(
  (item) => item && typeof item === 'object' && !Array.isArray(item)
) : []
const text = (value) => typeof value === 'string' ? value : undefined
const strings = (value) => Array.isArray(value) ? value.filter((item) => typeof item === 'string') : []
const withKeys = (value, ...keys) => rows(value).filter(
  (item) => keys.every((key) => typeof item[key] === 'string')
)

export function decodeSnapshot(raw) {
  if (!raw || typeof raw !== 'object' || raw.schema_version !== 1) {
    throw new Error('Unsupported research snapshot schema. Export a schema 1 snapshot.')
  }
  const warnings = []
  if (!Array.isArray(raw.nodes) || !Array.isArray(raw.edges) || !Array.isArray(raw.problems)) {
    warnings.push('Some research fields are missing from this snapshot.')
  }
  const ids = new Set()
  const nodes = rows(raw.nodes).filter((node) => {
    if (typeof node.id !== 'string' || typeof node.kind !== 'string' || ids.has(node.id)) {
      return false
    }
    ids.add(node.id)
    return true
  }).map((node) => ({
    ...node,
    label: text(node.label) ?? node.id,
    review_status: text(node.review_status) ?? 'UNKNOWN',
    assumptions: strings(node.assumptions),
    evidence: strings(node.evidence),
    statement: text(node.statement),
    detail: text(node.detail),
    category: text(node.category),
    claim_status: text(node.claim_status),
    obstacle_status: text(node.obstacle_status),
    obstacle_attempts: strings(node.obstacle_attempts),
    obstacle_routes: strings(node.obstacle_routes),
  }))
  const inputEdges = rows(raw.edges)
  const edges = inputEdges.filter(({ source, target, relation }) =>
    ids.has(source) && ids.has(target) && typeof relation === 'string'
  )
  if (edges.length !== inputEdges.length) warnings.push('Some graph links reference missing nodes.')
  const budget = raw.budget && typeof raw.budget === 'object' ? raw.budget : {}
  const usage = (value) => typeof value === 'number' && Number.isFinite(value) ? value : 'UNKNOWN'
  return {
    warnings,
    value: {
      ...raw, nodes, edges,
      source_revision: text(raw.source_revision), generated_at: text(raw.generated_at),
      problems: withKeys(raw.problems, 'problem_id').filter((item) => ids.has(item.problem_id)).map((item) => ({
        ...item, active_route: text(item.active_route), checkpoint: text(item.checkpoint),
        research_status: text(item.research_status), next_action: text(item.next_action),
        route_ids: strings(item.route_ids), definitions: strings(item.definitions),
        obligations: strings(item.obligations),
      })),
      programs: withKeys(raw.programs, 'program'),
      attempts: withKeys(raw.attempts, 'attempt_id', 'problem_id', 'state').map((item) => ({
        ...item, strategy: text(item.strategy), tokens: usage(item.tokens),
      })),
      sessions: withKeys(raw.sessions, 'session_id', 'problem_id', 'state').map((item) => ({
        ...item, route_id: text(item.route_id), checkpoint: text(item.checkpoint),
        continuity: text(item.continuity), semantic_events: usage(item.semantic_events),
      })),
      checkpoints: withKeys(raw.checkpoints, 'checkpoint_id', 'problem_id').map((item) => ({
        ...item, route_id: text(item.route_id), dispatch_phase: text(item.dispatch_phase),
      })),
      events: withKeys(raw.events, 'event_id', 'problem_id', 'kind').map((item) => ({
        ...item, route_id: text(item.route_id),
      })),
      decisions: withKeys(raw.decisions, 'decision_id', 'problem_id', 'route_id').map((item) => ({
        ...item, strategy_version: text(item.strategy_version), allocation: text(item.allocation),
        branch_state: text(item.branch_state), stagnation_count: usage(item.stagnation_count),
        cooldown_remaining: usage(item.cooldown_remaining),
      })),
      artifacts: withKeys(raw.artifacts, 'artifact_id', 'assertion_id').map((item) => ({
        ...item, source_type: text(item.source_type), coverage: text(item.coverage),
        check_result: text(item.check_result), properties: strings(item.properties),
      })),
      budget: {
        elapsed_ms: usage(budget.elapsed_ms), reserved_ms: usage(budget.reserved_ms),
        tokens: usage(budget.tokens), cost_microusd: usage(budget.cost_microusd),
        tool_calls: usage(budget.tool_calls),
      },
      omitted_details: typeof raw.omitted_details === 'number' ? raw.omitted_details : 0,
    },
  }
}

export function linkedNodes(edges, initial, direction) {
  const found = new Set()
  const queue = [initial]
  const origin = direction === 'upstream' ? 'source' : 'target'
  const destination = direction === 'upstream' ? 'target' : 'source'
  while (queue.length) {
    const key = queue.shift()
    for (const edge of edges) {
      if (edge[origin] === key && edge[destination] !== initial && !found.has(edge[destination])) {
        found.add(edge[destination])
        queue.push(edge[destination])
      }
    }
  }
  return [...found]
}
