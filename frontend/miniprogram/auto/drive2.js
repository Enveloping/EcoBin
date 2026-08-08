/* eslint-disable */
// 清会话 -> 从游客首页重新执行身份引导 -> 截首页
const path = require('path')
const fs = require('fs')
const automator = require('miniprogram-automator')

const SHOT_DIR = path.join(path.resolve(__dirname, '..'), 'auto', 'shots')
fs.mkdirSync(SHOT_DIR, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  let mp
  for (let i = 0; i < 30; i++) {
    try { mp = await automator.connect({ wsEndpoint: ws }); break } catch (e) { await sleep(2000) }
  }
  if (!mp) { console.error('连接失败'); process.exit(1) }
  console.log('connected.')

  try {
    // 1) 清登录态（storage + globalData）
    await mp.evaluate(() => {
      wx.removeStorageSync('ecobin_miniapp_session')
      wx.removeStorageSync('ecobin_silent_login_suppressed')
      const app = getApp()
      if (app && app.globalData) { app.globalData.session = undefined; app.globalData.testViewMode = undefined }
    })
    console.log('cleared login state.')

    // 2) 从游客首页重新走身份引导
    await mp.reLaunch('/pages/home/home')
    await sleep(6000)
    let page = await mp.currentPage()
    console.log('route after identity bootstrap:', page.path)

    // 3) 回首页
    await mp.switchTab('/pages/home/home').catch(() => {})
    await sleep(2500)
    page = await mp.currentPage()
    console.log('current route:', page.path)

    // 4) 截图
    await mp.screenshot({ path: path.join(SHOT_DIR, 'home-role2.png') })
    console.log('home-role2.png saved')

    // 5) 读当前 audience；游客时为空
    const audience = await mp.evaluate(() => { const a = getApp(); return a && a.globalData && a.globalData.session ? a.globalData.session.audience : null })
    console.log('AUDIENCE:', audience)
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    await mp.close().catch(() => {})
    console.log('closed.')
  }
})()
