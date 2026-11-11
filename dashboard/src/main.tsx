import { StrictMode, useCallback, useEffect, useState, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import { Graph } from './Graph'
import { decodeSnapshot, linkedNodes } from './snapshot.mjs'
import type { Artifact, GraphNode, Snapshot, Usage } from './types'
import './styles.css'

const sections = ['Graph', 'Sessions', 'Evidence', 'Obstructions'] as const
type Section = (typeof sections)[number]

const readable = (value?: string) => value?.replaceAll('_', ' ').toLowerCase() ?? 'unknown'
const time = (value?: Usage) => typeof value === 'number' ? (value / 1000).toFixed(2) + ' s' : 'UNKNOWN'
const display = (value?: Usage) => value ?? 'UNKNOWN'
const stamp = (value?: string) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString() : 'Timestamp unavailable'

function Badge({ children }: { children: ReactNode }) {
  return <span className="badge">{children}</span>
}

function MathText({ source }: { source: string }) {
  const blocks = source.split(/(\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|\$\$[\s\S]+?\$\$|\$[^$\n]+\$)/g)
  const hasDelimiters = blocks.length > 1
  const render = (text: string, displayMode: boolean) => {
    try {
      return katex.renderToString(text, {
        throwOnError: true, strict: 'error', trust: false, maxExpand: 100, maxSize: 10,
        displayMode, output: 'htmlAndMathml',
      })
    } catch {
      return null
    }
  }
  if (!hasDelimiters) {
    const compiled = source.includes('\\') ? render(source, true) : null
    return compiled
      ? <div className="math" dangerouslySetInnerHTML={{ __html: compiled }} />
      : <pre className="math-fallback">{source}</pre>
  }
  return <div className="math">{blocks.map((block, index) => {
    if (index % 2 === 0) return <span key={index}>{block}</span>
    const strip = block.startsWith('$') && !block.startsWith('$$') ? 1 : 2
    const compiled = render(block.slice(strip, -strip), block.startsWith('\\[') || block.startsWith('$$'))
    return compiled ? <span key={index} dangerouslySetInnerHTML={{ __html: compiled }} />
      : <code key={index} title="Could not render this formula">{block}</code>
  })}</div>
}

function NodeLinks({ ids, onSelect }: { ids?: string[]; onSelect: (id: string) => void }) {
  return ids?.length ? <div className="node-links">{ids.map((id) =>
    <button key={id} onClick={() => onSelect(id)} title={id}>{id}</button>)}</div>
    : <span className="muted">None in this snapshot</span>
}

function EvidenceLevel({ item }: { item: Artifact }) {
  return <><Badge>{readable(item.source_type)}</Badge>
    {item.properties?.map((name) => <Badge key={name}>{readable(name)}</Badge>)}</>
}

function Inspector({ node, snapshot, onSelect }: {
  node?: GraphNode; snapshot: Snapshot; onSelect: (id: string) => void
}) {
  const problem = snapshot.problems.find((item) => item.problem_id === node?.id)
  const artifact = snapshot.artifacts.find((item) => item.artifact_id === node?.id)
  if (!node) return <aside className="panel inspector"><p className="empty">Select a graph node.</p></aside>
  const upstream = linkedNodes(snapshot.edges, node.id, 'upstream')
  const downstream = linkedNodes(snapshot.edges, node.id, 'downstream')
  return <aside className="panel inspector" aria-label="Node inspector">
    <p className="eyebrow">Node inspector</p>
    <h2>{node.id}</h2>
    {node.label !== node.id && <p className="node-title">{node.label}</p>}
    <div className="badges">
      <Badge>{readable(node.kind)}</Badge><Badge>{readable(node.review_status)}</Badge>
      {node.claim_status && <Badge>{readable(node.claim_status)}</Badge>}
      {node.obstacle_status && <Badge>{readable(node.obstacle_status)}</Badge>}
    </div>
    {node.statement && <section className="detail"><h3>Mathematical statement</h3><MathText source={node.statement} /></section>}
    {node.detail && <section className="detail"><h3>{node.category ? readable(node.category) : 'Source detail'}</h3>
      <p className="prewrap">{node.detail}</p></section>}
    {artifact && <section className="detail"><h3>Evidence level</h3><div className="badges"><EvidenceLevel item={artifact} /></div>
      <p>{readable(artifact.coverage)} · {readable(artifact.check_result)}</p></section>}
    <section className="detail"><h3>Assumptions</h3><NodeLinks ids={node.assumptions} onSelect={onSelect} /></section>
    <section className="detail"><h3>Evidence</h3><NodeLinks ids={node.evidence} onSelect={onSelect} /></section>
    <section className="detail"><h3>Upstream research</h3><NodeLinks ids={upstream} onSelect={onSelect} /></section>
    <section className="detail"><h3>Downstream impact</h3><NodeLinks ids={downstream} onSelect={onSelect} /></section>
    {problem && <>
      <section className="detail"><h3>Problem explorer</h3>
        <p>Research state <strong>{readable(problem.research_status)}</strong></p>
        <p>Active route <strong>{problem.active_route ?? 'None recorded'}</strong></p>
        <p>Latest checkpoint <code>{problem.checkpoint ?? 'None recorded'}</code></p>
        <NodeLinks ids={problem.route_ids} onSelect={onSelect} />
      </section>
      {!!problem.definitions?.length && <section className="detail"><h3>Definitions</h3>
        <ul>{problem.definitions.map((line, index) => <li key={index}>{line}</li>)}</ul></section>}
      {!!problem.obligations?.length && <section className="detail"><h3>Open proof obligations</h3>
        <ul>{problem.obligations.map((line, index) => <li key={index}>{line}</li>)}</ul></section>}
      {problem.next_action && <section className="detail"><h3>Next action</h3><p>{problem.next_action}</p></section>}
    </>}
  </aside>
}

function SessionPanel({ snapshot }: { snapshot: Snapshot }) {
  const switches = snapshot.events.filter((event) => event.kind === 'ROUTE_SWITCHED')
  return <section className="panel list-panel">
    <div className="panel-heading"><div><p className="eyebrow">Execution</p><h2>Deep research sessions</h2></div></div>
    {snapshot.sessions.length ? <div className="table-wrap"><table>
      <thead><tr><th>Session</th><th>Problem / route</th><th>State</th><th>Elapsed / limit</th><th>Continuity</th><th>Checkpoint</th></tr></thead>
      <tbody>{snapshot.sessions.map((item) => <tr key={item.session_id}>
        <td><code title={item.session_id}>{item.session_id.slice(0, 15)}</code><small>{item.semantic_events} semantic events</small></td>
        <td>{item.problem_id} / {item.route_id ?? '—'}</td>
        <td><Badge>{readable(item.state)}</Badge></td>
        <td>{time(item.elapsed_ms)} / {time(item.session_limit_ms)}</td><td>{readable(item.continuity)}</td>
        <td><code title={item.checkpoint}>{item.checkpoint?.slice(0, 15) ?? '—'}</code></td>
      </tr>)}</tbody>
    </table></div> : <p className="empty">No deep sessions recorded.</p>}
    <div className="panel-heading subheading"><h2>Attempts and budget</h2></div>
    <p className="usage">Elapsed <b>{time(snapshot.budget.elapsed_ms)}</b> · Reserved <b>{time(snapshot.budget.reserved_ms)}</b> ·
      Tokens <b>{display(snapshot.budget.tokens)}</b> · Tool calls <b>{display(snapshot.budget.tool_calls)}</b> ·
      Cost (µUSD) <b>{display(snapshot.budget.cost_microusd)}</b></p>
    {snapshot.attempts.length ? <div className="table-wrap"><table>
      <thead><tr><th>Attempt</th><th>Problem</th><th>Strategy</th><th>State</th><th>Elapsed</th><th>Tokens</th></tr></thead>
      <tbody>{snapshot.attempts.map((item) => <tr key={item.attempt_id}>
        <td><code title={item.attempt_id}>{item.attempt_id.slice(0, 15)}</code></td>
        <td>{item.problem_id}</td><td>{readable(item.strategy)}</td>
        <td><Badge>{readable(item.state)}</Badge></td><td>{time(item.elapsed_ms)}</td><td>{display(item.tokens)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="empty">No public attempts recorded.</p>}
    <div className="panel-heading subheading"><h2>Route and checkpoint history</h2></div>
    {switches.map((item) => <p className="history-line" key={item.event_id}>
      <Badge>route switch</Badge> {item.problem_id} → {item.route_id ?? 'unavailable'} <span>{stamp(item.timestamp)}</span>
    </p>)}
    {snapshot.checkpoints.slice().reverse().map((item) => <p className="history-line" key={item.checkpoint_id}>
      <Badge>{item.dispatch_phase ? readable(item.dispatch_phase) : 'checkpoint'}</Badge>
      <code title={item.checkpoint_id}>{item.checkpoint_id.slice(0, 15)}</code> · {item.problem_id}
      {item.route_id && ' / ' + item.route_id} <span>{stamp(item.timestamp)}</span>
    </p>)}
    {!switches.length && !snapshot.checkpoints.length && <p className="empty">No checkpoint history recorded.</p>}
  </section>
}

function EvidencePanel({ snapshot, onSelect }: { snapshot: Snapshot; onSelect: (id: string) => void }) {
  const sources = snapshot.nodes.filter((node) => node.kind === 'LITERATURE_REFERENCE')
  return <section className="panel list-panel">
    <div className="panel-heading"><div><p className="eyebrow">Provenance</p><h2>Research artifacts</h2></div></div>
    {snapshot.artifacts.length ? <div className="table-wrap"><table>
      <thead><tr><th>Artifact</th><th>Assertion</th><th>Evidence level</th><th>Coverage</th><th>Result</th></tr></thead>
      <tbody>{snapshot.artifacts.map((item) => <tr key={item.artifact_id}>
        <td><button onClick={() => onSelect(item.artifact_id)}>{item.artifact_id}</button>
          {item.private && <small>Private source file</small>}</td>
        <td>{item.assertion_id}</td><td><div className="badges"><EvidenceLevel item={item} /></div></td>
        <td>{readable(item.coverage)}</td><td>{readable(item.check_result)}</td>
      </tr>)}</tbody>
    </table></div> : <p className="empty">No public artifacts recorded.</p>}
    <div className="panel-heading subheading"><h2>Literature</h2></div>
    <NodeLinks ids={sources.map((item) => item.id)} onSelect={onSelect} />
  </section>
}

function ObstructionPanel({ snapshot, onSelect }: { snapshot: Snapshot; onSelect: (id: string) => void }) {
  const obstacles = snapshot.nodes.filter((item) => item.kind === 'OBSTACLE')
  return <section className="panel list-panel">
    <div className="panel-heading"><div><p className="eyebrow">Research blockers</p><h2>Obstructions</h2></div></div>
    {obstacles.map((item) => <article className="obstacle" key={item.id}>
      <button onClick={() => onSelect(item.id)}>{item.label}</button>
      <Badge>{readable(item.obstacle_status)}</Badge><Badge>{readable(item.category)}</Badge>
      {item.detail && <p className="prewrap">{item.detail}</p>}
      <p className="muted">{item.obstacle_attempts?.length ?? 0} recorded attempts · {item.obstacle_routes?.length ?? 0} scoped routes</p>
    </article>)}
    {!obstacles.length && <p className="empty">No public obstructions recorded.</p>}
    <div className="panel-heading subheading"><h2>Strategy decisions</h2></div>
    {snapshot.decisions.length ? <div className="table-wrap"><table>
      <thead><tr><th>Route</th><th>Strategy version</th><th>Allocation</th><th>Branch state</th><th>Stagnation / cooldown</th></tr></thead>
      <tbody>{snapshot.decisions.map((item) => <tr key={item.decision_id}>
        <td><button onClick={() => onSelect(item.route_id)}>{item.route_id}</button></td>
        <td>{item.strategy_version ?? 'UNKNOWN'}</td><td>{readable(item.allocation)}</td>
        <td>{readable(item.branch_state)}</td><td>{item.stagnation_count} / {item.cooldown_remaining}</td>
      </tr>)}</tbody>
    </table></div> : <p className="empty">No recorded scheduling decisions.</p>}
  </section>
}

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot>()
  const [warnings, setWarnings] = useState<string[]>([])
  const [error, setError] = useState('')
  const [section, setSection] = useState<Section>('Graph')
  const [selected, setSelected] = useState('')
  const onSelect = useCallback((id: string) => setSelected(id), [])
  useEffect(() => {
    const controller = new AbortController()
    fetch(import.meta.env.BASE_URL + 'research-state.json', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Could not load the public research snapshot.')
        return response.json()
      })
      .then((raw: unknown) => {
        const decoded = decodeSnapshot(raw)
        const data = decoded.value as Snapshot
        setSnapshot(data)
        setSelected(data.problems[0]?.problem_id ?? data.nodes[0]?.id ?? '')
        setWarnings(decoded.warnings)
      })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [])

  return <>
    <header className="masthead">
      <div className="header-inner">
        <div className="brand"><span aria-hidden="true">∴</span><b>AMRE</b><i />Research Workbench</div>
        <div className="snapshot-mark"><span /> Public snapshot <b>Read only</b></div>
      </div>
    </header>
    <main className="workbench">
      <div className="intro">
        <div><p className="eyebrow">Autonomous Mathematical Research Engine</p><h1>Research overview</h1>
          <p>Inspect proof routes, research evidence, and continuous sessions.</p></div>
        {snapshot && <div className="stamp"><b>{snapshot.source_revision
          ? 'Revision ' + snapshot.source_revision.slice(0, 10) : 'Uncommitted workspace'}</b>
          <span>{stamp(snapshot.generated_at)}</span></div>}
      </div>
      {error && <p className="banner" role="alert">{error}</p>}
      {!snapshot && !error && <p className="empty" role="status">Loading research snapshot…</p>}
      {snapshot && <>
        {warnings.map((message) => <p className="banner" role="status" key={message}>{message}</p>)}
        <div className="metrics" aria-label="Research metrics">
          {([
            ['Programs', snapshot.programs.length],
            ['Problems', snapshot.problems.length],
            ['Proof candidates', snapshot.nodes.filter((node) => node.claim_status === 'PROOF_CANDIDATE').length],
            ['Open obstructions', snapshot.nodes.filter((node) => node.obstacle_status === 'OPEN').length],
            ['Active routes', new Set(snapshot.problems.map((item) => item.active_route).filter(Boolean)).size],
            ['Attempts', snapshot.attempts.length],
          ] as const).map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong></div>)}
        </div>
        <nav className="sections" aria-label="Dashboard sections">{sections.map((name) =>
          <button key={name} aria-current={section === name ? 'page' : undefined}
            onClick={() => setSection(name)}>{name}</button>)}</nav>
        {!snapshot.problems.length && <p className="banner">This snapshot contains no public research problems.</p>}
        <div className="columns">
          <aside className="panel index">
            <p className="eyebrow">Research index</p><h2>Problems</h2>
            {snapshot.problems.map((item) => <button className="index-item" key={item.problem_id}
              aria-pressed={item.problem_id === selected} onClick={() => { onSelect(item.problem_id); setSection('Graph') }}>
              <b>{item.problem_id}</b><small>{readable(item.research_status)}</small>
            </button>)}
            <h3>Nodes</h3>
            <div className="index-scroll">
              {snapshot.nodes.map((node) => <button className="index-node" key={node.id}
                aria-pressed={node.id === selected} onClick={() => onSelect(node.id)}>
                <b>{node.id}</b><small>{readable(node.kind)}</small>
              </button>)}
            </div>
          </aside>
          {section === 'Graph' && <Graph nodes={snapshot.nodes} edges={snapshot.edges} selected={selected} onSelect={onSelect} />}
          {section === 'Sessions' && <SessionPanel snapshot={snapshot} />}
          {section === 'Evidence' && <EvidencePanel snapshot={snapshot} onSelect={onSelect} />}
          {section === 'Obstructions' && <ObstructionPanel snapshot={snapshot} onSelect={onSelect} />}
          <Inspector node={snapshot.nodes.find((item) => item.id === selected)} snapshot={snapshot} onSelect={onSelect} />
        </div>
        <footer><span>AMRE public research snapshot · schema {snapshot.schema_version}</span>
          <span>{snapshot.omitted_details ? snapshot.omitted_details + ' sensitive details withheld' : 'Evidence levels apply to the recorded scope'}</span></footer>
      </>}
    </main>
  </>
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
