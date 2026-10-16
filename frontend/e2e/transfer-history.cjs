const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const endpoint = (account, playlist_name) => ({ account, provider: 'ytmusic', name: account,
  playlist_id: 'PL_example', playlist_name })
const failed = { id: 'failed-copy', status: 'error', source: endpoint('Old email', 'My videos'),
  dest: endpoint('New email', 'Copied videos'), added: 12, processed: 795, total: 795, deferred: 0,
  unavailable: 4, conflicts: [], error: 'Provider unavailable after 12 confirmed writes',
  created_at: '2026-10-16T10:00:00Z', started_at: '2026-10-16T10:00:01Z', finished_at: '2026-10-16T10:01:00Z' }

async function main() {
  const dist = path.resolve(__dirname, '../dist')
  const server = http.createServer((req, res) => {
    const asset = path.resolve(dist, '.' + new URL(req.url, 'http://localhost').pathname)
    const file = asset.startsWith(dist + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile() ? asset : path.join(dist, 'index.html')
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, timezoneId: 'America/New_York' })
    const page = await context.newPage()
    const queries = [], mutations = [], errors = []
    let unavailable = false
    page.on('pageerror', e => errors.push(e.message))
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url())
      if (request.method() !== 'GET') mutations.push(url.pathname)
      const send = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) })
      if (url.pathname === '/api/transfers/history') {
        queries.push(Object.fromEntries(url.searchParams))
        if (unavailable) return send({ detail: 'History store unavailable' }, 503)
        const empty = url.searchParams.get('search') === 'missing'
        return send({ items: empty ? [] : [failed], total: empty ? 0 : 21,
          accounts: [{ id: 'old', name: 'Old email' }, { id: 'new', name: 'New email' }], providers: ['ytmusic'] })
      }
      if (url.pathname === '/api/transfers/history/failed-copy') return send({ ...failed, events: [
        { at: failed.created_at, kind: 'queued', status: 'queued', added: 0 },
        { at: failed.started_at, kind: 'running', status: 'running', phase: 'copying', added: 0 },
        { at: failed.finished_at, kind: 'error', status: 'error', added: 12, error: failed.error },
      ] })
      if (url.pathname === '/api/transfers/failed-copy') return send({ ...failed, historical: true })
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (['/api/accounts', '/api/transfers', '/api/syncs', '/api/links', '/api/playlists'].includes(url.pathname)) return send([])
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    await page.goto(`http://127.0.0.1:${server.address().port}/transfers`, { waitUntil: 'networkidle' })
    await page.getByRole('radio', { name: 'Transfer history', exact: true }).click()
    const history = page.getByRole('region', { name: 'Transfer history', exact: true })
    await history.getByText('My videos', { exact: true }).waitFor()
    assert.match(await history.innerText(), /12 added/)
    await history.getByRole('button', { name: 'Next', exact: true }).click()
    await page.waitForFunction(() => document.body.innerText.includes('Showing 21–21 of 21'))
    assert.equal(queries.at(-1).offset, '20')
    await history.getByLabel('Search playlists', { exact: true }).fill('My videos')
    await history.getByLabel('Status', { exact: true }).selectOption('error')
    await history.getByLabel('Account', { exact: true }).selectOption('new')
    await history.getByLabel('Service', { exact: true }).selectOption('ytmusic')
    await history.getByLabel('From date', { exact: true }).fill('2026-10-15')
    await history.getByLabel('Through date', { exact: true }).fill('2026-10-16')
    await history.getByRole('button', { name: 'Apply filters', exact: true }).click()
    await page.waitForFunction(() => document.body.innerText.includes('Showing 1–20 of 21'))
    assert.deepEqual(queries.at(-1), { limit: '20', offset: '0', search: 'My videos', status: 'error', account: 'new', provider: 'ytmusic',
      after: '2026-10-15T04:00:00.000Z', before: '2026-10-17T04:00:00.000Z' })
    await history.getByRole('button', { name: 'View details', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Transfer details' })
    await dialog.getByRole('heading', { name: 'Audit trail', exact: true }).waitFor()
    assert.equal(await dialog.getByRole('listitem').count(), 3)
    assert.match(await dialog.innerText(), /Provider unavailable after 12 confirmed writes/)
    assert.match(await dialog.innerText(), /failed-copy/)
    for (const width of [1280, 768, 375]) {
      await page.setViewportSize({ width, height: 850 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `History must fit ${width}px`)
      assert.equal(await dialog.evaluate(el => el.getBoundingClientRect().right <= innerWidth), true)
    }
    await page.screenshot({ path: path.join(dist, 'transfer-history-mobile.png') })
    await dialog.getByRole('button', { name: 'View transfer', exact: true }).click()
    await page.getByText('Provider unavailable after 12 confirmed writes', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: /^(Pause|Resume|Stop|Apply saved matches)$/ }).count(), 0)
    await page.getByRole('radio', { name: 'Transfer history', exact: true }).click()
    await history.getByLabel('Search playlists', { exact: true }).fill('missing')
    await history.getByRole('button', { name: 'Apply filters', exact: true }).click()
    await history.getByText('No transfers match these filters', { exact: true }).waitFor()
    await history.getByRole('button', { name: 'Clear', exact: true }).click()
    await history.getByText('My videos', { exact: true }).waitFor()
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.screenshot({ path: path.join(dist, 'transfer-history-desktop.png') })
    unavailable = true
    await history.getByRole('button', { name: 'Refresh', exact: true }).click()
    await history.getByRole('alert').waitFor()
    assert.match(await history.getByRole('alert').innerText(), /History store unavailable/)
    assert.deepEqual(mutations, [])
    assert.deepEqual(errors, [])
    await context.close()
    console.log('PASS: persistent transfer history UI filters, local inclusive dates, pagination, failed audit details, read-only records, empty/error states and mobile layout')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
