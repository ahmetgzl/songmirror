// Per-page guidance, navigation, persistent disclosure state, and draft safety.
// API responses are local; reading a guide must not write to a music service.
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require('playwright')

const ACCOUNT = {
  id: 'profile_youtube', provider: 'ytmusic', provider_name: 'YouTube Music', label: 'New account',
  name: 'YouTube Music · New account', auth_kind: 'oauth_device', state: 'connected', fields: [],
  detail: null, transferable: true, preserves_order: false, public_playlists: true,
  capabilities: { library_read: true, library_write: true, public_playlist_read: true },
}
const ROUTES = [
  ['/', 'Dashboard', 'dashboard', 4], ['/accounts', 'Accounts', 'accounts', 3],
  ['/playlists', 'Playlists', 'playlists', 3], ['/sync', 'Sync', 'sync', 3],
  ['/transfers', 'Transfers', 'transfers', 3], ['/playlists/create', 'Create Playlist', 'create', 3],
  ['/playlists/create/history', 'Import history', 'history', 3],
  ['/mappings', null, 'mappings', 3], ['/settings', 'General', 'settings', 3],
  ['/settings?section=backups', 'Playlist backups', 'backups', 3],
  ['/settings?section=downloads', 'Downloads & Jellyfin', 'downloads', 3],
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
  const errors = [], writes = []
  const shots = path.join(__dirname, 'screenshots')
  fs.mkdirSync(shots, { recursive: true })
  try {
    async function setup({ accounts = [ACCOUNT], blockedStorage = false, populated = false, playlists = [] } = {}) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
      if (blockedStorage) {
        await context.addInitScript(() => {
          for (const method of ['getItem', 'setItem']) {
            const original = Storage.prototype[method]
            Storage.prototype[method] = function (key, ...args) {
              if (key === 'songmirror-feature-guides' || key.startsWith('songmirror-guide-v1:') || key.startsWith('songmirror-tour-v1:')) throw new DOMException('Storage blocked', 'SecurityError')
              return original.call(this, key, ...args)
            }
          }
        })
      }
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(`${page.url()}: ${error.message}`))
      await page.route('**/api/**', async route => {
        const request = route.request()
        const pathname = new URL(request.url()).pathname
        if (request.method() !== 'GET') writes.push([request.method(), pathname])
        const send = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
        if (pathname === '/api/accounts') return send(accounts)
        if (pathname === '/api/playlists') return send(playlists)
        if (pathname === '/api/settings') return send({ DISPLAY_NAME: '', DOWNLOAD_DIR: '', LOCAL_MIRROR_FORMAT: '', create_playlist_default_source: 'text' })
        if (pathname === '/api/sync/status') return send({ running: false, jobs: [], last: null })
        if (populated) {
          if (pathname === '/api/syncs') return send([{
            id: 'saved-sync', name: 'My playlists', enabled: false, mode: 'oneway',
            source: 'profile_old_youtube', providers: 'profile_old_youtube,profile_youtube', authorities: '',
            playlists: '', interval: '24h', max_adds: 100, max_removals: 0, download: false,
          }])
          if (pathname === '/api/resolve-cache') return send([{
            id: ACCOUNT.id, provider: 'ytmusic', name: ACCOUNT.name, total: 1, manual: 0, unmatched: 0,
          }])
          if (pathname.startsWith('/api/resolve-cache/')) return send({ total: 1, entries: [{
            key: 'song|artist', name: 'Song', artist: 'Artist', target_id: 'video1', manual: false, url: 'https://music.youtube.com/watch?v=video1',
          }] })
          if (pathname === '/api/playlist-backups') return send([{
            account_id: ACCOUNT.id, provider: 'ytmusic', provider_name: 'YouTube Music', account_name: ACCOUNT.name,
            enabled: false, interval: '24h', format: 'json', retention: 7, storage_dir: '',
            default_storage_dir: '/data/playlist_backups', storage_path: '/data/playlist_backups/profile_youtube',
            running: false, progress: null, next_run_at: null, snapshot_count: 0, last_success: null, last_failure: null,
          }])
          if (pathname === '/api/imports') return send({ jobs: [{
            id: 'saved-import', status: 'ready', source_kind: 'text', destination_account: ACCOUNT.id,
            destination_name: 'My songs', destination_description: '', destination_mode: 'create',
            created_at: '2026-10-15T12:00:00Z', updated_at: '2026-10-15T12:00:00Z',
            total_tracks: 1, matched_tracks: 1, unmatched_tracks: 0, needs_review: 0, tracks_added: 0, tracks_skipped: 0, tracks_failed: 0,
          }] })
        }
        if (pathname === '/api/imports') return send({ jobs: [] })
        if (['/api/playlists', '/api/links', '/api/syncs', '/api/transfers', '/api/resolve-cache', '/api/playlist-backups'].includes(pathname)) return send([])
        return send({})
      })
      await page.route('**/events*', route => route.fulfill({ contentType: 'text/event-stream', body: '' }))
      return { context, page }
    }

    const { context, page } = await setup()
    for (const [url, feature, key, count] of ROUTES) {
      await page.goto(base + url, { waitUntil: 'networkidle' })
      const guide = page.getByRole('region', { name: feature ? `Quick guide: ${feature}` : 'How to use mappings', exact: true })
      const hide = guide.getByRole('button', { name: 'Hide guide', exact: true })
      assert.equal(await hide.getAttribute('aria-expanded'), 'true', `${url} starts expanded`)
      assert.equal(await guide.getByRole('listitem').count(), count, `${url} has its own steps`)
      for (const width of [1280, 375, 320]) {
        await page.setViewportSize({ width, height: 1000 })
        assert.equal(await guide.evaluate(element => [...element.querySelectorAll('p, h2, h3')]
          .every(row => row.scrollWidth <= row.clientWidth + 1)), true, `${url} guide text fits ${width}px`)
      }
      await hide.focus()
      await page.keyboard.press('Enter')
      assert.equal(await guide.getByRole('listitem').count(), 0, 'Collapsed steps leave the accessibility tree')
      assert.equal(await page.evaluate(id => localStorage.getItem(`songmirror-guide-v1:${id}`), key), 'hidden')
      await page.reload({ waitUntil: 'networkidle' })
      const show = guide.getByRole('button', { name: 'Show guide', exact: true })
      assert.equal(await show.getAttribute('aria-expanded'), 'false', `${url} remembers collapse after reload`)
      await show.click()
      assert.equal(await guide.getByRole('listitem').count(), count)

      await page.setViewportSize({ width: 1280, height: 1000 })
      await guide.getByRole('button', { name: 'Start tour', exact: true }).click()
      const tour = page.getByRole('dialog')
      await tour.waitFor()
      await page.locator('.driver-overlay').waitFor({ state: 'visible' })
      assert.equal(await page.locator('.driver-overlay').count(), 1, 'A spotlight overlay is rendered')
      assert.equal(await page.locator('#root').evaluate(element => element.inert), true, 'The tour contains focus and prevents accidental page actions')
      assert.equal(await tour.getAttribute('aria-modal'), 'true')
      assert.equal(await page.locator('.driver-active-element').count(), 1, `${url} highlights one actual target`)
      assert.ok(await page.locator('.driver-active-element').getAttribute('data-tour'), `${url} uses a real control anchor`)
      assert.match(await tour.evaluate(element => getComputedStyle(element).fontFamily), /Archivo/, 'The tour uses the app typography')
      if (await tour.getByRole('button', { name: 'Next', exact: true }).count()) {
        await tour.getByRole('button', { name: 'Next', exact: true }).click()
        await tour.getByRole('button', { name: 'Back', exact: true }).click()
        assert.match(await tour.locator('.driver-popover-progress-text').innerText(), /^Step 1 of /)
      }
      if (key === 'transfers') {
        assert.equal(await page.locator('.driver-active-element').getAttribute('data-tour'), 'transfer-source')
        await page.screenshot({ path: path.join(shots, 'tour-transfers-desktop.png') })
      }
      let tourSteps = 0
      while (await tour.getByRole('button', { name: 'Next', exact: true }).count()) {
        assert.ok(++tourSteps < 8, 'A tour cannot loop indefinitely')
        await page.setViewportSize({ width: 320, height: 568 })
        await page.waitForFunction(() => {
          const box = document.querySelector('.driver-popover')?.getBoundingClientRect()
          const target = document.querySelector('.driver-active-element')?.getBoundingClientRect()
          return box && target && target.bottom > 0 && target.top < innerHeight && box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight
        })
        await page.keyboard.press('Tab')
        assert.equal(await tour.evaluate(element => element.contains(document.activeElement)), true, 'Tab focus stays in the tour')
        if (key === 'transfers' && tourSteps === 1) {
          await page.screenshot({ path: path.join(shots, 'tour-transfers-mobile.png') })
        }
        await tour.getByRole('button', { name: 'Next', exact: true }).click()
      }
      await tour.getByRole('button', { name: 'Done', exact: true }).click()
      await tour.waitFor({ state: 'detached' })
      assert.equal(await page.locator('#root').evaluate(element => element.inert), false)
      await page.waitForFunction(() => document.activeElement?.matches('[data-tour-launcher]'))
      assert.equal(await page.evaluate(id => localStorage.getItem(`songmirror-tour-v1:${id}`), key), 'completed')
      await guide.getByRole('button', { name: 'Replay tour', exact: true }).waitFor()
    }
    console.log('PASS: all 11 feature guides support keyboard use, mobile widths, hide/show, and saved preferences')
    console.log('PASS: all 11 interactive tours highlight real controls, navigate back/next, fit small screens, restore focus, and persist completion')

    // Real-sized cards and short windows reproduce the clipped footer and
    // square outline that were missed by the original tall-window coverage.
    const compact = await setup({ playlists: [{ id: 'mix', name: 'My playlist', count: 150, image: '', owned: true }] })
    const compactSession = await compact.context.newCDPSession(compact.page)
    for (const [url, width, height, zoom] of [
      ['/playlists', 1280, 720, 1], ['/playlists', 800, 600, 1],
      ['/playlists', 701, 480, 1], ['/transfers', 954, 472, 1],
      ['/transfers', 375, 360, 1], ['/playlists', 960, 640, 1.5],
    ]) {
      await compact.page.setViewportSize({ width, height })
      await compact.page.goto(base + url, { waitUntil: 'networkidle' })
      await compact.page.getByRole('button', { name: /^(Start|Replay) tour$/ }).click()
      await compact.page.getByRole('dialog').getByRole('button', { name: 'Next', exact: true }).click()
      await compactSession.send('Emulation.setPageScaleFactor', { pageScaleFactor: zoom })
      await compact.page.waitForFunction(() => {
        const popover = document.querySelector('.songmirror-tour')
        const frame = document.querySelector('.songmirror-tour-highlight')
        if (!popover?.dataset.placement || !frame || frame.hidden) return false
        const box = popover.getBoundingClientRect(), footer = popover.querySelector('.driver-popover-footer').getBoundingClientRect()
        const target = document.querySelector('.driver-active-element').getBoundingClientRect(), highlight = frame.getBoundingClientRect()
        const viewport = visualViewport
        return Math.abs(highlight.left - target.left + 6) < 1 && Math.abs(highlight.top - target.top + 6) < 1 &&
          box.left >= viewport.offsetLeft && box.top >= viewport.offsetTop && box.right <= viewport.offsetLeft + viewport.width + 1 && box.bottom <= viewport.offsetTop + viewport.height + 1 && footer.top >= box.top && footer.bottom <= box.bottom + 1
      })
      const geometry = await compact.page.evaluate(() => {
        const popover = document.querySelector('.songmirror-tour'), frame = document.querySelector('.songmirror-tour-highlight')
        const p = popover.getBoundingClientRect(), t = document.querySelector('.driver-active-element').getBoundingClientRect()
        return { side: popover.dataset.placement, overlaps: p.left < t.right && p.right > t.left && p.top < t.bottom && p.bottom > t.top,
          frameRadius: getComputedStyle(frame).borderRadius, outline: getComputedStyle(document.querySelector('.driver-active-element')).outlineStyle }
      })
      if (geometry.side !== 'viewport') assert.equal(geometry.overlaps, false, 'Adjacent placement must not cover the highlighted area')
      assert.equal(geometry.frameRadius, '8px')
      assert.equal(geometry.outline, 'none', 'The target does not draw an outline that the spotlight can clip')
      await compact.page.screenshot({ path: path.join(shots, `tour-layout-${url.slice(1)}-${width}-${height}-${zoom}.png`) })
      await compact.page.keyboard.press('Escape')
      await compact.page.locator('.songmirror-tour-highlight').waitFor({ state: 'detached' })
    }
    await compact.context.close()
    console.log('PASS: tour tooltips and navigation fit short and zoomed windows; rounded highlights render independently of target clipping')

    // Switching Settings sections reuses the page without leaking guide state.
    await page.goto(base + '/settings', { waitUntil: 'networkidle' })
    await page.getByRole('region', { name: 'Quick guide: General' }).getByRole('button', { name: 'Hide guide' }).click()
    await page.getByRole('link', { name: 'Playlist backups', exact: true }).click()
    const backups = page.getByRole('region', { name: 'Quick guide: Playlist backups' })
    await backups.getByRole('button', { name: 'Hide guide' }).click()
    await page.getByRole('link', { name: 'Downloads & Jellyfin', exact: true }).click()
    await page.getByRole('region', { name: 'Quick guide: Downloads & Jellyfin' }).getByRole('button', { name: 'Hide guide' }).waitFor()
    await page.getByRole('link', { name: 'Playlist backups', exact: true }).click()
    await backups.getByRole('button', { name: 'Show guide' }).waitFor()
    assert.equal(await backups.getByRole('button', { name: 'Show guide' }).isVisible(), true)
    console.log('PASS: Settings sections have independent guide state during client-side navigation')

    // The device-wide preference removes the whole guide, including tour
    // launchers, without overwriting each page's disclosure preference.
    await page.goto(base + '/settings', { waitUntil: 'networkidle' })
    const guidesSwitch = page.getByRole('switch', { name: /^Show feature guides/ })
    await guidesSwitch.click()
    assert.equal(await guidesSwitch.getAttribute('aria-checked'), 'false')
    assert.equal(await page.locator('[data-tour-launcher]').count(), 0)
    await page.reload({ waitUntil: 'networkidle' })
    assert.equal(await guidesSwitch.getAttribute('aria-checked'), 'false')
    for (const [url] of ROUTES) {
      await page.goto(base + url, { waitUntil: 'networkidle' })
      assert.equal(await page.locator('[data-tour-launcher]').count(), 0, `${url}: global preference hides the guide`)
      assert.equal(await page.getByRole('button', { name: /^(Hide|Show) guide$/ }).count(), 0)
    }
    await page.goto(base + '/settings', { waitUntil: 'networkidle' })
    await guidesSwitch.click()
    await page.getByRole('region', { name: 'Quick guide: General' }).getByRole('button', { name: 'Show guide' }).waitFor()
    await page.getByRole('link', { name: 'Playlist backups', exact: true }).click()
    await backups.getByRole('button', { name: 'Show guide' }).waitFor()
    await page.getByRole('link', { name: 'Downloads & Jellyfin', exact: true }).click()
    await page.getByRole('region', { name: 'Quick guide: Downloads & Jellyfin' }).getByRole('button', { name: 'Hide guide' }).waitFor()
    console.log('PASS: global guide switch persists across all 11 pages and restores individual disclosure preferences')

    await page.goto(base + '/transfers', { waitUntil: 'networkidle' })
    const transferGuide = page.getByRole('region', { name: 'Quick guide: Transfers' })
    assert.match(await transferGuide.innerText(), /public YouTube playlist/)
    await page.getByRole('radio', { name: 'Paste a link' }).click()
    await page.getByLabel('Playlist link', { exact: true }).fill('https://youtube.com/playlist?list=my-draft')
    await transferGuide.getByRole('button', { name: 'Hide guide' }).click()
    await transferGuide.getByRole('button', { name: 'Show guide' }).click()
    await transferGuide.getByRole('button', { name: 'Replay tour' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Skip tour' }).click()
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    assert.equal(await page.evaluate(() => localStorage.getItem('songmirror-tour-v1:transfers')), 'skipped')
    assert.equal(await page.getByLabel('Playlist link', { exact: true }).inputValue(), 'https://youtube.com/playlist?list=my-draft')
    assert.equal(await page.getByRole('heading', { name: 'Source', exact: true }).count(), 2) // Guide and form.
    assert.equal(await page.getByText('DECK A · SOURCE').count(), 0)
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.screenshot({ path: path.join(shots, 'onboarding-transfers-desktop.png'), fullPage: true })
    await page.setViewportSize({ width: 375, height: 1000 })
    await page.screenshot({ path: path.join(shots, 'onboarding-transfers-mobile.png'), fullPage: true })
    console.log('PASS: YouTube migration guidance is visible and toggling it preserves an unfinished transfer')

    // Dashboard links are real routes and opening them causes no mutations.
    await page.goto(base, { waitUntil: 'networkidle' })
    const dashboard = page.getByRole('region', { name: 'Quick guide: Dashboard' })
    assert.equal(await dashboard.locator('ol').count(), 0, 'Alternative tasks are not numbered as a sequence')
    await dashboard.getByRole('link', { name: 'Transfers', exact: true }).click()
    assert.equal(new URL(page.url()).pathname, '/transfers')

    // An unavailable current target is skipped without leaving a stale cutout.
    await transferGuide.getByRole('button', { name: 'Replay tour' }).click()
    await page.waitForFunction(() => document.querySelector('.driver-active-element')?.getAttribute('data-tour') === 'transfer-source')
    await page.locator('[data-tour="transfer-source"]').evaluate(element => element.setAttribute('hidden', ''))
    await page.waitForFunction(() => document.querySelector('.driver-active-element')?.getAttribute('data-tour') === 'transfer-destination')
    await page.keyboard.press('Escape')
    await page.getByRole('dialog').waitFor({ state: 'detached' })
    await page.locator('.driver-overlay').waitFor({ state: 'detached' })
    assert.equal(await page.locator('#root').evaluate(element => element.inert), false)
    await page.locator('[data-tour="transfer-source"]').evaluate(element => element.removeAttribute('hidden'))
    await page.reload({ waitUntil: 'networkidle' })
    await transferGuide.getByRole('button', { name: 'Replay tour' }).waitFor()

    // Tour links navigate normally and close the overlay before changing routes.
    await page.goto(base, { waitUntil: 'networkidle' })
    await dashboard.getByRole('button', { name: 'Replay tour' }).click()
    await page.getByRole('dialog').getByRole('link', { name: 'Accounts', exact: true }).click()
    await page.getByRole('heading', { level: 1, name: 'Accounts', exact: true }).waitFor()
    assert.equal(await page.locator('.driver-overlay').count(), 0)
    await page.getByRole('region', { name: 'Quick guide: Accounts' }).getByRole('button', { name: 'Replay tour' }).click()
    await page.goBack({ waitUntil: 'networkidle' })
    await page.getByRole('heading', { level: 1 }).waitFor()
    assert.equal(await page.locator('.driver-overlay').count(), 0, 'History navigation cleans up an active tour')
    assert.equal(await page.locator('#root').evaluate(element => element.inert), false)
    await context.close()

    const filled = await setup({ populated: true, accounts: [
      { ...ACCOUNT, id: 'profile_old_youtube', label: 'Old account', name: 'YouTube Music · Old account' }, ACCOUNT,
    ] })
    for (const [url, feature, targets] of [
      ['/sync', 'Quick guide: Sync', ['sync-new', 'sync-preview']],
      ['/mappings', 'How to use mappings', ['mapping-providers', 'mapping-filters', 'mapping-row']],
      ['/settings?section=backups', 'Quick guide: Playlist backups', ['backup-add', 'backup-schedule', 'backup-result']],
      ['/playlists/create/history', 'Quick guide: Import history', ['import-history', 'import-resume', 'creation-back']],
    ]) {
      await filled.page.goto(base + url, { waitUntil: 'networkidle' })
      for (const target of targets) await filled.page.locator(`[data-tour="${target}"]`).first().waitFor()
      await filled.page.getByRole('region', { name: feature, exact: true }).getByRole('button', { name: 'Start tour', exact: true }).click()
      const tour = filled.page.getByRole('dialog')
      for (let index = 0; index < targets.length; index++) {
        await filled.page.waitForFunction(name => document.querySelector('.driver-active-element')?.getAttribute('data-tour') === name, targets[index])
        const label = index === targets.length - 1 ? 'Done' : 'Next'
        await tour.getByRole('button', { name: label, exact: true }).click()
      }
      await tour.waitFor({ state: 'detached' })
    }
    await filled.context.close()
    console.log('PASS: saved syncs, populated mappings, existing backup schedules, and resumable imports highlight their actual controls')

    const empty = await setup({ accounts: [] })
    await empty.page.goto(base + '/transfers', { waitUntil: 'networkidle' })
    await empty.page.getByRole('main').getByRole('link', { name: 'Accounts', exact: true }).click()
    assert.equal(new URL(empty.page.url()).pathname, '/accounts')
    await empty.context.close()
    console.log('PASS: task links and the unconnected transfer state lead to working next steps')

    const storage = await setup({ blockedStorage: true })
    await storage.page.goto(base + '/accounts', { waitUntil: 'networkidle' })
    const storageGuide = storage.page.getByRole('region', { name: 'Quick guide: Accounts' })
    await storageGuide.getByRole('button', { name: 'Hide guide' }).click()
    await storageGuide.getByRole('button', { name: 'Show guide' }).click()
    assert.equal(await storageGuide.getByRole('listitem').count(), 3)
    await storageGuide.getByRole('button', { name: 'Start tour' }).click()
    await storage.page.locator('.driver-overlay').waitFor({ state: 'visible' })
    await storage.page.keyboard.press('Escape')
    await storage.page.getByRole('dialog').waitFor({ state: 'detached' })
    assert.equal(await storage.page.locator('#root').evaluate(element => element.inert), false)
    await storage.page.goto(base + '/settings', { waitUntil: 'networkidle' })
    const storageSwitch = storage.page.getByRole('switch', { name: /^Show feature guides/ })
    await storageSwitch.click()
    assert.equal(await storage.page.locator('[data-tour-launcher]').count(), 0)
    await storage.page.getByRole('link', { name: 'Playlist backups', exact: true }).click()
    assert.equal(await storage.page.locator('[data-tour-launcher]').count(), 0)
    await storage.page.getByRole('link', { name: 'General', exact: true }).click()
    await storageSwitch.click()
    await storage.page.locator('[data-tour-launcher]').waitFor()
    await storage.context.close()
    console.log('PASS: guides remain usable when preference storage is blocked')

    for (const language of ['es', 'ar', 'ja']) {
      const localized = await setup()
      await localized.context.addInitScript(lang => localStorage.setItem('songmirror.language', lang), language)
      await localized.page.goto(base + '/transfers', { waitUntil: 'networkidle' })
      const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, `../src/i18n/locales/${language}.json`), 'utf8'))
      const title = catalog['Quick guide: {{feature}}'].replace('{{feature}}', catalog.Transfers)
      const guide = localized.page.getByRole('region', { name: title, exact: true })
      await guide.waitFor()
      assert.equal(await guide.getByRole('button', { name: catalog['Hide guide'], exact: true }).isVisible(), true)
      await localized.page.setViewportSize({ width: 375, height: 1000 })
      assert.equal(await guide.evaluate(element => [...element.querySelectorAll('p, h2, h3')]
        .every(row => row.scrollWidth <= row.clientWidth + 1)), true, `${language} guide fits mobile`)
      if (language === 'ar') {
        assert.equal(await localized.page.locator('html').getAttribute('dir'), 'rtl')
        await localized.page.screenshot({ path: path.join(shots, 'onboarding-transfers-arabic.png'), fullPage: true })
      }
      await guide.getByRole('button', { name: catalog['Start tour'], exact: true }).click()
      const tour = localized.page.getByRole('dialog')
      await tour.waitFor()
      assert.equal(await tour.getAttribute('dir'), language === 'ar' ? 'rtl' : 'ltr')
      if (language === 'ar') {
        await localized.page.keyboard.press('ArrowLeft')
        assert.equal(await localized.page.locator('.driver-active-element').getAttribute('data-tour'), 'transfer-destination')
        await localized.page.screenshot({ path: path.join(shots, 'tour-transfers-arabic.png') })
      }
      await tour.getByRole('button', { name: catalog['Skip tour'], exact: true }).click()
      await tour.waitFor({ state: 'detached' })
      await localized.context.close()
    }
    assert.deepEqual(writes, [], 'Reading, navigating, or dismissing guidance must not modify account data')
    assert.deepEqual(errors, [])
    console.log('PASS: translated and RTL guides work without browser errors or API writes')
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
