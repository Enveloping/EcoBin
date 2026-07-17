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
    await mp.reLaunch('/pages/home/home')
    await sleep(4000)
    const page = await mp.currentPage()
    console.log('route:', page.path)
    // 查 t-button 组件
    const btn = await page.$('.t-button').catch(() => null)
    console.log('has .t-button el:', !!btn)
    if (btn) {
      const cls = await btn.attribute('class').catch(() => null)
      console.log('t-button class attr:', cls)
      const size = await btn.size().catch(() => null)
      console.log('t-button size:', JSON.stringify(size))
      const style = await btn.wxml().catch(() => null)
      console.log('t-button wxml:', (style || '').slice(0, 400))
    }
    // 查 t-button 自定义组件实例
    const comp = await page.$('t-button').catch(() => null)
    console.log('has <t-button> el:', !!comp)
    if (comp) {
      const inner = await comp.wxml().catch(() => null)
      console.log('<t-button> wxml:', (inner || '').slice(0, 600))
    }
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    process.exit(0)
  }
})()
