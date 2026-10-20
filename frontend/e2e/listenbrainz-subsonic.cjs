const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

async function main() {
  const dist = path.resolve(__dirname, '../dist')
  const server = http.createServer((req, res) => {
    const asset = path.resolve(dist, '.' + new URL(req.url, 'http://localhost').pathname)
    const file = asset.startsWith(dist + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile()
      ? asset : path.join(dist, 'index.html')
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json' }[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const browser = await chromium.launch({ headless: true })
  const errors = [], connections = [], saved = []
  const accounts = [
    { id: 'loves', provider: 'listenbrainz', name: 'ListenBrainz', provider_name: 'ListenBrainz',
      transferable: false, source_capable: true, supports_playlists: false,
      fields: [{ key: 'LISTENBRAINZ_USER', label: 'Username', required: true, secret: false }],
      capabilities: { library_read: true, library_write: false, favorites_write: false, public_playlist_read: false } },
    { id: 'local', provider: 'subsonic', name: 'Navidrome / Subsonic', provider_name: 'Navidrome / Subsonic',
      transferable: true, source_capable: true, supports_playlists: true,
      fields: [{ key: 'SUBSONIC_URL', label: 'Server URL' }, { key: 'SUBSONIC_USER', label: 'Username' },
        { key: 'SUBSONIC_PASSWORD', label: 'Password', secret: true }].map(field => ({ required: true, ...field })),
      capabilities: { library_read: true, library_write: true, favorites_write: true, public_playlist_read: true } },
  ].map(account => ({ auth_kind: 'api_key', state: 'unconfigured', label: account.name, detail: '',
    is_default: true, removable: false, preserves_order: false, ...account,
    fields: account.fields.map(field => ({ value: '', configured: false, ...field })) }))

  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
  page.setDefaultTimeout(10_000)
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', route => {
    const request = route.request(), url = new URL(request.url())
    const send = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (url.pathname === '/api/accounts') return send(accounts)
    if (/\/api\/accounts\/[^/]+\/connect$/.test(url.pathname)) {
      const account = accounts.find(account => url.pathname.includes(`/${account.id}/`))
      const values = request.postDataJSON()
      connections.push({ provider: account.provider, values })
      account.state = 'connected'
      return send({ kind: 'api_key', state: 'connected', detail: account.name })
    }
    if (url.pathname === '/api/syncs') {
      if (request.method() === 'POST') {
        const job = { id: 'job', ...request.postDataJSON() }
        saved.push(job)
        return send(job)
      }
      return send(saved)
    }
    if (url.pathname === '/api/sync/status') return send({ jobs: [], running: false })
    if (url.pathname.startsWith('/api/playlists')) return send([])
    if (['/api/links', '/api/playlist-backups'].includes(url.pathname)) return send([])
    return send({})
  })
  await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))

  try {
    await page.goto(base + '/accounts')
    const lovesCard = page.locator('[data-tour="account-card"]').filter({ has: page.getByRole('heading', { name: 'ListenBrainz', exact: true }) })
    const localCard = page.locator('[data-tour="account-card"]').filter({ has: page.getByRole('heading', { name: 'Navidrome / Subsonic', exact: true }) })
    await lovesCard.getByRole('button', { name: 'Connect', exact: true }).click()
    let dialog = page.getByRole('dialog')
    assert.equal(await dialog.getByLabel('Password', { exact: false }).count(), 0)
    await dialog.getByLabel('Username', { exact: false }).fill('listener')
    await dialog.getByRole('button', { name: 'Connect', exact: true }).click()
    await dialog.getByRole('status').filter({ hasText: 'ListenBrainz is connected.' }).waitFor()
    await dialog.waitFor({ state: 'hidden' })

    await localCard.getByRole('button', { name: 'Connect', exact: true }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Server URL', { exact: false }).fill('https://music.example/navidrome')
    await dialog.getByLabel('Username', { exact: false }).fill('listener')
    const password = dialog.getByLabel('Password', { exact: false })
    assert.equal(await password.getAttribute('type'), 'password')
    await password.fill('secret')
    await dialog.getByRole('button', { name: 'Connect', exact: true }).click()
    await dialog.getByRole('status').filter({ hasText: 'Navidrome / Subsonic is connected.' }).waitFor()
    await dialog.waitFor({ state: 'hidden' })
    assert.deepEqual(connections, [
      { provider: 'listenbrainz', values: { LISTENBRAINZ_USER: 'listener' } },
      { provider: 'subsonic', values: { SUBSONIC_URL: 'https://music.example/navidrome', SUBSONIC_USER: 'listener', SUBSONIC_PASSWORD: 'secret' } },
    ])
    console.log('PASS: username-only ListenBrainz setup and masked Subsonic password setup')

    await page.goto(base + '/sync')
    await page.getByRole('button', { name: 'New sync', exact: true }).first().click()
    dialog = page.getByRole('dialog')
    await dialog.getByLabel('Name', { exact: false }).fill('Independent favorites')
    await dialog.getByRole('radio', { name: 'ListenBrainz', exact: true }).click()
    await dialog.getByRole('radio', { name: 'Playlists', exact: true }).click()
    assert.equal(await dialog.getByRole('checkbox', { name: 'ListenBrainz Loved Recordings', exact: true }).isChecked(), true)
    assert.equal(await dialog.getByText('No playlists found on ListenBrainz.', { exact: true }).count(), 0)
    assert.equal(await dialog.getByRole('switch', { name: 'Also sync every regular playlist', exact: false }).count(), 0)
    assert.equal(await dialog.getByRole('radio', { name: 'Use Navidrome / Subsonic Starred Songs', exact: true }).isChecked(), true)
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
    }
    const screenshotDir = process.env.SONGMIRROR_E2E_SCREENSHOTS
    if (screenshotDir) {
      fs.mkdirSync(screenshotDir, { recursive: true })
      await page.screenshot({ path: path.join(screenshotDir, 'listenbrainz-sync-mobile.png'), fullPage: true })
    }
    await dialog.getByRole('button', { name: 'Create sync', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' })
    assert.equal(saved.length, 1)
    assert.equal(saved[0].source, 'loves')
    assert.equal(saved[0].sync_playlists, false)
    assert.equal(saved[0].liked_tracks, true)
    assert.deepEqual(saved[0].liked_routes, { local: { kind: 'native' } })
    assert.deepEqual(errors, [])
    console.log('PASS: ListenBrainz source selects loves automatically and saves a native Subsonic route on mobile')
  } finally {
    await page.close()
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
