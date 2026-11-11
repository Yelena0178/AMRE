import cytoscape from 'cytoscape'
import { useEffect, useRef, useState } from 'react'
import type { GraphEdge, GraphNode } from './types'

const colors: Record<string, string> = {
  RESEARCH_PROBLEM: '#213a53', THEOREM: '#365a7a', PROOF_ROUTE: '#3a7161',
  ASSUMPTION: '#7371a4', OBSTACLE: '#a65833', EVIDENCE: '#6b7955',
  LITERATURE_REFERENCE: '#57778b', LEMMA: '#507695', CLAIM: '#476682',
}

const shape: Record<string, string> = {
  PROOF_ROUTE: 'round-rectangle', ASSUMPTION: 'diamond', OBSTACLE: 'hexagon',
  EVIDENCE: 'ellipse', LITERATURE_REFERENCE: 'ellipse',
}

export function Graph({ nodes, edges, selected, onSelect }: {
  nodes: GraphNode[]; edges: GraphEdge[]; selected: string; onSelect: (id: string) => void
}) {
  const element = useRef<HTMLDivElement>(null)
  const core = useRef<cytoscape.Core | null>(null)
  const [kind, setKind] = useState('')
  const [review, setReview] = useState('')
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!element.current || !nodes.length) return
    const graph = cytoscape({
      container: element.current,
      elements: [
        ...nodes.map((node) => ({ data: {
          id: node.id, name: node.id, color: colors[node.kind] ?? '#476682',
          shape: shape[node.kind] ?? 'round-rectangle',
        } })),
        ...edges.map((edge, index) => ({ data: {
          id: '$edge-' + index, source: edge.source, target: edge.target,
          label: edge.relation === 'REQUIRES_ALL' ? 'AND'
            : edge.relation === 'ALTERNATIVE_ROUTE' ? 'OR' : '',
          relation: edge.relation,
        } })),
      ],
      minZoom: 0.22, maxZoom: 2.2, boxSelectionEnabled: false,
      layout: { name: 'breadthfirst', directed: true, padding: 35, spacingFactor: 1.25 },
      style: [
        { selector: 'node', style: {
          'background-color': 'data(color)', shape: (node) => node.data('shape'), label: 'data(name)',
          color: '#fff', 'text-valign': 'center', 'text-halign': 'center',
          'font-size': 10, 'font-weight': 700, 'text-max-width': '66px',
          'text-wrap': 'ellipsis', width: 66, height: 36, 'border-width': 2,
          'border-color': '#fafbfc',
        } },
        { selector: 'edge', style: {
          'curve-style': 'bezier', width: 1.3, 'line-color': '#8ea1ae',
          'target-arrow-color': '#8ea1ae', 'target-arrow-shape': 'triangle',
          label: 'data(label)', 'font-size': 8, 'font-weight': 700, color: '#365166',
          'text-background-color': '#f6f8fa', 'text-background-opacity': 1,
          'text-background-padding': '2px',
        } },
        { selector: 'edge[relation = "REQUIRES_ALL"]', style: { 'line-color': '#548b79', width: 2 } },
        { selector: 'edge[relation = "ALTERNATIVE_ROUTE"]', style: { 'line-style': 'dashed', width: 2 } },
        { selector: '.chosen', style: { 'border-color': '#d39139', 'border-width': 4 } },
        { selector: '.filtered', style: { opacity: 0.13 } },
      ],
    })
    graph.on('tap', 'node', (event) => onSelect(event.target.id()))
    core.current = graph
    const resize = new ResizeObserver(() => graph.resize())
    resize.observe(element.current)
    return () => {
      resize.disconnect()
      core.current = null
      graph.destroy()
    }
  }, [nodes, edges, onSelect])

  useEffect(() => {
    const graph = core.current
    if (!graph) return
    graph.nodes().removeClass('chosen')
    graph.getElementById(selected).addClass('chosen')
    const needle = query.trim().toLowerCase()
    const hidden = new Set(nodes.filter((node) =>
      (kind && kind !== node.kind) || (review && review !== node.review_status)
      || (needle && !(node.id + ' ' + node.label).toLowerCase().includes(needle))
    ).map((node) => node.id))
    graph.batch(() => {
      graph.nodes().forEach((node) => { node.toggleClass('filtered', hidden.has(node.id())) })
      graph.edges().forEach((edge) => {
        edge.toggleClass('filtered', hidden.has(edge.source().id()) || hidden.has(edge.target().id()))
      })
    })
  }, [selected, kind, review, query, nodes])

  return <section className="panel graph-panel" aria-labelledby="graph-heading">
    <div className="panel-heading">
      <div><p className="eyebrow">Structure</p><h2 id="graph-heading">Research graph</h2></div>
      <div className="graph-actions">
        <button onClick={() => core.current?.zoom((core.current?.zoom() ?? 1) * 1.2)} aria-label="Zoom in">+</button>
        <button onClick={() => core.current?.zoom((core.current?.zoom() ?? 1) / 1.2)} aria-label="Zoom out">−</button>
        <button onClick={() => core.current?.fit(undefined, 35)}>Fit</button>
      </div>
    </div>
    <div className="graph-filters">
      <input aria-label="Find graph nodes" placeholder="Find a node…" value={query}
        onChange={(event) => setQuery(event.target.value)} />
      <select aria-label="Filter node type" value={kind} onChange={(event) => setKind(event.target.value)}>
        <option value="">All node types</option>
        {[...new Set(nodes.map((node) => node.kind))].sort().map((type) =>
          <option key={type} value={type}>{type.replaceAll('_', ' ').toLowerCase()}</option>)}
      </select>
      <select aria-label="Filter review status" value={review} onChange={(event) => setReview(event.target.value)}>
        <option value="">All review states</option>
        <option value="CURRENT">Current</option>
        <option value="NEEDS_RECHECK">Needs recheck</option>
      </select>
    </div>
    <div className="graph-wrap">
      {nodes.length ? <div className="graph-canvas" ref={element} aria-label="Interactive proof graph" />
        : <p className="empty">No public graph nodes are available.</p>}
    </div>
    <div className="legend">
      <span><i className="legend-and" /> AND · all required lemmas</span>
      <span><i className="legend-or" /> OR · alternative routes</span>
      <span>{nodes.length} nodes · {edges.length} links</span>
    </div>
  </section>
}
