/* eslint-disable */
// 用 miniprogram-automator 驱动开发者工具：登录 -> 截首页 -> 读 tabBar
const path = require('path')
const fs = require('fs')
const automator = require('miniprogram-automator')

const CLI = 'C:\\D\\001-Apps\\微信web开发者工具\\cli.bat'
const PROJECT = path.resolve(__dirname, '..')
const SHOT_DIR = path.join(PROJECT, 'auto', 'shots')
fs.mkdirSync(SHOT_DIR, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function readTabBar(mp) {
  // 自定义 tabBar 是页面外的全局组件，通过 getElement 抓取 .tab-bar 下的文案
  try {
    const page = await mp.currentPage()
    const items = await page.$$('.tab-bar-text')
    const texts = []
    for (const el of items) texts.push((await el.text()).trim())
    return texts
  } catch (e) {
    return ['<读取失败: ' + e.message + '>']
  }
}

;(async () => {
  console.log('PROJECT =', PROJECT)
  // Node 22 无法直接 spawn .bat，故由外部用 PowerShell 起 `cli auto`，这里只 connect
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  console.log('connecting to', ws)
  let mp
  for (let i = 0; i < 30; i++) {
    try {
      mp = await automator.connect({ wsEndpoint: ws })
      break
    } catch (e) {
      await sleep(2000)
    }
  }
  if (!mp) {
    console.error('无法连接自动化端口，确认 cli auto 已在 ' + ws + ' 启动')
    process.exit(1)
  }
  console.log('connected.')

  try {
    // 1) 进登录页触发静默登录
    await mp.reLaunch('/pages/login/login')
    await sleep(6000)

    let page = await mp.currentPage()
    console.log('route after login wait:', page.path)

    // 登录失败仍停留在 login -> 截图看错误
    if (page.path.includes('login')) {
      await mp.screenshot({ path: path.join(SHOT_DIR, 'login-state.png') })
      const errEl = await page.$('.hint')
      if (errEl) console.log('login hint:', (await errEl.text()).trim())
      console.log('仍在登录页，已截图 login-state.png')
    }

    // 2) 确保在首页
    await mp.switchTab('/pages/home/home').catch(() => {})
    await sleep(2500)
    page = await mp.currentPage()
    console.log('current route:', page.path)

    // 3) 截首页
    await mp.screenshot({ path: path.join(SHOT_DIR, 'home.png') })
    console.log('home.png saved')

    // 4) 读 tabBar 文案
    const tabs = await readTabBar(mp)
    console.log('TABBAR:', JSON.stringify(tabs))

    // 5) 读 globalData.role
    const role = await mp.evaluate(() => {
      const app = getApp()
      return app && app.globalData ? app.globalData.role : null
    })
    console.log('ROLE:', role)
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    await mp.close().catch(() => {})
    console.log('closed.')
  }
})()
