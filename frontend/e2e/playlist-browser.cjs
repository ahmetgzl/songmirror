// Playlist browsing shows readable connected accounts in a compact responsive grid.
// Sorting changes presentation only; provider order and playlist contents stay intact.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const account = (id, provider, name, extra = {}) => ({
  id, provider, provider_name: name, name, label: '', state: 'connected', fields: [],
  transferable: true, preserves_order: true,
  capabilities: { library_read: true, library_write: true, public_playlist_read: true }, ...extra,
})
const ACCOUNTS = [
  account('spotify', 'spotify', 'Spotify · Main'),
  account('second', 'spotify', 'Spotify · Second account'),
  account('jellyfin', 'jellyfin', 'Jellyfin', { transferable: false, capabilities: { library_read: true, library_write: false, public_playlist_read: false } }),
  account('empty', 'ytmusic', 'Empty library'),
  account('failed', 'qobuz', 'Library with a browse error'),
  account('offline', 'tidal', 'Disconnected account', { state: 'unconfigured' }),
  account('expired', 'deezer', 'Expired account', { state: 'error' }),
  account('catalog', 'apple', 'Catalog-only account', { capabilities: { library_read: false, library_write: false, public_playlist_read: true } }),
]
const PLAYLISTS = [
  { id: 'z', name: 'Zulu', count: 4, image: '', external_url: 'https://example.test/playlist/z', owned: true },
  { id: 'ten', name: 'Mix 10', count: null, image: '', owned: true },
  { id: 'a', name: 'alpha', count: 0, image: '', owned: true },
  { id: 'two', name: 'Mix 2', count: 20, image: '', owned: true },
  { id: 'long', name: 'Very long playlist title '.repeat(8).trim(), count: 150, image: '', owned: true },
]

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
  const errors = [], writes = [], reads = []
  const shots = path.join(__dirname, 'screenshots')
  fs.mkdirSync(shots, { recursive: true })
  try {
    async function setup({ accounts = ACCOUNTS, blockedStorage = false, failAccounts = false } = {}) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
      await context.addInitScript(blocked => {
        localStorage.setItem('songmirror-feature-guides', 'hidden')
        if (blocked) for (const method of ['getItem', 'setItem']) {
          const original = Storage.prototype[method]
          Storage.prototype[method] = function (key, ...args) {
            if (key === 'songmirror-playlist-sort' || key === 'songmirror-playlist-layout') throw new DOMException('Storage blocked', 'SecurityError')
            return original.call(this, key, ...args)
          }
        }
      }, blockedStorage)
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      await page.route('**/api/**', async route => {
        const req = route.request(), url = new URL(req.url())
        const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
        if (req.method() !== 'GET') writes.push([req.method(), url.pathname])
        if (url.pathname === '/api/accounts') return failAccounts ? send({ detail: 'Accounts unavailable' }, 502) : send(accounts)
        if (url.pathname === '/api/playlists') {
          const id = url.searchParams.get('provider')
          reads.push(id)
          if (id === 'failed') return send({ detail: 'Library unavailable' }, 502)
          return send(id === 'empty' ? [] : PLAYLISTS)
        }
        const detail = url.pathname.match(/^\/api\/playlists\/([^/]+)\/([^/]+)$/)
        if (detail) return send({ ...PLAYLISTS.find(row => row.id === detail[2]), provider: detail[1],
          editable: false, tracks: [], next_cursor: null, complete: true })
        if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
        if (['/api/links', '/api/syncs', '/api/transfers'].includes(url.pathname)) return send([])
        return send({})
      })
      await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
      await page.goto(base + '/playlists', { waitUntil: 'networkidle' })
      return { page, context }
    }

    const { page, context } = await setup()
    const main = page.getByRole('region', { name: 'Spotify · Main', exact: true })
    await main.getByRole('button', { name: 'Open Zulu inside SongMirror', exact: true }).waitFor()
    const ids = () => main.locator('ul button').evaluateAll(rows => rows.map(row => row.getAttribute('aria-label')))
    const original = await ids()
    const layoutSwitch = page.getByRole('group', { name: 'Playlist layout', exact: true })
    const grid = layoutSwitch.getByRole('radio', { name: 'Grid', exact: true })
    const rows = layoutSwitch.getByRole('radio', { name: 'Rows', exact: true })
    assert.equal(await grid.isChecked(), true, 'Grid is the default')
    assert.equal(original.length, PLAYLISTS.length)
    for (const active of ACCOUNTS.slice(0, 5)) await page.getByRole('region', { name: active.name, exact: true }).waitFor()
    for (const inactive of ACCOUNTS.slice(5)) assert.equal(await page.getByRole('region', { name: inactive.name, exact: true }).count(), 0)
    assert.deepEqual([...new Set(reads)].sort(), ACCOUNTS.slice(0, 5).map(row => row.id).sort(), 'Only readable connected accounts are fetched')
    await page.getByText('No playlists found', { exact: true }).waitFor()
    await page.getByText('Could not load playlists: Library unavailable', { exact: true }).waitFor()
    console.log('PASS: inactive and catalog-only accounts are hidden; multiple profiles, read-only libraries, empty libraries, and browse errors remain visible')

    const select = page.getByRole('combobox', { name: 'Sort playlists', exact: true })
    await select.selectOption('name-asc')
    assert.deepEqual((await ids()).slice(0, 3), ['Open alpha inside SongMirror', 'Open Mix 2 inside SongMirror', 'Open Mix 10 inside SongMirror'])
    await select.selectOption('name-desc')
    assert.equal((await ids())[0], 'Open Zulu inside SongMirror')
    await page.reload({ waitUntil: 'networkidle' })
    assert.equal(await select.inputValue(), 'name-desc')
    assert.equal((await ids())[0], 'Open Zulu inside SongMirror')
    await select.selectOption('service')
    assert.deepEqual(await ids(), original, 'Sorting must not mutate the provider order')
    await main.getByRole('button', { name: 'Open Mix 2 inside SongMirror', exact: true }).click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('dialog').getByRole('heading', { name: 'Mix 2', exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    assert.equal(await main.getByRole('link', { name: 'Open Zulu in Spotify · Main', exact: true }).getAttribute('href'), PLAYLISTS[0].external_url)
    await main.getByRole('button', { name: 'Export all Spotify · Main playlists', exact: true }).waitFor()
    console.log('PASS: natural name sorting, descending order, saved preference, original order, playlist details, external links, and exports remain usable')

    async function checkToolbar(width) {
      const controls = await layoutSwitch.evaluate(group => {
        const sort = [...document.querySelectorAll('select')].find(element => [...element.labels].some(label => label.textContent === 'Sort playlists'))
        const layoutLabel = document.getElementById(group.getAttribute('aria-labelledby'))
        const geometry = element => {
          const box = element.getBoundingClientRect(), style = getComputedStyle(element)
          return { top: box.top, left: box.left, width: box.width, height: box.height, radius: style.borderRadius, fontSize: style.fontSize }
        }
        return { layout: geometry(group), sort: geometry(sort), layoutLabel: geometry(layoutLabel), sortLabel: geometry(sort.labels[0]),
          choices: [...group.querySelectorAll('label')].map(geometry), text: geometry(group.querySelector('label span')) }
      })
      assert.equal(controls.layout.height, controls.sort.height, `Both controls have equal heights at ${width}px`)
      assert.equal(controls.layout.radius, controls.sort.radius, 'Control corners match')
      assert.equal(controls.text.fontSize, controls.sort.fontSize, 'Control text has consistent sizing')
      if (width >= 640) {
        assert.ok(Math.abs(controls.layout.top - controls.sort.top) <= 1, `Controls align at ${width}px`)
        assert.ok(Math.abs(controls.layoutLabel.top - controls.sortLabel.top) <= 1, 'Labels align above the controls')
      } else {
        assert.equal(controls.layout.height, 44, 'Mobile controls retain a 44px height')
        assert.ok(controls.choices.every(choice => choice.height >= 44), 'Each mobile layout option has a full-height click target')
        assert.ok(Math.abs(controls.layout.width - controls.sort.width) <= 1, 'Stacked mobile fields have equal widths')
        assert.ok(Math.abs(controls.layout.left - controls.sort.left) <= 1, 'Stacked mobile fields share the same left edge')
        assert.ok(controls.sortLabel.top >= controls.layout.top + controls.layout.height, 'Mobile labels do not overlap the preceding control')
      }
    }

    for (const [width, columns] of [[1536, 4], [1280, 3], [1024, 2], [768, 2], [640, 2], [375, 1], [320, 1]]) {
      await page.setViewportSize({ width, height: 900 })
      await checkToolbar(width)
      const layout = await main.evaluate(element => {
        const own = element.getBoundingClientRect(), container = element.parentElement.getBoundingClientRect()
        const title = [...element.querySelectorAll('ul button span')].find(span => span.textContent.startsWith('Very long playlist title'))
        return { width: own.width, container: container.width,
          columns: getComputedStyle(element.parentElement).gridTemplateColumns.split(' ').length,
          fullTitle: title.querySelector('[title]')?.getAttribute('title') ?? title.getAttribute('title') }
      })
      assert.equal(layout.columns, columns, `Compact grid uses ${columns} columns at ${width}px`)
      assert.equal(layout.fullTitle, PLAYLISTS.at(-1).name, 'Long names remain available through the title and accessible button label')
      if (columns === 1) assert.ok(Math.abs(layout.width - layout.container) <= 1)
      else assert.ok(layout.width < layout.container / 1.5, 'Desktop providers share a row')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      if (width === 1536 || width === 375) await page.screenshot({ path: path.join(shots, `playlist-browser-${width}.png`) })
    }
    console.log('PASS: aligned labels, equal control heights, consistent type and corners, full-height mobile targets, and compact grid columns without overflow')

    await page.setViewportSize({ width: 1280, height: 900 })
    await select.selectOption('name-asc')
    const sorted = await ids()
    const readsBeforeLayout = reads.length
    await layoutSwitch.getByText('Rows', { exact: true }).click()
    assert.deepEqual(await ids(), sorted, 'Changing layout retains the current sorting')
    for (const width of [1280, 375, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await checkToolbar(width)
      assert.equal(await main.evaluate(element => Math.abs(element.getBoundingClientRect().width - element.parentElement.getBoundingClientRect().width) < 1), true, 'Rows use the full available width')
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      await page.screenshot({ path: path.join(shots, `playlist-rows-${width}.png`) })
    }
    assert.equal(reads.length, readsBeforeLayout, 'Changing layout does not refetch playlists')
    await page.reload({ waitUntil: 'networkidle' })
    assert.equal(await rows.isChecked(), true, 'Rows preference survives reload')
    assert.equal(await select.inputValue(), 'name-asc', 'Layout preference is independent of sorting')
    await rows.focus()
    await page.keyboard.press('ArrowLeft')
    assert.equal(await grid.isChecked(), true, 'Native radio navigation switches layout with the keyboard')
    await page.reload({ waitUntil: 'networkidle' })
    assert.equal(await grid.isChecked(), true, 'Returning to Grid is also remembered')
    console.log('PASS: layout defaults to Grid; Rows fits desktop/mobile, retains sorting without refetching, persists after reload, and supports keyboard navigation')
    await context.close()

    const blocked = await setup({ blockedStorage: true })
    await blocked.page.getByRole('combobox', { name: 'Sort playlists' }).selectOption('name-asc')
    await blocked.page.getByText('Rows', { exact: true }).click()
    assert.equal(await blocked.page.getByRole('radio', { name: 'Rows', exact: true }).isChecked(), true)
    assert.equal(await blocked.page.getByRole('region', { name: 'Spotify · Main', exact: true }).locator('ul button').first().getAttribute('aria-label'), 'Open alpha inside SongMirror')
    await blocked.context.close()
    const empty = await setup({ accounts: ACCOUNTS.slice(5) })
    await empty.page.getByText('No connected playlist accounts', { exact: true }).waitFor()
    assert.equal(await empty.page.getByRole('combobox', { name: 'Sort playlists' }).count(), 0)
    assert.equal(await empty.page.getByRole('group', { name: 'Playlist layout' }).count(), 0)
    await empty.page.getByRole('main').getByRole('link', { name: 'Accounts', exact: true }).click()
    assert.equal(new URL(empty.page.url()).pathname, '/accounts')
    await empty.context.close()
    const failure = await setup({ failAccounts: true })
    await failure.page.getByText('Could not load accounts: Accounts unavailable', { exact: true }).waitFor()
    assert.equal(await failure.page.getByText('No connected playlist accounts', { exact: true }).count(), 0, 'An account error is not an empty library')
    await failure.context.close()
    assert.deepEqual(writes, [])
    assert.deepEqual(errors, [])
    console.log('PASS: blocked preferences remain usable, no connected accounts lead to Accounts, and account errors stay distinct without API writes')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
