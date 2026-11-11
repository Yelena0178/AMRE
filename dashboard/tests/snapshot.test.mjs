import assert from 'node:assert/strict'
import { test } from 'node:test'
import { decodeSnapshot, linkedNodes } from '../src/snapshot.mjs'

test('unsupported schemas are rejected and missing data renders safely', () => {
  assert.throws(() => decodeSnapshot({ schema_version: 42 }), /Unsupported/)
  const decoded = decodeSnapshot({ schema_version: 1 })
  assert.deepEqual(decoded.value.problems, [])
  assert.equal(decoded.warnings.length, 1)
  assert.deepEqual(decodeSnapshot({ schema_version: 1, sessions: [{}] }).value.sessions, [])
})

test('broken edges are ignored and AND/OR directions remain distinct', () => {
  const data = decodeSnapshot({
    schema_version: 1, problems: [{ problem_id: 'T' }],
    nodes: [
      { id: 'T', kind: 'THEOREM' }, { id: 'A', kind: 'PROOF_ROUTE' }, { id: 'L1', kind: 'LEMMA' },
    ],
    edges: [
      { source: 'T', target: 'A', relation: 'ALTERNATIVE_ROUTE' },
      { source: 'A', target: 'L1', relation: 'REQUIRES_ALL' },
      { source: 'L1', target: 'Missing', relation: 'DEPENDS_ON' },
    ],
  })
  assert.match(data.warnings[0], /missing nodes/)
  assert.deepEqual(linkedNodes(data.value.edges, 'T', 'upstream'), ['A', 'L1'])
  assert.deepEqual(linkedNodes(data.value.edges, 'L1', 'downstream'), ['A', 'T'])
  assert.deepEqual(data.value.nodes[0].evidence, [])
})
