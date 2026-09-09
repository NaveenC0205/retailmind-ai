import { test, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { chatStream } from '../src/api.js'

const originalFetch = globalThis.fetch
globalThis.localStorage = { getItem: () => null }
afterEach(() => { globalThis.fetch = originalFetch })
function stream(parts) {
  return new Response(new ReadableStream({
    start(controller) {
      parts.forEach((p) => controller.enqueue(new TextEncoder().encode(p)))
      controller.close()
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } })
}
test('handles CRLF split across chunks and multiline JSON', async () => {
  globalThis.fetch = async () => stream(['event: final\r', '\ndata: {\r\ndata: "answer": "Hello"}\r', '\n\r\n'])
  assert.equal((await chatStream('hello')).answer, 'Hello')
})
test('handles final event at end of stream without a trailing delimiter', async () => {
  globalThis.fetch = async () => stream(['event: final\ndata: {"answer":"Hello"}'])
  assert.equal((await chatStream('hello')).answer, 'Hello')
})
test('does not repeat failed requests that may have placed an order', async () => {
  let calls = 0
  globalThis.fetch = async () => { calls++; return new Response('{"detail":"Unavailable"}', { status: 500 }) }
  await assert.rejects(chatStream('buy this'), /Unavailable/)
  assert.equal(calls, 1)
})
test('falls back only when the streaming endpoint is unavailable', async () => {
  let calls = 0
  globalThis.fetch = async () => ++calls === 1 ? new Response('', { status: 404 }) : Response.json({ answer: 'Hello' })
  assert.equal((await chatStream('hello')).answer, 'Hello')
  assert.equal(calls, 2)
})
test('reports error events and truncated replies', async () => {
  globalThis.fetch = async () => stream(['event: error\ndata: {"message":"Try later"}\n\n'])
  await assert.rejects(chatStream('hello'), /Try later/)
  globalThis.fetch = async () => stream(['event: start\ndata: {}\n\n'])
  await assert.rejects(chatStream('hello'), /without an answer/)
})
test('cancels the reader as soon as the final answer arrives', async () => {
  let cancelled = false
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(c) { c.enqueue(new TextEncoder().encode('event: final\ndata: {"answer":"Hello"}\n\n')) },
    cancel() { cancelled = true },
  }))
  assert.equal((await chatStream('hello')).answer, 'Hello')
  assert.equal(cancelled, true)
})
test('preserves intentional cancellation', async () => {
  const controller = new AbortController()
  controller.abort()
  globalThis.fetch = async (_, options) => { options.signal.throwIfAborted() }
  await assert.rejects(chatStream('hello', null, null, { signal: controller.signal }), { name: 'AbortError' })
})
