const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

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
    for (const legacy of [false, true]) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
      const page = await context.newPage(), errors = []
      let syncRunning = true
      const job = { id: 'waiting-copy', status: legacy ? 'running' : 'queued', phase: legacy ? 'reading' : 'waiting',
        source: { provider: 'ytmusic', name: 'Old account', playlist_id: 'PL_source', playlist_name: '' },
        dest: { provider: 'ytmusic', name: 'New account', playlist_id: 'PL_copy', playlist_name: 'My videos' },
        added: 0, processed: 0, total: 0, conflicts: [], error: null,
        created_at: '2026-10-16T12:12:59Z', started_at: legacy ? '2026-10-16T12:12:59Z' : null,
        events: [{ at: '2026-10-16T12:12:59Z', kind: 'queued', status: 'queued', phase: 'waiting', added: 0 }] }
      page.on('pageerror', e => errors.push(e.message))
      await page.route('**/api/**', route => {
        const req = route.request(), url = new URL(req.url())
        const send = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
        if (url.pathname === '/api/transfers/history') return send({ items: [job], total: 1, accounts: [], providers: [] })
        if (url.pathname === '/api/transfers/history/waiting-copy' || url.pathname === '/api/transfers/waiting-copy') return send(job)
        if (url.pathname === '/api/transfers/waiting-copy/stop') { job.status = 'stopped'; return send({ ok: true }) }
        if (url.pathname === '/api/sync/status') return send({ running: syncRunning, running_job: syncRunning ? 'liked' : null, jobs: [], last: null })
        if (['/api/accounts', '/api/transfers', '/api/syncs', '/api/links'].includes(url.pathname)) return send([])
        return send({})
      })
      await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
      await page.goto(`http://127.0.0.1:${server.address().port}/transfers`, { waitUntil: 'networkidle' })
      await page.getByRole('radio', { name: 'Transfer history', exact: true }).click()
      await page.getByRole('button', { name: 'View details', exact: true }).click()
      const dialog = page.getByRole('dialog', { name: 'Transfer details' })
      await dialog.getByText('Waiting for another sync or transfer to finish. This copy will start automatically.', { exact: true }).waitFor({ timeout: 2000 })
      assert.equal(await dialog.getByRole('progressbar').count(), 0)
      assert.equal(await dialog.getByText('Reading source playlist…', { exact: true }).count(), 0)
      assert.equal(await dialog.getByText('PL_source', { exact: true }).count(), 1)
      await page.setViewportSize({ width: 375, height: 900 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true)
      if (legacy) {
        syncRunning = false
        await dialog.getByText('Reading source playlist…', { exact: true }).waitFor({ timeout: 6500 })
        assert.equal(await dialog.getByRole('progressbar').count(), 1)
      } else {
        await dialog.getByRole('button', { name: 'View transfer', exact: true }).click()
        assert.equal(await page.getByRole('button', { name: 'Pause', exact: true }).count(), 0)
        await page.getByRole('button', { name: 'Stop', exact: true }).click()
        await page.getByRole('dialog').getByRole('button', { name: 'Stop', exact: true }).click()
        await page.getByText('Stopped', { exact: true }).first().waitFor()
      }
      assert.deepEqual(errors, [])
      await context.close()
    }
    console.log('PASS: native and older-server queued transfers show a waiting reason, source fallback, correct controls and mobile layout; the live read begins when the sync ends')
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
}
main().catch(e => { console.error(e); process.exitCode = 1 })
