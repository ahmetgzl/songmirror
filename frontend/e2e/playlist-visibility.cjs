// Focused regression: a transfer that creates a playlist can replace the copied
// description and publish it, and Create Playlist can publish too. The public
// switch is offered only where the destination account can honor it.
// Every API response is local; no music service is contacted.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const CAPABILITIES = { library_read: true, library_write: true, public_playlist_read: true, favorites_write: true }
const ACCOUNTS = [
  // Spotify in cookie write mode can only create private playlists.
  { id: 'spotify', provider: 'spotify', provider_name: 'Spotify', label: '', name: 'Spotify', state: 'connected',
    fields: [], transferable: true, preserves_order: true, public_playlists: false, capabilities: CAPABILITIES },
  { id: 'tidal', provider: 'tidal', provider_name: 'TIDAL', label: '', name: 'TIDAL', state: 'connected',
    fields: [], transferable: true, preserves_order: true, public_playlists: true, capabilities: CAPABILITIES },
]
const PLAYLISTS = {
  spotify: [{ id: 'p1', name: 'Road Trip', description: 'Songs for the drive', count: 12, image: '', external_url: '', owned: true }],
  tidal: [],
}

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

  async function makePage(sent) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })
    page.on('pageerror', error => errors.push(`${error.message} @ ${page.url()}\n${error.stack}`))
    await page.route('**/api/**', async route => {
      const req = route.request()
      const url = new URL(req.url())
      const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (url.pathname === '/api/accounts') return send(ACCOUNTS)
      if (url.pathname === '/api/settings') return send({ create_playlist_default_source: 'text' })
      if (url.pathname === '/api/playlists') return send(PLAYLISTS[url.searchParams.get('provider')] ?? [])
      if (url.pathname === '/api/transfers' && req.method() === 'POST') {
        sent.transfer = req.postDataJSON()
        return send({ job_id: 't1' }, 202)
      }
      if (url.pathname === '/api/transfers/t1') {
        return send({
          id: 't1', status: 'queued', preserve_order: false, added: 0, deferred: 0, chronology_replayed: 0,
          unavailable: 0, conflicts: [], error: null, total: 0, processed: 0,
          source: { account: 'spotify', provider: 'spotify', name: 'Spotify', playlist_id: 'p1', playlist_name: 'Road Trip' },
          dest: { account: 'tidal', provider: 'tidal', name: 'TIDAL', playlist_id: null, playlist_name: 'Road Trip' },
        })
      }
      if (url.pathname === '/api/imports/text') {
        sent.import = req.postDataJSON()
        return send({ detail: 'stop here' }, 422)
      }
      if (url.pathname === '/api/transfers' || url.pathname === '/api/links' || url.pathname === '/api/imports') {
        return send(url.pathname === '/api/imports' ? { jobs: [] } : [])
      }
      return send({})
    })
    await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
    return page
  }

  async function chooseCreateTransfer(page, destination) {
    await page.goto(base + '/transfers', { waitUntil: 'networkidle' })
    await page.getByLabel('Service', { exact: true }).first().selectOption('spotify')
    await page.getByLabel('Playlist', { exact: true }).click()
    await page.getByRole('option', { name: /Road Trip/ }).click()
    await page.getByLabel('Service', { exact: true }).nth(1).selectOption(destination)
    await page.getByRole('radio', { name: 'Create new' }).click()
  }

  try {
    const edited = {}
    const page = await makePage(edited)
    await chooseCreateTransfer(page, 'spotify')
    const publicSwitch = page.getByRole('switch', { name: /Make the new playlist public/ })
    assert.equal(await publicSwitch.isDisabled(), true, 'An account that creates private playlists offers no public switch')
    await page.getByText('Spotify creates private playlists here.', { exact: false }).waitFor()
    await page.getByLabel('Service', { exact: true }).nth(1).selectOption('tidal')
    const description = page.getByLabel('Description (optional)', { exact: true })
    assert.equal(await description.inputValue(), 'Songs for the drive', 'The description starts as the source playlist\'s')
    assert.equal(await publicSwitch.isEnabled(), true)
    await publicSwitch.click()
    assert.equal(await publicSwitch.getAttribute('aria-checked'), 'true')
    await description.fill('Made for the drive')
    await page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText('The new playlist will be public.', { exact: false }).waitFor()
    await dialog.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    for (let attempt = 0; attempt < 50 && !edited.transfer; attempt++) await page.waitForTimeout(50)
    assert.deepEqual(
      { name: edited.transfer.dest_name, description: edited.transfer.dest_description, public: edited.transfer.dest_public, id: edited.transfer.dest_playlist_id },
      { name: 'Road Trip', description: 'Made for the drive', public: true, id: null },
    )
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Transfers must fit ${width}px`)
    }
    console.log('PASS: a created transfer destination takes the edited description and the public choice')
    await page.close()

    const untouched = {}
    const plain = await makePage(untouched)
    await chooseCreateTransfer(plain, 'tidal')
    await plain.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await plain.getByRole('dialog').getByRole('button', { name: 'Copy playlist', exact: true }).click()
    for (let attempt = 0; attempt < 50 && !untouched.transfer; attempt++) await plain.waitForTimeout(50)
    assert.equal(untouched.transfer.dest_description, null, 'An untouched description is copied by the server')
    assert.equal(untouched.transfer.dest_public, false)
    console.log('PASS: an untouched description and switch keep the source description and a private playlist')
    await plain.close()

    const created = {}
    const create = await makePage(created)
    await create.goto(base + '/playlists/create', { waitUntil: 'networkidle' })
    await create.getByLabel('Paste your tracks', { exact: true }).fill('Daft Punk - One More Time')
    await create.getByLabel('Account', { exact: true }).selectOption('spotify')
    const createSwitch = create.getByRole('switch', { name: /Make the playlist public/ })
    assert.equal(await createSwitch.isDisabled(), true)
    await create.getByLabel('Account', { exact: true }).selectOption('tidal')
    await createSwitch.click()
    await create.getByRole('button', { name: 'Find matches', exact: true }).click()
    for (let attempt = 0; attempt < 50 && !created.import; attempt++) await create.waitForTimeout(50)
    assert.equal(created.import.public, true)
    assert.equal(created.import.destination_account, 'tidal')
    console.log('PASS: Create Playlist sends the public choice only where the account can honor it')
    assert.deepEqual(errors, [], 'Browser runtime errors')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
