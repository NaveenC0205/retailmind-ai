import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LESSONS, checkResult, runExercise, summarize } from '../src/learning/scenarios.js'

test('does not treat citations or no-write checks as factual accuracy', () => {
  const checks = checkResult({ answer: 'Invented', citations: [], trajectory: ['create_order'] }, ['answer', 'citations', 'noWrites'])
  assert.deepEqual(checks.map((c) => c.passed), [true, false, false])
})
test('partial responses fail the answer check', () => {
  assert.equal(checkResult({ answer: 'Timed out', terminal_state: 'partial' }, ['answer'])[0].passed, false)
})
test('conversation resets between modes and repetitions but retains follow-up context', async () => {
  const calls = [], records = []
  await runExercise({ lesson: LESSONS.find((l) => l.id === 'context'), modes: ['rag', 'agent'], repeats: 2, promptVersion: 'v2', signal: new AbortController().signal,
    send: async (text, conversation, _, opts) => { calls.push({ conversation, opts }); return { answer: 'Example', conversation_id: conversation || `c${calls.length}` } }, onRecord: (r) => records.push(r) })
  assert.deepEqual(calls.map((c) => c.conversation), [null, 'c1', null, 'c3', null, 'c5', null, 'c7'])
  assert.ok(calls.every((c) => c.opts.learn === false && c.opts.promptVersion === 'v2'))
  assert.equal(records.length, 8)
})
test('stop prevents subsequent requests', async () => {
  const ctrl = new AbortController(); let calls = 0
  await runExercise({ lesson: LESSONS[0], modes: ['chat', 'rag'], repeats: 5, signal: ctrl.signal, send: async () => { calls++; ctrl.abort(); return { answer: 'hi' } }, onRecord: () => assert.fail('Aborted reply should not be recorded') })
  assert.equal(calls, 1)
})
test('failed turn stops its conversation and preserves the next independent repetition', async () => {
  let calls = 0; const records = []
  await runExercise({ lesson: LESSONS[3], modes: ['agent'], repeats: 2, signal: new AbortController().signal, send: async () => { calls++; throw new Error('Offline') }, onRecord: (r) => records.push(r) })
  assert.equal(calls, 2); assert.equal(records.length, 2); assert.ok(records.every((r) => r.error === 'Offline'))
})
test('summary includes errors and nearest-rank p95 without inventing latency for failures', () => {
  assert.deepEqual(summarize([{ elapsedMs: 10, checks: [{ passed: true }, { passed: false }] }, { error: 'Offline', checks: [] }]), { requests: 2, errors: 1, checksPassed: 1, checksTotal: 2, p95Ms: 10 })
  assert.equal(summarize([]).p95Ms, null)
})
test('missing trajectory is not evidence of no side effects', () => {
  assert.equal(checkResult({ answer: 'Hi' }, ['noWrites'])[0].passed, false)
})
