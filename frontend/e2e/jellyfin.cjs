// Jellyfin browse failures must be visible, retryable, and preserve saved lists.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ACCOUNT = {
  id: 'profile_default_jellyfin', provider: 'jellyfin', provider_name: 'Jellyfin',
  label: 'Jellyfin', name: 'Jellyfin', state: 'connected', fields: [],
  transferable: false, source_capable: false, preserves_order: false,
  capabilities: { library_read: true, library_write: false, public_playlist_read: false },
}
const PLAYLISTS = [
  { id: 'mix', name: 'Jellyfin mix', count: 7, image: '', owned: true, external_url: 'http://jellyfin.test/web/#/details?id=mix' },
  { id: 'chill', name: 'Quiet evenings', count: null, image: '', owned: true, external_url: 'http://jellyfin.test/web/#/details?id=chill' },
]
const ACCOUNT_IDS = ['profile_default_jellyfin', 'jellyfin']
const TRACKS = Array.from({ length: 7 }, (_, position) => ({
  position, id: `track-${position}`, name: `Jellyfin track ${position + 1}`, artist: 'Local artist',
  album: 'Local album', image: '', duration_ms: 120000, external_url: '', occurrence_id: '', isrc: '', added_at: '',
}))

async function main() {
  const dist = path.resolve(__dirname, '../dist')
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname
    const asset = path.resolve(dist, '.' + pathname)
    const file = asset.startsWith(dist + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile()
      ? asset : path.join(dist, 'index.html')
    const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2' }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
  const page = await context.newPage()
  const errors = [], writes = []
  let fail = true, playlists = PLAYLISTS, reads = 0
  const detailReads = []
  try {
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url())
      const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (req.method() !== 'GET') writes.push(req.url())
      if (url.pathname === '/api/accounts') return send([ACCOUNT])
      if (url.pathname === '/api/playlists') {
        assert.ok(ACCOUNT_IDS.includes(url.searchParams.get('provider')), 'Both aliases resolve to the same Jellyfin account')
        reads++
        return fail
          ? send({ detail: 'Jellyfin rejected access. Check the API key and user permissions in Accounts.' }, 502)
          : send(playlists)
      }
      const detail = url.pathname.match(/^\/api\/playlists\/([^/]+)\/mix$/)
      if (detail && ACCOUNT_IDS.includes(detail[1])) {
        assert.equal(url.searchParams.get('page_size'), '20', 'Jellyfin uses progressive inspection for every account identity')
        const offset = Number(url.searchParams.get('offset') || 0)
        detailReads.push({ account: detail[1], offset, refresh: url.searchParams.get('refresh') })
        return send({ ...PLAYLISTS[0], provider: detail[1], editable: false, description: '',
          tracks: offset === 0 ? TRACKS.slice(0, 3) : TRACKS.slice(3),
          next_cursor: offset === 0 ? '3' : null, complete: offset !== 0 })
      }
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (['/api/syncs', '/api/links', '/api/transfers'].includes(url.pathname)) return send([])
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    const visit = () => page.goto(`http://127.0.0.1:${server.address().port}/playlists`, { waitUntil: 'networkidle' })
    await visit()
    await page.getByText(/Could not load playlists: Jellyfin rejected access/).waitFor()
    assert.equal(await page.getByText('No playlists found', { exact: true }).count(), 0)
    fail = false
    await page.getByRole('button', { name: 'Retry', exact: true }).click()
    await page.getByText('Jellyfin mix', { exact: true }).waitFor()
    await page.getByText('Quiet evenings', { exact: true }).waitFor()
    assert.equal(await page.getByText(/Could not load playlists:/).count(), 0)
    assert.equal(await page.getByRole('link', { name: 'Open Jellyfin mix in Jellyfin', exact: true }).getAttribute('href'), PLAYLISTS[0].external_url)
    assert.equal(reads, 2)
    for (const width of [320, 768, 1024, 1280, 1536]) {
      await page.setViewportSize({ width, height: 1000 })
      const name = await page.getByText('Jellyfin mix', { exact: true }).boundingBox()
      assert.ok(name && name.width >= 64, `Playlist name must retain readable width at ${width}px`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    }
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.screenshot({ path: path.join(__dirname, 'screenshots/jellyfin-browse.png'), fullPage: true })
    console.log('PASS: Jellyfin error is visible and Retry restores playlists with known and unknown counts')

    for (const identity of ACCOUNT_IDS) {
      ACCOUNT.id = identity
      if (identity === 'jellyfin') await visit()
      await page.getByRole('button', { name: 'Open Jellyfin mix inside SongMirror', exact: true }).click()
      const dialog = page.getByRole('dialog')
      await dialog.getByText('Jellyfin track 7', { exact: true }).waitFor()
      await dialog.getByText('This playlist is read-only on Jellyfin. You can inspect it here or open it on the service.', { exact: true }).waitFor()
      assert.equal(await dialog.getByText("Playlist couldn't be opened", { exact: true }).count(), 0)
      assert.equal(await dialog.getByRole('button', { name: /Remove|Edit details|Export/ }).count(), 0)
      assert.equal(await dialog.getByText('Portable snapshot', { exact: true }).count(), 0)
      await dialog.getByRole('button', { name: 'Refresh', exact: true }).click()
      await dialog.getByText('Jellyfin track 7', { exact: true }).waitFor()
      assert.ok(detailReads.some(read => read.account === identity && read.offset === 3), 'Remaining tracks load after the first page')
      await page.keyboard.press('Escape')
      await dialog.waitFor({ state: 'detached' })
    }
    await page.screenshot({ path: path.join(__dirname, 'screenshots/jellyfin-inspection.png') })
    console.log('PASS: profile and legacy Jellyfin accounts open and refresh ordered track pages with read-only controls')

    fail = true
    await visit()
    await page.getByText(/Showing the saved list. Refresh failed: Jellyfin rejected access/).waitFor()
    await page.getByText('Jellyfin mix', { exact: true }).waitFor()
    assert.equal(await page.getByText('No playlists found', { exact: true }).count(), 0)
    console.log('PASS: failed Jellyfin refresh keeps the previously saved playlists')

    // Clear persisted snapshots for a successful but truly empty server.
    fail = false
    playlists = []
    await page.evaluate(() => localStorage.clear())
    await visit()
    await page.getByText('No playlists found', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).count(), 0)
    await page.setViewportSize({ width: 320, height: 568 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    assert.deepEqual(errors, [])
    assert.deepEqual(writes, [])
    console.log('PASS: an empty Jellyfin library stays distinct from a connection failure; mobile has no overflow')
  } catch (error) {
    await page.screenshot({ path: path.join(__dirname, 'screenshots/jellyfin-error.png'), fullPage: true })
    throw error
  } finally {
    await context.close()
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
