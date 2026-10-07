// Dependency-free component checks using the existing TypeScript compiler and
// mocked browser/React hooks. Effects are controlled explicitly; no network runs.
const assert = require('node:assert/strict')
const { test } = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const { renderToStaticMarkup } = require('react-dom/server')
const React = require('react')
const jsx = require('react/jsx-runtime')

const feature = (remaining) => ({ limit: 10, used: 10 - remaining, remaining, resets_at: '2099-01-01T00:00:00Z' })
const usage = (stt = 10, tts = 5) => ({ stt: feature(stt), tts: feature(tts) })
const answer = { id: 'answer-1', role: 'assistant', content: 'తెలుగు సమాధానం.' }

function harness() {
  let index = 0
  const slots = [], effects = [], revoked = [], audios = [], requests = []
  const react = {
    useState(initial) {
      const slot = index++
      if (!(slot in slots)) slots[slot] = slot === 0 ? { id: 'user', email: 'user@example.test' }
        : slot === 1 ? false : typeof initial === 'function' ? initial() : initial
      return [slots[slot], (value) => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value }]
    },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot] },
    useCallback(fn) { return fn },
    useEffect(fn) { effects.push(fn) },
  }
  class Audio {
    constructor(url) { this.url = url; this.paused = true; audios.push(this) }
    async play() { if (this.blocked) throw Error('blocked'); this.paused = false }
    pause() { this.paused = true }
    removeAttribute() { this.removed = true }
    load() {}
  }
  const context = {
    exports: {}, Error, TypeError, DOMException, Blob, FormData, AbortController,
    Date, setTimeout, clearTimeout,
    URL: { createObjectURL: () => `blob:audio-${audios.length}`, revokeObjectURL: (url) => revoked.push(url) },
    Audio, crypto: require('node:crypto').webcrypto,
    window: { addEventListener() {}, removeEventListener() {} },
    navigator: {},
    require(name) {
      if (name === 'react') return react
      if (name === 'react/jsx-runtime') return jsx
      if (name === './App.css') return {}
      if (name === '../package.json') return require('../package.json')
      throw Error(`Unexpected import: ${name}`)
    },
    fetch: async (url, options) => {
      requests.push({ url, options })
      if (url === '/voice/usage') return { ok: true, json: async () => usage() }
      if (url === '/speak') return { ok: true, blob: async () => new Blob(['audio'], { type: 'audio/mpeg' }) }
      throw Error(`Unexpected request: ${url}`)
    },
  }
  let source = fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8')
  source = source.replaceAll('import.meta.env.VITE_API_URL', 'undefined').replaceAll('import.meta.env.DEV', 'true')
  // Expose closures in this test copy only; production source remains unchanged.
  source = source.replace('  if (isAuthLoading) {', `  globalThis.testApi = { handleSpeak, stopSpeech, handleLogout, startNewConversation,
    refreshVoiceUsage, setVoiceUsage, setQuestion, setMessages, setActiveDocumentId,
    setDocuments, voiceUserRef, voiceUsage, playingMessageId, speechError };
  if (isAuthLoading) {`)
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  vm.runInNewContext(js, context)
  const render = () => { index = 0; effects.length = 0; return context.exports.default() }
  render()
  context.testApi.voiceUserRef.current = 'user'
  context.testApi.setActiveDocumentId('document')
  context.testApi.setDocuments([{ id: 'document', filename: 'sample.pdf' }])
  context.testApi.setMessages([answer])
  context.testApi.setQuestion('Typed question')
  context.testApi.setVoiceUsage(usage())
  render()
  const html = () => renderToStaticMarkup(render())
  return { context, render, html, effects, revoked, audios, requests, api: () => context.testApi }
}

