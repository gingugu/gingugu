// A small brain in the /api/graph wire format, shared by the unit suites.
import type { GraphPayload } from '../api/types'

export const NOW = new Date('2026-10-25T12:00:00Z')

export const GRAPH: GraphPayload = {
  ok: true,
  version: 'v1',
  namespaces: [
    { id: 'ns-crow', name: 'crow', count: 2 },
    { id: 'ns-gin', name: 'gingugu', count: 3 },
  ],
  tags: ['git', 'stacking', 'sqlite', 'release'],
  nodes: [
    {
      id: 'm-rule', ns: 'ns-crow', type: 'preference', conf: 'verified',
      title: 'Never delete a stack parent branch', pinned: true, access: 40,
      created: '2026-08-01T00:00:00+00:00', updated: '2026-10-20T00:00:00+00:00',
      accessed: '2026-10-24T00:00:00+00:00', confirmed: '2026-10-20T00:00:00+00:00',
      tags: [0, 1],
    },
    {
      id: 'm-lesson', ns: 'ns-crow', type: 'pattern', conf: 'inferred',
      title: 'Retarget child PRs before merging', pinned: false, access: 3,
      created: '2026-08-02T00:00:00+00:00', updated: '2026-08-02T00:00:00+00:00',
      accessed: '2026-10-01T00:00:00+00:00', confirmed: null,
      tags: [0, 1],
    },
    {
      id: 'm-schema', ns: 'ns-gin', type: 'decision', conf: 'verified',
      title: 'Migrations key off user_version', pinned: false, access: 0,
      created: '2026-06-01T00:00:00+00:00', updated: '2026-06-01T00:00:00+00:00',
      accessed: '2026-06-01T00:00:00+00:00', confirmed: '2026-06-01T00:00:00+00:00',
      tags: [2],
    },
    {
      id: 'm-old', ns: 'ns-gin', type: 'fact', conf: 'deprecated',
      title: 'FTS triggers were hand written', pinned: false, access: 1,
      created: '2026-06-01T00:00:00+00:00', updated: '2026-06-02T00:00:00+00:00',
      accessed: '2026-10-22T00:00:00+00:00', confirmed: null,
      tags: [2],
    },
    {
      id: 'm-dated', ns: 'ns-gin', type: 'workflow', conf: 'verified',
      title: 'Release notes for the day', pinned: false, access: 5,
      created: '2026-10-20T00:00:00+00:00', updated: '2026-10-20T00:00:00+00:00',
      accessed: '2026-10-20T00:00:00+00:00', confirmed: '2026-10-20T00:00:00+00:00',
      tags: [3],
    },
  ],
  relations: [
    { s: 'm-lesson', t: 'm-rule', type: 'caused_by', created: '2026-08-02T00:00:00+00:00' },
    { s: 'm-schema', t: 'm-old', type: 'supersedes', created: '2026-06-02T00:00:00+00:00' },
    { s: 'm-schema', t: 'm-gone', type: 'related_to', created: '2026-06-02T00:00:00+00:00' },
  ],
  tag_edges: [
    { s: 'm-lesson', t: 'm-rule', w: 3.2 },
    { s: 'm-old', t: 'm-schema', w: 0.8 },
  ],
} // fmt: one node per block on purpose - readable fixtures beat dense ones
