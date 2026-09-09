import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { getCart, addToCart, cartCount, setCartQty, getWishlist } from '../src/store.js'
const data = new Map()
globalThis.localStorage = { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) }
globalThis.window = new EventTarget()
beforeEach(() => data.clear())
test('malformed saved data does not crash cart or wishlist', () => {
  data.set('cart', '{}'); data.set('wishlist', 'null')
  assert.deepEqual(getCart(), []); assert.deepEqual(getWishlist(), [])
  data.set('cart', JSON.stringify([null, { id: 'a', title: 'A', price: 10, qty: -1 }, { id: 'b', title: 'B', price: 20, qty: 2 }]))
  assert.equal(cartCount(), 2)
})
test('cart quantities are integers with a bounded maximum', () => {
  addToCart('a', 'A', 100, 2)
  addToCart('a', 'A', 100, '3')
  assert.equal(cartCount(), 5)
  setCartQty('a', 'invalid'); setCartQty('a', -2); setCartQty('a', 1.5)
  assert.equal(cartCount(), 5)
  addToCart('a', 'A', 100, 100)
  assert.equal(cartCount(), 100)
  setCartQty('a', 0)
  assert.deepEqual(getCart(), [])
})
