/* eslint-disable */
const automator = require('miniprogram-automator')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
;(async () => {
  const ws = process.env.WS_ENDPOINT || 'ws://127.0.0.1:9420'
  let mp
  for (let i = 0; i < 30; i++) { try { mp = await automator.connect({ wsEndpoint: ws }); break } catch (e) { await sleep(2000) } }
  if (!mp) { console.error('连接失败'); process.exit(1) }
  console.log('connected.')
  mp.on('console', (m) => {
    try {
      const args = (m.args || []).map(a => (a && a.value !== undefined) ? a.value : JSON.stringify(a)).join(' ')
      console.log('[CONSOLE.' + (m.type || '?') + ']', args)
    } catch (e) { console.log('[CONSOLE raw]', JSON.stringify(m).slice(0, 300)) }
  })
  mp.on('exception', (e) => {
    console.log('[EXCEPTION]', (e && (e.value || e.message || JSON.stringify(e))) || '')
  })
  try {
    await mp.reLaunch('/pages/home/home')
    await sleep(5000)
    console.log('done waiting')
  } catch (e) {
    console.error('ERR:', e && e.stack ? e.stack : e)
  } finally {
    process.exit(0)
  }
})()
