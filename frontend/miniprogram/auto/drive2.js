/* eslint-disable */
// 清登录态 -> 重新登录(应拿到 role=2) -> 截首页验证「清运」tab
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
      wx.removeStorageSync('ecobin_token')
      wx.removeStorageSync('ecobin_role')
      wx.removeStorageSync('ecobin_user_info')
      const app = getApp()
      if (app && app.globalData) { app.globalData.token = undefined; app.globalData.role = undefined; app.globalData.userInfo = undefined }
    })
    console.log('cleared login state.')

    // 2) 重新走登录页
    await mp.reLaunch('/pages/login/login')
    await sleep(6000)
    let page = await mp.currentPage()
    console.log('route after re-login:', page.path)

    // 3) 回首页
    await mp.switchTab('/pages/home/home').catch(() => {})
    await sleep(2500)
    page = await mp.currentPage()
    console.log('current route:', page.path)

    // 4) 截图
    await mp.screenshot({ path: path.join(SHOT_DIR, 'home-role2.png') })
    console.log('home-role2.png saved')

    // 5) 读 role
    const role = await mp.evaluate(() => { const a = getApp(); return a && a.globalData ? a.globalData.role : null })
    console.log('ROLE:', role)
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    await mp.close().catch(() => {})
    console.log('closed.')
  }
})()
