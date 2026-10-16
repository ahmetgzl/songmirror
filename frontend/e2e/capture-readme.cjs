// Refresh the README gallery from the production UI with public sample data.
// Run after `pnpm -C frontend build`: node frontend/e2e/capture-readme.cjs
// All API calls are mocked; no configured server or provider is contacted.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')
const { chromium } = require('playwright')
const { ACCOUNTS, SHOWCASE_PLAYLISTS, installMocks, initialSyncs, syncStatusFixture } = require('./verify.cjs')

const output = path.resolve(__dirname, '../../.github/assets')
const accounts = ACCOUNTS.map(account => ({ ...account, state: 'connected', detail: null }))
const oldAccount = { ...accounts.find(a => a.id === 'ytmusic'), name: 'YouTube Music · Old account', label: 'Old account', public_playlists: true }
const newAccount = { ...oldAccount, id: 'new-youtube', name: 'YouTube Music · New account', label: 'New account', is_default: false, removable: true }
const playlists = Object.fromEntries(Object.entries(SHOWCASE_PLAYLISTS).map(([provider, rows]) => [provider,
  (provider === 'ytmusic' ? rows.slice(0, 4) : rows).map((row, i) => ({ ...row, owned: true, ...(provider === 'ytmusic' ? { library_kind: i < 3 ? 'music' : 'video' } : {}) })),
]))
playlists['new-youtube'] = playlists.ytmusic.map(p => ({ ...p, id: `new_${p.id}` }))

function record(id, title, status, added, hours) {
  const created_at = `2026-10-16T${hours}:00:00Z`
  return { id, status, phase: 'copying',
    source: { account: oldAccount.id, provider: 'ytmusic', name: oldAccount.name, playlist_id: `source_${id}`, playlist_name: title },
    dest: { account: newAccount.id, provider: 'ytmusic', name: newAccount.name, playlist_id: `dest_${id}`, playlist_name: title },
    added, processed: 118, total: 118, deferred: 0, unavailable: 0, conflicts: [],
    error: status === 'error' ? 'Connection interrupted. Retry into the existing playlist to copy the remaining videos.' : null,
    created_at, started_at: created_at, finished_at: `2026-10-16T${hours}:03:00Z` }
}
const history = [record('road-trip', 'Road Trip 2025', 'done', 118, '12'),
  record('night-drive', 'Night Drive', 'error', 42, '11'), record('morning-focus', 'Morning Focus', 'stopped', 18, '10')]

