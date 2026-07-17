/* eslint-disable */
const path = require('path')
const fs = require('fs')
const automator = require('miniprogram-automator')
const SHOT_DIR = path.join(path.resolve(__dirname, '..'), 'auto', 'shots')
fs.mkdirSync(SHOT_DIR, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const name = process.argv[2] || 'shot'

;(async () => {
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  let mp
  for (let i = 0; i < 30; i++) { try { mp = await automator.connect({ wsEndpoint: ws }); break } catch (e) { await sleep(2000) } }
  if (!mp) { console.error('连接失败'); process.exit(1) }
  console.log('connected.')
  try {
    await mp.reLaunch('/pages/login/login')
    await sleep(5000)
    await mp.switchTab('/pages/home/home').catch(() => {})
    await sleep(3000) // 等字体/样式渲染
    const page = await mp.currentPage()
    console.log('route:', page.path)
    await mp.screenshot({ path: path.join(SHOT_DIR, name + '.png') })
    console.log(name + '.png saved')
    const role = await mp.evaluate(() => { const a = getApp(); return a && a.globalData ? a.globalData.role : null })
    console.log('ROLE:', role)
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    process.exit(0)
  }
})()
