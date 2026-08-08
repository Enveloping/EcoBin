/* eslint-disable */
const path = require('path')
const fs = require('fs')
const automator = require('miniprogram-automator')
const SHOT_DIR = path.join(path.resolve(__dirname, '..'), 'auto', 'shots')
fs.mkdirSync(SHOT_DIR, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const TABS = [
  '/pages/home/home',
  '/pages/clean/clean',
  '/pages/records/records',
  '/pages/wallet/wallet',
  '/pages/profile/profile',
]

;(async () => {
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  let mp
  for (let i = 0; i < 30; i++) { try { mp = await automator.connect({ wsEndpoint: ws }); break } catch (e) { await sleep(2000) } }
  if (!mp) { console.error('连接失败'); process.exit(1) }
  console.log('connected.')
  const errors = []
  mp.on('console', (m) => { if (m.type === 'error') { const a = (m.args||[]).map(x=>x&&x.value!==undefined?x.value:'').join(' '); errors.push('[err] '+a) } })
  mp.on('exception', (e) => { errors.push('[exc] '+((e&&(e.value||e.message))||'')) })
  try {
    await mp.reLaunch('/pages/home/home')
    await sleep(5000)
    const audience = await mp.evaluate(() => { const a = getApp(); return a && a.globalData && a.globalData.session ? a.globalData.session.audience : null })
    console.log('AUDIENCE:', audience)
    for (const p of TABS) {
      const name = 'p-' + p.split('/').pop()
      try {
        await mp.switchTab(p).catch(async () => { await mp.reLaunch(p) })
        await sleep(2500)
        const page = await mp.currentPage()
        await mp.screenshot({ path: path.join(SHOT_DIR, name + '.png') })
        console.log('shot', name, '<-', page.path)
      } catch (e) {
        console.log('FAIL', name, e && e.message)
      }
    }
    console.log('---- ERRORS (' + errors.length + ') ----')
    errors.slice(0, 20).forEach((e) => console.log(e))
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    process.exit(0)
  }
})()