function button(html, label) {
  return html.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`))?.[0]
}

test('STT mic hidden and TTS disabled at exhaustion; typed Send remains enabled', () => {
  const h = harness()
  h.api().setVoiceUsage(usage(0, 0))
  const html = h.html()
  assert(!button(html, 'Record a question'))
  assert.match(button(html, 'Play voice answer'), /disabled/)
  assert(!button(html, 'Send question').includes('disabled'))
  assert.match(html, /Daily voice-input limit reached/)
  assert.match(html, /AI-generated voice/)
  assert.match(html, /Telugu voice quality requires validation/)
  assert.equal(h.requests.length, 0) // Rendering never requests speech.
})

test('unknown/failed usage fails closed for voice controls while text still works', async () => {
  const h = harness()
  h.context.fetch = async () => { throw new TypeError('offline') }
  await h.api().refreshVoiceUsage()
  const html = h.html()
  assert(!button(html, 'Record a question'))
  assert.match(button(html, 'Play voice answer'), /disabled/)
  assert(!button(html, 'Send question').includes('disabled'))
})

test('explicit click sends unchanged answer; replaced and finished playback releases URLs', async () => {
  const h = harness()
  await h.api().handleSpeak(answer)
  h.render()
  assert.equal(h.audios.length, 1)
  assert.equal(h.requests.filter(r => r.url === '/speak').length, 1)
  assert.equal(JSON.parse(h.requests[0].options.body).text, answer.content)
  assert.equal(h.requests[0].options.credentials, 'include')
  await h.api().handleSpeak({ ...answer, id: 'answer-2' })
  h.render()
  assert(h.audios[0].paused)
  assert.deepEqual(h.revoked, ['blob:audio-0'])
  h.audios[1].onended()
  assert.deepEqual(h.revoked, ['blob:audio-0', 'blob:audio-1'])
})

test('duplicate clicks while generating do not dispatch another request', async () => {
  const h = harness()
  let resolve
  const original = h.context.fetch
  h.context.fetch = (url, options) => url === '/speak' ? new Promise(r => { resolve = r }) : original(url, options)
  const pending = h.api().handleSpeak(answer)
  const second = h.api().handleSpeak(answer)
  await second
  resolve({ ok: true, blob: async () => new Blob(['audio']) })
  await pending
  assert.equal(h.audios.length, 1)
  h.api().stopSpeech()
})

test('blocked playback retries existing audio without consuming a new allowance', async () => {
  const h = harness()
  const original = h.context.Audio.prototype.play
  h.context.Audio.prototype.play = async function () { throw Error('blocked') }
  await h.api().handleSpeak(answer)
  h.render()
  assert.match(h.api().speechError, /Playback was blocked/)
  h.context.Audio.prototype.play = original
  await h.api().handleSpeak(answer)
  h.render()
  assert.equal(h.api().playingMessageId, answer.id)
  assert.equal(h.requests.filter(r => r.url === '/speak').length, 1)
  h.api().stopSpeech()
})

test('unmount releases playback; late generation after navigation creates no audio', async () => {
  const h = harness()
  await h.api().handleSpeak(answer)
  h.render()
  const lifecycle = h.effects.find(fn => fn.toString().includes('releaseSpeech(speechSessionRef.current)'))
  lifecycle()()
  assert(h.audios[0].paused)
  assert.equal(h.revoked.length, 1)
  const next = harness()
  let resolve
  next.context.fetch = () => new Promise(r => { resolve = r })
  const pending = next.api().handleSpeak(answer)
  next.api().startNewConversation()
  resolve({ ok: true, blob: async () => new Blob(['audio']) })
  await pending
  assert.equal(next.audios.length, 0)
})

test('logout releases audio, clears usage, and ignores a late usage response', async () => {
  const h = harness()
  await h.api().handleSpeak(answer)
  h.render()
  let resolveUsage
  h.context.fetch = async (url) => {
    if (url === '/auth/logout') return { ok: true }
    if (url === '/voice/usage') return new Promise(resolve => { resolveUsage = resolve })
    throw Error(url)
  }
  const pending = h.api().refreshVoiceUsage()
  await h.api().handleLogout()
  h.render()
  resolveUsage({ ok: true, json: async () => usage() })
  await pending
  h.render()
  assert.equal(h.api().voiceUsage, null)
  assert.equal(h.api().voiceUserRef.current, null)
  assert(h.audios[0].paused)
  assert.equal(h.revoked.length, 1)
})
