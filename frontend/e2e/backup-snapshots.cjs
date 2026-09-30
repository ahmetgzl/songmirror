// Focused regression: a running backup shows its phase and progress, a failed
// run shows its provider-level cause and where it stopped, and single snapshots
// can be listed, downloaded, and deleted. Every API response is local.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

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
  const browser = await chromium.launch({ headless: true })
  const errors = []
  const requests = []
  const job = {
    account_id: 'spotify', provider: 'spotify', provider_name: 'Spotify', account_name: 'Spotify',
    enabled: true, interval: '24h', format: 'json', retention: 5, storage_dir: '',
    default_storage_dir: '/data/playlist_backups', storage_path: '/data/playlist_backups/spotify',
    running: true, progress: { phase: 'waiting' }, next_run_at: null, snapshot_count: 2,
    last_success: null, last_failure: null,
  }
  let snapshots = [
    { filename: 'songmirror-spotify-all-playlists-20260929T001009Z.json', format: 'json', size: 7355754, created_at: '2026-09-29T00:10:09Z' },
    { filename: 'songmirror-spotify-all-playlists-20260928T000710Z.json', format: 'json', size: 7356019, created_at: '2026-09-28T00:07:10Z' },
  ]
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const req = route.request()
      const url = new URL(req.url())
      const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      requests.push(`${req.method()} ${url.pathname}`)
      if (url.pathname === '/api/accounts') return send([{ id: 'spotify', provider: 'spotify', label: '', name: 'Spotify', state: 'connected', fields: [], transferable: true }])
      if (url.pathname === '/api/settings') return send({ DISPLAY_NAME: '', DOWNLOAD_DIR: '/music', LOCAL_MIRROR_FORMAT: '' })
      if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
      if (url.pathname === '/api/playlist-backups') return send([job])
      if (url.pathname === '/api/playlist-backups/spotify/snapshots') return send(snapshots)
      const single = url.pathname.match(/^\/api\/playlist-backups\/spotify\/snapshots\/([^/]+)$/)
      if (single) {
        const name = decodeURIComponent(single[1])
        if (req.method() === 'DELETE') {
          snapshots = snapshots.filter(row => row.filename !== name)
          job.snapshot_count = snapshots.length
          return send({ ok: true })
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'Content-Disposition': `attachment; filename="${name}"` },
          body: '{"kind":"songmirror-playlist-backup"}',
        })
      }
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    await page.goto(`http://127.0.0.1:${server.address().port}/settings?section=backups`)

    await page.getByText('Waiting for a sync or transfer to finish.', { exact: false }).waitFor()
    job.progress = { phase: 'reading', done: 11, total: 69, tracks: 2104, playlist: 'Road Trip' }
    await page.reload()
    const bar = page.getByRole('progressbar', { name: 'Backup progress' })
    await page.getByText('Reading playlist 12 of 69: Road Trip', { exact: true }).waitFor()
    assert.equal(await bar.getAttribute('aria-valuenow'), '16')
    await page.getByText('Tracks read so far: 2,104', { exact: true }).waitFor()
    job.progress = { phase: 'saving', done: 69, total: 69, tracks: 12425, playlist: null }
    await page.reload()
    await page.getByText('Saving the snapshot file…', { exact: true }).waitFor()
    assert.equal(await bar.getAttribute('aria-valuenow'), '100')
    console.log('PASS: a running backup shows waiting, per-playlist reading progress, and saving')

    Object.assign(job, {
      running: false,
      progress: null,
      last_failure: {
        at: '2026-09-30T00:07:53Z',
        error: 'Spotify could not export playlists right now. Retry; if it continues, reconnect the account.',
        detail: 'HTTPError: 429 Client Error: Too Many Requests for url: https://api-partner.spotify.com/pathfinder/v2/query',
        progress: { phase: 'reading', done: 23, total: 69, tracks: 4210, playlist: 'Road Trip' },
      },
    })
    await page.reload()
    await page.getByText('Reason: HTTPError: 429 Client Error: Too Many Requests', { exact: false }).waitFor()
    await page.getByText('It stopped at playlist 24 of 69: Road Trip', { exact: true }).waitFor()
    console.log('PASS: a failed backup shows its provider-level cause and where it stopped')

    await page.getByRole('button', { name: 'Show snapshots', exact: true }).click()
    const list = page.getByRole('list', { name: 'Stored snapshots' })
    await list.getByText(snapshots[1].filename, { exact: false }).waitFor()
    assert.equal(await list.getByRole('listitem').count(), 2)
    await list.getByText('7.4 MB', { exact: false }).first().waitFor()
    const download = page.waitForEvent('download')
    await list.getByRole('listitem').first().getByRole('button', { name: /^Download the snapshot/ }).click()
    assert.equal((await download).suggestedFilename(), snapshots[0].filename)
    const doomed = snapshots[1].filename
    await list.getByRole('listitem').nth(1).getByRole('button', { name: /^Delete the snapshot/ }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText(doomed, { exact: false }).waitFor()
    await dialog.getByRole('button', { name: 'Delete snapshot', exact: true }).click()
    await page.getByText('1 stored snapshot.', { exact: true }).waitFor()
    await page.waitForFunction(() => document.querySelectorAll('[aria-label="Stored snapshots"] li').length === 1)
    assert.ok(requests.includes(`DELETE /api/playlist-backups/spotify/snapshots/${doomed}`))
    console.log('PASS: snapshots list, download one by name, and delete one after confirmation')

    const shots = path.join(__dirname, 'screenshots')
    fs.mkdirSync(shots, { recursive: true })
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Backups must fit ${width}px`)
      await page.screenshot({ path: path.join(shots, `backup-snapshots-${width}.png`), fullPage: true, animations: 'disabled' })
    }
    console.log('PASS: the backup card with its snapshot list fits desktop and mobile widths')
    assert.deepEqual(errors, [], 'Browser runtime errors')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
