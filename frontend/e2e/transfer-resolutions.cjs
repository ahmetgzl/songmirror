// Manual choices must be applied to the existing job, with polling restarted
// after its original terminal state. All provider/API traffic is mocked.
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
    const mime = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2' }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
    fs.createReadStream(file).pipe(res)
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({ headless: true })
  const errors = []
  let copies = 0
  let attempts = 0
  let polls = 0
  let holdNextStatus = false
  let releaseStatus
  let markStatusHeld
  const statusHeld = new Promise(resolve => { markStatusHeld = resolve })
  const job = {
    id: 'copy-1', status: 'done', added: 1, total: 4, processed: 4, deferred: 0, unavailable: 0, error: null,
    source: { account: 'spotify', provider: 'spotify', playlist_id: 'p1', playlist_name: 'Source' },
    dest: { account: 'deezer', provider: 'deezer', playlist_id: 'p2', playlist_name: 'Destination' },
    conflicts: [
      { key: 'song|artist', name: 'Song', artist: 'Artist', resolved: false },
      { key: 'other|artist', name: 'Other', artist: 'Artist', resolved: false },
      { key: 'last|artist', name: 'Last', artist: 'Artist', resolved: false },
    ],
  }
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    await page.route('**/api/**', async route => {
      const req = route.request()
      const url = new URL(req.url())
      const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (url.pathname === '/api/accounts') return send(['spotify', 'deezer'].map(provider => ({
        id: provider, provider, label: '', name: provider === 'spotify' ? 'Spotify' : 'Deezer', state: 'connected', fields: [],
        transferable: true, capabilities: { library_read: true, library_write: true, public_playlist_read: true },
      })))
      if (url.pathname === '/api/playlists') {
        const source = url.searchParams.get('provider') === 'spotify'
        return send([{ id: source ? 'p1' : 'p2', name: source ? 'Source' : 'Destination', count: 3, image: '', owned: true }])
      }
      if (url.pathname === '/api/transfers' && req.method() === 'POST') {
        copies++
        return send({ job_id: job.id }, 202)
      }
      if (url.pathname === '/api/transfers/copy-1/resolve') {
        const chosen = req.postDataJSON()
        const index = chosen.key === 'song|artist' ? 0 : 1
        assert.deepEqual(chosen, { key: job.conflicts[index].key, dest_id: index === 0 ? '123' : '456' })
        Object.assign(job.conflicts[index], { resolved: true, dest_id: chosen.dest_id })
        if (index === 1) holdNextStatus = true
        return send({ ok: true })
      }
      if (url.pathname === '/api/transfers/copy-1/apply-resolved') {
        attempts++
        if (attempts === 1) return send({ detail: 'Destination unavailable. Try again.' }, 503)
        job.status = 'running'
        return send({ ok: true }, 202)
      }
      if (url.pathname === '/api/transfers/copy-1') {
        if (holdNextStatus) {
          holdNextStatus = false
          const snapshot = structuredClone(job)
          await new Promise(resolve => {
            releaseStatus = resolve
            markStatusHeld()
          })
          return send(snapshot)
        }
        if (job.status === 'running') {
          polls++
          if (polls === 1) return send({ detail: 'Status temporarily unavailable' }, 503)
          if (polls >= 3) {
            job.status = 'done'
            job.added = 3
            job.conflicts[0].applied = true
            job.conflicts[1].applied = true
          }
        }
        return send(job)
      }
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (['/api/links', '/api/syncs', '/api/transfers'].includes(url.pathname)) return send([])
      return send({})
    })

    await page.goto(`http://127.0.0.1:${server.address().port}/transfers`, { waitUntil: 'networkidle' })
    await page.getByLabel('Service', { exact: true }).first().selectOption('spotify')
    await page.getByLabel('Playlist', { exact: true }).click()
    await page.getByRole('option', { name: 'Source' }).click()
    await page.getByLabel('Service', { exact: true }).nth(1).selectOption('deezer')
    await page.getByLabel('Existing playlist', { exact: true }).click()
    await page.getByRole('option', { name: 'Destination' }).click()
    await page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Copy playlist', exact: true }).click()
    const input = page.getByLabel('Destination track link or id').first()
    await input.waitFor()
    assert.equal(await page.getByRole('button', { name: 'Apply resolved tracks', exact: true }).count(), 0)
    // Let the original terminal-state polling tick elapse before restarting it.
    await page.waitForTimeout(1700)
    await input.fill('123')
    await input.press('Enter')
    const apply = page.getByRole('button', { name: 'Apply resolved tracks', exact: true })
    await apply.waitFor()
    const shots = path.join(__dirname, 'screenshots')
    fs.mkdirSync(shots, { recursive: true })
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      await apply.scrollIntoViewIfNeeded()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Fits ${width}px`)
      await page.screenshot({ path: path.join(shots, `transfer-resolutions-${width}.png`), animations: 'disabled' })
    }
    await apply.focus()
    await page.keyboard.press('Enter')
    await page.getByRole('alert').filter({ hasText: 'Destination unavailable' }).waitFor()
    assert.equal(job.conflicts[0].resolved, true, 'An apply failure keeps the saved choice')
    // Keep a second resolution's old done response in flight while Apply
    // succeeds. The action must queue a fresh read after that stale response.
    const second = page.getByLabel('Destination track link or id').first()
    await second.fill('456')
    await second.press('Enter')
    await statusHeld
    await apply.click()
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Apply resolved tracks' && !button.disabled))
    releaseStatus()
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Apply resolved tracks' && button.disabled))
    await page.getByText('Applied', { exact: true }).first().waitFor({ timeout: 10000 })
    assert.equal(await page.getByText('Applied', { exact: true }).count(), 2)
    assert.equal(await apply.count(), 0, 'No pending matches means no apply action')
    assert.equal(await page.getByLabel('Destination track link or id').count(), 1, 'Unresolved row remains editable')
    assert.equal(copies, 1, 'Applying does not create a new transfer')
    assert.equal(attempts, 2, 'The failed request can be retried')
    assert.ok(polls >= 3, 'Polling resumes after a terminal state even when the first refresh fails')
    assert.deepEqual(errors, [])
    console.log('PASS: save, apply, error/retry, terminal-state polling, keyboard access, and desktop/mobile layout')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