async function main() {
  const dist = path.resolve(__dirname, '../dist')
  assert.ok(fs.existsSync(path.join(dist, 'index.html')), 'Build the frontend before capturing screenshots')
  const server = http.createServer((req, res) => {
    const asset = path.resolve(dist, '.' + new URL(req.url, 'http://localhost').pathname)
    const file = asset.startsWith(dist + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile() ? asset : path.join(dist, 'index.html')
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const origin = `http://127.0.0.1:${server.address().port}`
  const browser = await chromium.launch({ headless: true })
  const errors = []
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 960 }, locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' })
    await context.addInitScript(() => {
      localStorage.setItem('songmirror-theme', 'dark')
      localStorage.setItem('songmirror-language', 'en')
      // Keep the guide launcher visible without adding repeated instructions
      // above every feature in the gallery.
      for (const page of ['dashboard', 'accounts', 'playlists', 'sync', 'transfers']) localStorage.setItem(`songmirror-guide-v1:${page}`, 'hidden')
    })
    const page = await context.newPage()
    await page.clock.setFixedTime(new Date('2026-10-16T12:00:00Z'))
    page.on('pageerror', error => errors.push(error.message))
    await installMocks(page, { accounts, playlists })
    await page.route('**/*', route => {
      const url = new URL(route.request().url())
      if (url.href === 'https://m.media-amazon.com/images/G/01/music/logo/1.0/smile_256x256.png') {
        return route.fulfill({ contentType: 'image/png', body: fs.readFileSync(path.join(__dirname, 'fixtures/amazon-music.png')) })
      }
      return url.origin === origin ? route.fallback() : route.abort()
    })
    // Use a quiet, successful pass for the dashboard, with real UI contracts.
    await page.route('**/api/sync/status', route => {
      const status = syncStatusFixture(initialSyncs())
      status.next_run_at = Date.parse('2026-10-16T13:00:00Z') / 1000
      status.jobs.forEach(job => { if (job.next_run_at) job.next_run_at = status.next_run_at })
      status.last.per_target = accounts.filter(a => a.transferable).map(a => ({ name: a.provider_name, added: 4, removed: 0, missing: 0, held: 0, deferred: 0, created: 0, skipped: 0 }))
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(status) })
    })
    await page.route('**/api/transfers/history*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: history, total: history.length,
      accounts: [oldAccount, newAccount].map(a => ({ id: a.id, name: a.name })), providers: ['ytmusic'] }) }))

    async function shot(name) {
      await page.evaluate(() => document.fonts.ready)
      await page.waitForFunction(() => [...document.images].every(img => img.complete))
      assert.equal(await page.locator('img').evaluateAll(images => images.every(img => img.naturalWidth > 0)), true, `${name}: broken image`)
      await page.evaluate(() => document.activeElement?.blur())
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${name}: horizontal overflow`)
      assert.deepEqual(errors, [], `${name}: browser errors`)
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true, animations: 'disabled' })
      console.log(`Captured ${name}.png`)
    }

    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Recent activity', exact: true }).waitFor()
    await shot('dashboard')

    await page.goto(origin + '/accounts', { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Accounts', exact: true }).waitFor()
    assert.equal(await page.getByText('Connected', { exact: true }).count(), accounts.length)
    await shot('accounts')

    // A smaller library makes the grid, Music/video grouping and Jellyfin
    // browsing readable at README width, using the same real components.
    await page.route('**/api/accounts', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(accounts.filter(a => ['spotify', 'ytmusic', 'jellyfin'].includes(a.id))) }))
    await page.route('**/api/links', route => route.fulfill({ contentType: 'application/json', body: '[]' }))
    await page.goto(origin + '/playlists', { waitUntil: 'networkidle' })
    await page.getByText('Night Drive', { exact: true }).first().waitFor()
    assert.equal(await page.getByRole('radio', { name: 'Grid', exact: true }).isChecked(), true)
    await shot('playlists')

    await page.unroute('**/api/accounts')
    await page.goto(origin + '/sync', { waitUntil: 'networkidle' })
    const card = page.locator('h3', { hasText: 'Default' }).locator('xpath=ancestor::div[contains(@class,"rounded-card")][1]')
    await card.getByRole('button', { name: 'Edit', exact: true }).click()
    await page.getByRole('radio', { name: 'Services', exact: true }).click()
    for (const name of ['TIDAL', 'Apple Music', 'YouTube Music', 'Qobuz', 'Deezer', 'Amazon Music']) {
      const chip = page.getByRole('button', { name, exact: true })
      if (await chip.getAttribute('aria-pressed') !== 'true') await chip.click()
    }
    await shot('sync-wizard')
    await page.keyboard.press('Escape')

    await page.route('**/api/accounts', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify(accounts.map(a => a.id === 'ytmusic' ? oldAccount : a).concat(newAccount)) }))
    await page.goto(origin + '/transfers', { waitUntil: 'networkidle' })
    await page.getByLabel('Service', { exact: true }).nth(0).selectOption(oldAccount.id)
    await page.getByRole('button', { name: 'Playlist', exact: true }).click()
    await page.getByRole('option', { name: 'Road Trip 2025', exact: false }).click()
    await page.getByLabel('Service', { exact: true }).nth(1).selectOption(newAccount.id)
    await page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await page.getByLabel('New playlist name', { exact: false }).fill('Road Trip 2025 — new account')
    await shot('transfers')

    await page.getByRole('radio', { name: 'Transfer history', exact: true }).click()
    await page.getByText('118 added', { exact: false }).waitFor()
    await shot('transfer-history')
    await context.close()
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
