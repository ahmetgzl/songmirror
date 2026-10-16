// YouTube account migration: a public link needs only the destination account;
// a private playlist can use two named accounts of the same provider.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const NEW_ACCOUNT = {
  id: 'profile_new_youtube', provider: 'ytmusic', provider_name: 'YouTube Music',
  label: 'New email', name: 'YouTube Music · New email', state: 'connected', fields: [],
  transferable: true, preserves_order: false, public_playlists: true,
  capabilities: { library_read: true, library_write: true, public_playlist_read: true },
}
const OLD_ACCOUNT = { ...NEW_ACCOUNT, id: 'profile_old_youtube', label: 'Old email', name: 'YouTube Music · Old email' }
const PLAYLIST = { id: 'PL_owned', name: 'My videos', description: '', count: 1250, image: '', owned: true }

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
  const errors = []
  try {
    async function setup(accounts, result = {}, playlists = [PLAYLIST]) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
      const page = await context.newPage()
      const submissions = [], previews = []
      page.on('pageerror', error => errors.push(error.message))
      await page.route('**/api/**', async route => {
        const req = route.request()
        const url = new URL(req.url())
        const send = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
        if (url.pathname === '/api/accounts') return send(accounts)
        if (url.pathname === '/api/playlists') return send(playlists)
        if (url.pathname === '/api/transfers/preview') {
          previews.push(req.postDataJSON())
          return send({ provider: 'ytmusic', account: NEW_ACCOUNT.id, playlist_id: 'PL_public',
            name: 'Old account videos', description: 'Collected over the years', count: 1250, image: '',
            external_url: 'https://www.youtube.com/playlist?list=PL_public' })
        }
        if (url.pathname === '/api/transfers' && req.method() === 'POST') {
          submissions.push(req.postDataJSON())
          return send({ job_id: 'copy1' }, 202)
        }
        if (url.pathname === '/api/transfers/copy1') {
          return send({ id: 'copy1', status: 'done',
            source: { account: NEW_ACCOUNT.id, provider: 'ytmusic', name: NEW_ACCOUNT.name, playlist_id: 'PL_public', playlist_name: 'Old account videos' },
            dest: { account: NEW_ACCOUNT.id, provider: 'ytmusic', name: NEW_ACCOUNT.name, playlist_id: 'PL_copy', playlist_name: 'Old account videos' },
            added: 1250, deferred: 0, unavailable: 0, conflicts: [], total: 1250, processed: 1250, error: null, ...result })
        }
        if (url.pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
        if (['/api/transfers', '/api/syncs', '/api/links'].includes(url.pathname)) return send([])
        return send({})
      })
      await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
      await page.goto(`http://127.0.0.1:${server.address().port}/transfers`, { waitUntil: 'networkidle' })
      return { context, page, submissions, previews }
    }

    // Only one account is connected: read an old account's public YouTube URL
    // with the new account and create the copy there.
    const single = await setup([NEW_ACCOUNT])
    const { page } = single
    await page.getByRole('radio', { name: 'Paste a link' }).click()
    await page.getByLabel('Open with account', { exact: true }).selectOption(NEW_ACCOUNT.id)
    const url = 'https://www.youtube.com/playlist?list=PL_public'
    await page.getByLabel('Playlist link', { exact: true }).fill(url)
    await page.getByRole('button', { name: 'Open link', exact: true }).click()
    await page.getByText('Old account videos', { exact: true }).waitFor()
    assert.deepEqual(single.previews, [{ url, source_account: NEW_ACCOUNT.id }])
    await page.getByLabel('Service', { exact: true }).selectOption(NEW_ACCOUNT.id)
    await page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await page.getByLabel(/New playlist name/).fill('Videos from my old account')
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1000 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Form must fit ${width}px`)
    }
    await page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    const confirm = page.getByRole('dialog')
    assert.match(await confirm.innerText(), /Videos from my old account/)
    await confirm.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await confirm.waitFor({ state: 'detached' })
    await page.getByText('1250', { exact: true }).first().waitFor()
    assert.deepEqual(single.submissions, [{
      source_account: NEW_ACCOUNT.id, source_playlist_id: 'PL_public', dest_account: NEW_ACCOUNT.id,
      dest_playlist_id: null, dest_name: 'Videos from my old account', dest_description: null,
      dest_public: false, preserve_order: false,
    }])
    await single.context.close()
    console.log('PASS: one connected YouTube account can copy an old account’s public playlist')

    const household = await setup([OLD_ACCOUNT, NEW_ACCOUNT])
    const services = household.page.getByLabel('Service', { exact: true })
    await services.nth(0).selectOption(OLD_ACCOUNT.id)
    await household.page.getByRole('button', { name: 'Playlist', exact: true }).click()
    await household.page.getByRole('option', { name: /My videos/ }).click()
    await services.nth(1).selectOption(NEW_ACCOUNT.id)
    await household.page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await household.page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await household.page.getByRole('dialog').getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await household.page.getByRole('dialog').waitFor({ state: 'detached' })
    await household.page.getByText('1250', { exact: true }).first().waitFor()
    assert.equal(household.submissions[0].source_account, OLD_ACCOUNT.id)
    assert.equal(household.submissions[0].dest_account, NEW_ACCOUNT.id)
    await household.context.close()
    console.log('PASS: two YouTube accounts remain independently selectable for a library copy')

    const videos = await setup([OLD_ACCOUNT, NEW_ACCOUNT], { added: 788, unavailable: 4, total: 795, processed: 795 })
    const videoServices = videos.page.getByLabel('Service', { exact: true })
    await videoServices.nth(0).selectOption(OLD_ACCOUNT.id)
    await videos.page.getByRole('button', { name: 'Playlist', exact: true }).click()
    await videos.page.getByRole('option', { name: /My videos/ }).click()
    await videoServices.nth(1).selectOption(NEW_ACCOUNT.id)
    await videos.page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await videos.page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await videos.page.getByRole('dialog').getByRole('button', { name: 'Copy playlist', exact: true }).click()
    await videos.page.getByRole('dialog').waitFor({ state: 'detached' })
    await videos.page.getByText('· 788 added', { exact: true }).waitFor()
    await videos.page.getByText('· 4 unavailable skipped', { exact: true }).waitFor()
    assert.equal(await videos.page.getByText(/unavailable TIDAL/).count(), 0)
    await videos.context.close()
    console.log('PASS: video transfers distinguish copied and unavailable entries without TIDAL-specific advice')

    const groupedRows = [
      { ...PLAYLIST, id: 'video-owned', name: 'A video owned', library_kind: 'video' },
      { ...PLAYLIST, id: 'music-followed', name: 'Z music followed', owned: false, library_kind: 'music' },
      { ...PLAYLIST, id: 'music-owned', name: 'Z music owned', library_kind: 'music' },
      { ...PLAYLIST, id: 'video-followed', name: 'A video followed', owned: false, library_kind: 'video' },
      ...Array.from({ length: 8 }, (_, i) => ({ ...PLAYLIST, id: `other-${i}`, name: `Other video ${i}`, library_kind: 'video' })),
    ]
    const grouped = await setup([OLD_ACCOUNT, NEW_ACCOUNT], {}, groupedRows)
    const groupedServices = grouped.page.getByLabel('Service', { exact: true })
    await groupedServices.nth(0).selectOption(OLD_ACCOUNT.id)
    await grouped.page.getByRole('button', { name: 'Playlist', exact: true }).click()
    const listbox = grouped.page.getByRole('listbox')
    assert.deepEqual(await listbox.getByRole('group').evaluateAll(groups => groups.map(g => g.getAttribute('aria-label'))),
      ['YouTube Music playlists', 'YouTube videos'])
    const optionNames = await listbox.getByRole('option').allTextContents()
    assert.match(optionNames[0], /Z music owned/)
    assert.match(optionNames[1], /Z music followed/)
    assert.match(optionNames[2], /A video owned/)
    await grouped.page.getByRole('textbox', { name: 'Search playlists', exact: true }).fill('music followed')
    assert.equal(await listbox.getByRole('group').count(), 1)
    await listbox.getByRole('option', { name: /Z music followed/ }).click()
    await groupedServices.nth(1).selectOption(NEW_ACCOUNT.id)
    await grouped.page.getByRole('button', { name: 'Existing playlist', exact: true }).click()
    assert.equal(await grouped.page.getByRole('listbox').getByRole('option', { name: /followed/ }).count(), 0)
    assert.match((await grouped.page.getByRole('listbox').getByRole('option').allTextContents())[0], /Z music owned/)
    await grouped.page.keyboard.press('Escape')
    await grouped.page.goto(grouped.page.url().replace('/transfers', '/playlists'), { waitUntil: 'networkidle' })
    const library = grouped.page.getByRole('region', { name: OLD_ACCOUNT.name, exact: true })
    await library.getByText('YouTube Music playlists', { exact: true }).waitFor()
    assert.match((await library.getByRole('button', { name: /inside SongMirror/ }).allTextContents())[0], /Z music/)
    await grouped.page.getByLabel('Sort playlists', { exact: true }).selectOption('name-desc')
    const sortedNames = await library.getByRole('button', { name: /inside SongMirror/ }).allTextContents()
    assert.match(sortedNames[0], /Z music/)
    assert.match(sortedNames[1], /Z music/)
    assert.equal(grouped.submissions.length, 0)
    await grouped.context.close()
    console.log('PASS: Music playlists stay first in labeled picker and browser groups; search and destination ownership remain correct')

    for (const legacy of [false, true]) {
      const liveState = { status: 'running', total: 795, processed: 795, added: legacy ? 788 : 2,
        ...(legacy ? {} : { phase: 'copying', written: 2, write_total: 788 }) }
      const live = await setup([NEW_ACCOUNT], liveState)
      const liveServices = live.page.getByLabel('Service', { exact: true })
      await liveServices.nth(0).selectOption(NEW_ACCOUNT.id)
      await live.page.getByRole('button', { name: 'Playlist', exact: true }).click()
      await live.page.getByRole('option', { name: /My videos/ }).click()
      await liveServices.nth(1).selectOption(NEW_ACCOUNT.id)
      await live.page.getByRole('radio', { name: 'Create new', exact: true }).click()
      await live.page.getByRole('button', { name: 'Copy playlist', exact: true }).click()
      await live.page.getByRole('dialog').getByRole('button', { name: 'Copy playlist', exact: true }).click()
      await live.page.getByRole('dialog').waitFor({ state: 'detached' })
      await live.page.getByText('Copying tracks…', { exact: false }).first().waitFor()
      const progressBar = live.page.getByRole('progressbar')
      assert.equal(await progressBar.getAttribute('aria-valuenow'), legacy ? null : '2')
      assert.equal(await progressBar.getAttribute('aria-valuemax'), legacy ? null : '788')
      assert.equal(await live.page.getByText('+788', { exact: true }).count(), 0)
      if (!legacy) {
        liveState.added = liveState.written = 3
        await live.page.getByText('+3', { exact: true }).waitFor()
        assert.equal(await progressBar.getAttribute('aria-valuenow'), '3')
      }
      await live.context.close()
    }
    console.log('PASS: writing progress counts acknowledged videos; older servers cannot display queued videos as already added')

    const self = await setup([NEW_ACCOUNT])
    const selfServices = self.page.getByLabel('Service', { exact: true })
    await selfServices.nth(0).selectOption(NEW_ACCOUNT.id)
    await self.page.getByRole('button', { name: 'Playlist', exact: true }).click()
    await self.page.getByRole('option', { name: /My videos/ }).click()
    await selfServices.nth(1).selectOption(NEW_ACCOUNT.id)
    await self.page.getByRole('button', { name: 'Existing playlist', exact: true }).click()
    await self.page.getByRole('option', { name: /My videos/ }).click()
    await self.page.getByText("That's the same playlist as the source.", { exact: false }).waitFor()
    assert.equal(await self.page.getByRole('button', { name: 'Copy playlist', exact: true }).isDisabled(), true)
    await self.page.getByRole('radio', { name: 'Create new', exact: true }).click()
    await self.page.getByRole('button', { name: 'Copy playlist', exact: true }).click({ trial: true })
    assert.equal(await self.page.getByRole('button', { name: 'Copy playlist', exact: true }).isEnabled(), true)
    await self.context.close()
    console.log('PASS: copying a playlist into itself is blocked while creating a separate copy is allowed')

    for (const accounts of [[], [{ ...NEW_ACCOUNT, capabilities: { ...NEW_ACCOUNT.capabilities, library_write: false } }]]) {
      const unavailable = await setup(accounts)
      assert.equal(await unavailable.page.getByRole('button', { name: 'Copy playlist', exact: true }).count(), 0)
      await unavailable.context.close()
    }
    assert.deepEqual(errors, [])
    console.log('PASS: no accounts and read-only accounts cannot start copies; no browser errors')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
