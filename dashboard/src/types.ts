export type Usage = number | 'UNKNOWN'

export interface GraphNode {
  id: string
  kind: string
  label: string
  review_status: string
  claim_status?: string
  statement?: string
  assumptions: string[]
  evidence: string[]
  detail?: string
  category?: string
  obstacle_status?: string
  obstacle_attempts?: string[]
  obstacle_routes?: string[]
}

export interface GraphEdge {
  source: string
  target: string
  relation: string
}

export interface Problem {
  problem_id: string
  research_status: string
  claim_status: string
  route_ids?: string[]
  active_route?: string
  checkpoint?: string
  obstacle_ids?: string[]
  evidence_ids?: string[]
  definitions?: string[]
  obligations?: string[]
  next_action?: string
}

export interface Attempt {
  attempt_id: string
  problem_id: string
  route_id?: string
  session_id?: string
  state: string
  strategy?: string
  elapsed_ms: Usage
  reserved_ms: number
  tokens: Usage
  tool_calls: Usage
}

export interface Session {
  session_id: string
  problem_id: string
  route_id?: string
  state: string
  elapsed_ms: number
  session_limit_ms?: number
  checkpoint?: string
  continuity?: string
  semantic_events: number
}

export interface Artifact {
  artifact_id: string
  assertion_id: string
  source_type: string
  check_result: string
  coverage: string
  properties?: string[]
  private: boolean
}

export interface Snapshot {
  schema_version: 1
  generated_at?: string
  source_revision?: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  problems: Problem[]
  programs: { program: string; problems: string[] }[]
  attempts: Attempt[]
  sessions: Session[]
  artifacts: Artifact[]
  checkpoints: {
    checkpoint_id: string; problem_id: string; route_id?: string; dispatch_phase?: string; timestamp: string
  }[]
  events: { event_id: string; problem_id: string; route_id?: string; kind: string; timestamp: string }[]
  decisions: {
    decision_id: string; problem_id: string; route_id: string; strategy_version?: string; allocation: string;
    branch_state: string; stagnation_count: number; cooldown_remaining: number
  }[]
  budget: { elapsed_ms?: number; reserved_ms?: number; tokens?: Usage; cost_microusd?: Usage; tool_calls?: Usage }
  omitted_details?: number
}
