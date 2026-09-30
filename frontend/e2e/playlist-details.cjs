// Focused regression: an owned playlist's details can be edited from the
// playlist inspector. Only the fields the account can change render, a save
// sends only what changed, and the refreshed header shows the result.
// Every API response is local; no music service is contacted.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const CAPABILITIES = { library_read: true, library_write: true, public_playlist_read: true, favorites_write: true }
const ACCOUNTS = [
  { id: 'tidal', provider: 'tidal', provider_name: 'TIDAL', label: '', name: 'TIDAL', state: 'connected', fields: [],
    transferable: true, preserves_order: true, public_playlists: true, editable_details: ['description', 'name', 'public'],
    capabilities: CAPABILITIES },
  { id: 'spotify', provider: 'spotify', provider_name: 'Spotify', label: '', name: 'Spotify', state: 'connected', fields: [],
    transferable: true, preserves_order: true, public_playlists: false, editable_details: ['description', 'name'],
    capabilities: CAPABILITIES },
]
const TRACKS = [{ position: 0, id: 't1', isrc: '', occurrence_id: '', name: 'Song', artist: 'Artist', album: 'Album',
  duration_ms: 200000, image: '', added_at: '', external_url: '' }]

async function main() {
  const dist = path.resolve(__dirname, '../dist')
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname
    const asset = path.resolve(dist, '.' + pathname)
    const file = asset.startsWith(dist + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile()
      ? asset : path.join(dist, 'index.html')
    const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.woff2': 'font/woff2' }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const browser = await chromium.launch({ headless: true })
  const errors = []
  const patches = []
  const playlists = {
    tidal: { id: 'p1', name: 'Road Trip', description: 'Old notes', count: 1, image: '',
      external_url: 'https://listen.tidal.com/playlist/p1', owned: true, public: false },
    spotify: { id: 's1', name: 'Night Drive', description: '', count: 1, image: '', external_url: '', owned: true, public: null },
  }
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
    page.on('pageerror', error => errors.push(`${error.message} @ ${page.url()}`))
    await page.route('**/api/**', async route => {
      const req = route.request()
      const url = new URL(req.url())
      const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (url.pathname === '/api/accounts') return send(ACCOUNTS)
      if (url.pathname === '/api/playlists') {
        const row = playlists[url.searchParams.get('provider')]
        return send(row ? [row] : [])
      }
      const single = url.pathname.match(/^\/api\/playlists\/(tidal|spotify)\/([^/]+)$/)
      if (single) {
        const provider = single[1]
        if (req.method() === 'PATCH') {
          const changes = req.postDataJSON()
          patches.push([provider, changes])
          Object.assign(playlists[provider], changes)
          return send({ ok: true })
        }
        return send({ ...playlists[provider], provider, editable: true, tracks: TRACKS, next_cursor: null, complete: true })
      }
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (['/api/links', '/api/syncs', '/api/transfers'].includes(url.pathname)) return send([])
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))

    await page.goto(base + '/playlists', { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: 'Open Road Trip inside SongMirror' }).first().click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: 'Edit details', exact: true }).click()
    const editor = dialog.getByRole('region', { name: 'Edit details' })
    const save = editor.getByRole('button', { name: 'Save changes', exact: true })
    assert.equal(await save.isDisabled(), true, 'Nothing changed yet')
    assert.equal(await editor.getByLabel('Visibility').inputValue(), 'private')
    await editor.getByLabel('Playlist Name').fill('Road Trip 2026')
    await editor.getByLabel('Visibility').selectOption('public')
    const shots = path.join(__dirname, 'screenshots')
    fs.mkdirSync(shots, { recursive: true })
    await page.screenshot({ path: path.join(shots, 'playlist-details-editor.png'), animations: 'disabled' })
    await save.click()
    await dialog.getByText('Road Trip 2026', { exact: true }).first().waitFor()
    await editor.waitFor({ state: 'detached' })
    assert.deepEqual(patches, [['tidal', { name: 'Road Trip 2026', public: true }]], 'Only changed fields are sent')
    console.log('PASS: editing sends only the changed name and visibility and refreshes the inspector')

    await dialog.getByRole('button', { name: 'Edit details', exact: true }).click()
    await editor.getByLabel('Playlist Name').fill('   ')
    await editor.getByText('Enter a playlist name.').waitFor()
    assert.equal(await editor.getByLabel('Playlist Name').getAttribute('aria-invalid'), 'true')
    assert.equal(await save.isDisabled(), true, 'An empty name cannot be saved')
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Editor must fit ${width}px`)
    }
    await page.setViewportSize({ width: 1280, height: 1000 })
    await editor.getByRole('button', { name: 'Cancel', exact: true }).click()
    await editor.waitFor({ state: 'detached' })
    console.log('PASS: an empty name is refused, and the editor fits desktop and mobile widths')

    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'detached' })
    await page.getByRole('button', { name: 'Open Night Drive inside SongMirror' }).first().click()
    await dialog.getByRole('button', { name: 'Edit details', exact: true }).click()
    assert.equal(await editor.getByLabel('Visibility').count(), 0, 'Spotify offers no visibility edit')
    await editor.getByText('Change its visibility in Spotify.', { exact: true }).waitFor()
    await editor.getByLabel('Description (optional)').fill('For late drives')
    await editor.getByRole('button', { name: 'Save changes', exact: true }).click()
    await editor.waitFor({ state: 'detached' })
    assert.deepEqual(patches[1], ['spotify', { description: 'For late drives' }])
    console.log('PASS: Spotify edits name and description only and points visibility to the app')
    assert.deepEqual(errors, [], 'Browser runtime errors')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
