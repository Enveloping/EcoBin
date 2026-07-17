/* eslint-disable */
const automator = require('miniprogram-automator')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

;(async () => {
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  let mp
  for (let i = 0; i < 30; i++) { try { mp = await automator.connect({ wsEndpoint: ws }); break } catch (e) { await sleep(2000) } }
  if (!mp) { console.error('连接失败'); process.exit(1) }
  console.log('connected.')

  try {
    await mp.switchTab('/pages/home/home').catch(() => {})
    await sleep(1500)
    const page = await mp.currentPage()

    async function info(sel) {
      const els = await page.$$(sel)
      if (!els.length) return `${sel}: NOT FOUND`
      const el = els[0]
      let size = {}, tag = ''
      try { size = await el.size() } catch (e) {}
      try { tag = await el.property('tagName') } catch (e) {}
      return `${sel}: count=${els.length} tag=${tag} size=${JSON.stringify(size)}`
    }

    console.log(await info('.t-button'))
    console.log(await info('.t-button__content'))
    console.log(await info('.t-tag'))
    console.log(await info('.t-icon'))
    console.log(await info('.tab-bar'))
    console.log(await info('.tab-bar-text'))

    // 看自定义 tabBar 组件是否存在、其内 t-icon 数量
    const tb = await mp.evaluate(() => {
      // 在渲染层无法直接拿 DOM，这里返回页面栈信息
      const pages = getCurrentPages()
      const cur = pages[pages.length - 1]
      return { route: cur ? cur.route : null }
    })
    console.log('page route:', JSON.stringify(tb))
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    await mp.disconnect().catch(() => {})
    console.log('disconnected.')
  }
})()
