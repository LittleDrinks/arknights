import assert from 'node:assert/strict';

// Pure append: callers publish under their own lock/CAS. No filesystem mutation and no
// last-write-wins deduplication that could silently replace an existing recording/episode.
export function mergeIndexes(existingAgent, existingRecordings, addedAgent, addedRecordings) {
  assert.equal(existingAgent.schema, addedAgent.schema, 'Index schema mismatch');
  const append = (oldRows, newRows, key) => {
    const keys = new Set(), files = new Set();
    for (const row of [...oldRows, ...newRows]) {
      assert(typeof row[key] === 'string' && typeof row.file === 'string', 'Invalid index entry');
      assert(!keys.has(row[key]) && !files.has(row.file), `Index collision: ${row[key]}`);
      assert(/^[A-Za-z0-9._/-]+$/.test(row.file) && !row.file.startsWith('/') &&
        row.file.split('/').every(part => part && part !== '.' && part !== '..'), 'Unsafe index path');
      keys.add(row[key]); files.add(row.file);
    }
    return structuredClone([...oldRows, ...newRows]);
  };
  const episodes = append(existingAgent.episodes, addedAgent.episodes, 'id');
  const recordings = append(existingRecordings, addedRecordings, 'name');
  for (const ep of episodes) for (const b of ep.battles || []) {
    assert(recordings.some(r => r.name === b.name && r.file === b.file), `Missing recording: ${b.name}`);
  }
  return { agent: { ...structuredClone(existingAgent), episodes,
    totalSteps: episodes.reduce((n, e) => n + e.steps, 0),
    totalBytes: episodes.reduce((n, e) => n + (e.bytes || 0), 0) }, recordings };
}
