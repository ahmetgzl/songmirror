const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const account = id => ({ id, provider: 'ytmusic', provider_name: 'YouTube Music', label: id,
  name: `YouTube Music · ${id}`, state: 'connected', fields: [], transferable: true,
  capabilities: { library_read: true, library_write: true, public_playlist_read: true } })
const rows = id => [{ id: `${id}-playlist`, name: `${id} saved playlist`, count: 10, image: '', owned: true, library_kind: 'music' }]

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
  let release
  let gate = new Promise(resolve => { release = resolve })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const page = await context.newPage()
    let hold = false, fresh = false, failRefresh = false, empty = false
    const requests = [], errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url())
      const send = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
      if (url.pathname === '/api/accounts') return send([account('old'), account('new')])
      if (url.pathname === '/api/playlists') {
        const id = url.searchParams.get('provider')
        requests.push(id)
        if (hold) await gate
        if (failRefresh) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Library refresh failed' }) })
        return send(empty && id === 'old' ? [] : rows(id).map(row => fresh ? { ...row, name: `${id} fresh playlist` } : row))
      }
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (['/api/transfers', '/api/links', '/api/syncs'].includes(url.pathname)) return send([])
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    await page.goto(`http://127.0.0.1:${server.address().port}/transfers`, { waitUntil: 'networkidle' })
    const source = page.getByLabel('Service', { exact: true }).nth(0)
    const picker = page.getByRole('button', { name: 'Playlist', exact: true })
    await source.selectOption('old')
    await picker.click()
    await page.getByRole('option', { name: /old saved playlist/ }).click()
    assert.match(await picker.innerText(), /old saved playlist\s*10 tracks/)

    hold = true
    await page.reload({ waitUntil: 'domcontentloaded' })
    await source.selectOption('old')
    // A previous successful load must remain usable even when the next request
    // has not returned. Hard reload exercises persisted storage, not only memory.
    assert.doesNotMatch(await picker.innerText(), /Loading/, 'A cached picker must not return to its initial loading placeholder')
    await picker.click()
    await page.getByRole('option', { name: /old saved playlist/ }).waitFor({ timeout: 1500 })
    assert.equal(await page.getByRole('option', { name: /new saved playlist/ }).count(), 0)
    await page.keyboard.press('Escape')
    await source.selectOption('new')
    await picker.click()
    await page.getByRole('option', { name: /new saved playlist/ }).waitFor({ timeout: 1500 })
    assert.equal(await page.getByRole('option', { name: /old saved playlist/ }).count(), 0)
    await page.keyboard.press('Escape')
    assert.deepEqual(requests.sort(), ['new', 'new', 'old', 'old'])
    await source.selectOption('old')
    await picker.click()
    await page.getByRole('option', { name: /old saved playlist/ }).click()
    await page.getByLabel('Service', { exact: true }).nth(1).selectOption('new')
    const destinationPicker = page.getByRole('button', { name: 'Existing playlist', exact: true })
    await destinationPicker.click()
    await page.getByRole('option', { name: /new saved playlist/ }).click()
    assert.equal(await page.getByRole('button', { name: 'Copy playlist', exact: true }).isEnabled(), true)
    await page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await page.getByLabel(/New playlist name/).fill('Keep my custom name')
    await page.getByLabel('Description (optional)', { exact: true }).fill('Keep my description')
    fresh = true
    release()
    await page.getByText('Showing saved playlists while updating…', { exact: true }).waitFor({ state: 'detached' })
    assert.equal(await page.getByLabel(/New playlist name/).inputValue(), 'Keep my custom name')
    assert.equal(await page.getByLabel('Description (optional)', { exact: true }).inputValue(), 'Keep my description')
    await picker.click()
    await page.getByRole('option', { name: /old fresh playlist/ }).waitFor()
    await page.keyboard.press('Escape')
    const countBefore = requests.length
    failRefresh = true
    await page.getByRole('button', { name: 'Refresh Playlist', exact: true }).click()
    await page.getByRole('alert').getByText('Library refresh failed', { exact: true }).waitFor()
    assert.deepEqual(requests.slice(countBefore), ['old'], 'Refresh must request only the selected account')
    await picker.click()
    await page.getByRole('option', { name: /old fresh playlist/ }).waitFor()
    await page.keyboard.press('Escape')
    failRefresh = false
    await page.getByRole('button', { name: 'Refresh Playlist', exact: true }).click()
    await page.getByRole('alert').waitFor({ state: 'detached' })
    const beforeEmpty = requests.length
    empty = true
    await page.getByRole('button', { name: 'Refresh Playlist', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('button[aria-label="Refresh Playlist"]')?.disabled === false)
    assert.equal(requests.length, beforeEmpty + 1)
    assert.equal(await page.getByRole('button', { name: 'Copy playlist', exact: true }).isEnabled(), false,
      'A playlist removed by a successful refresh cannot remain a transfer source')

    gate = new Promise(resolve => { release = resolve })
    await page.reload({ waitUntil: 'domcontentloaded' })
    await source.selectOption('old')
    assert.doesNotMatch(await picker.innerText(), /Loading/, 'A saved empty library is already loaded')
    await picker.click()
    await page.getByText('No playlists found', { exact: true }).waitFor()
    await page.keyboard.press('Escape')
    release()
    await page.getByRole('button', { name: 'Refresh Playlist', exact: true }).waitFor()

    // Invalid snapshots are discarded, and a real first load still shows its
    // loading state instead of borrowing the other account's playlists.
    await page.evaluate(() => localStorage.setItem('songmirror-cache-v2:playlists:old', '{broken'))
    gate = new Promise(resolve => { release = resolve })
    empty = false
    await page.reload({ waitUntil: 'domcontentloaded' })
    await source.selectOption('old')
    assert.match(await picker.innerText(), /Loading/)
    await picker.click()
    assert.equal(await page.getByRole('listbox').getByRole('option').count(), 0)
    await page.keyboard.press('Escape')
    release()
    await page.waitForFunction(() => document.querySelector('button[aria-label="Refresh Playlist"]')?.disabled === false)
    await picker.click()
    await page.getByRole('option', { name: /old fresh playlist/ }).waitFor()
    await page.keyboard.press('Escape')
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Cached picker must fit ${width}px`)
    }
    assert.deepEqual(errors, [])
    await context.close()
    console.log('PASS: cached account libraries are usable during refresh; source/destination isolation, draft edits, targeted retry, fresh removals, saved empty libraries, invalid storage and mobile layout remain correct')
  } finally {
    release()
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
