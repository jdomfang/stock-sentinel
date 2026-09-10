// Offline viewport regression. Requires Playwright and its Chromium browser.
// Run: NODE_PATH=<node_modules containing playwright> node tests/browser/scan_feedback.cjs
// Optional: CHROMIUM_EXECUTABLE, PYTHON (defaults to python3).
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert/strict');
const {spawn} = require('child_process');
const {chromium} = require('playwright');
const repo = path.resolve(__dirname, '../..');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scan-feedback-'));
fs.copyFileSync(path.join(__dirname, 'scan_feedback_app.py'), path.join(dir, 'app.py'));
fs.mkdirSync(path.join(dir, 'pages'));
// Native navigation needs registered pages; never execute production page modules here.
for (const name of fs.readdirSync(path.join(repo, 'pages')).filter(n => n.endsWith('.py'))) {
  fs.writeFileSync(path.join(dir, 'pages', name), '# Offline navigation placeholder\n');
}
const port = 18518;
const server = spawn(process.env.PYTHON || 'python3', ['-m', 'streamlit', 'run',
  path.join(dir, 'app.py'), '--server.port', String(port), '--server.headless', 'true',
  '--browser.gatherUsageStats', 'false'], {cwd: repo, env: {...process.env,
    SCAN_FEEDBACK_REPO: repo, SCAN_FEEDBACK_CONTROL: dir}, stdio: 'ignore'});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      try {ready = (await fetch(`http://localhost:${port}/_stcore/health`)).ok;} catch {}
      if (ready) break;
      await pause(100);
    }
    assert(ready, 'Offline server did not start');
    browser = await chromium.launch({executablePath: process.env.CHROMIUM_EXECUTABLE,
      args: ['--no-sandbox']});
    for (const [width, sector, mode] of [[1440,'utilities','success'],
      [1440,'communication','success'], [390,'communication','success'],
      [1440,'utilities','displaced'], [1440,'utilities','empty'], [1440,'utilities','refused'], [1440,'utilities','failure']]) {
      fs.writeFileSync(path.join(dir, 'case.json'), JSON.stringify({mode}));
      for (const f of ['started','release']) fs.rmSync(path.join(dir,f), {force:true});
      const page = await browser.newPage({viewport:{width,height:900},reducedMotion:width===390?'reduce':'no-preference'});
      await page.goto(`http://localhost:${port}`);
      await page.getByRole('heading',{name:'Sector Pulse',exact:true}).waitFor();
      if (sector === 'communication') await page.getByText('View all 10 sectors',{exact:true}).click();
      await page.locator(`.st-key-ss_pulse_row_discovery_${sector} button`).first().click();
      const card = page.locator('.st-key-discovery_scan_progress');
      await card.getByText(`Scanning recent discussion for ${sector} momentum…`,{exact:true}).waitFor();
      assert(fs.existsSync(path.join(dir,'started')), 'Must paint before debit completes');
      assert(await card.evaluate(e => {
        const r=e.getBoundingClientRect();
        return getComputedStyle(e).position === 'fixed' && r.top>=0 && r.bottom<=innerHeight
          && r.left>=0 && r.right<=innerWidth
          && [...e.querySelectorAll('.stMarkdown,[data-testid=stMarkdownContainer],.ss-processing-state')]
            .every(child => child.scrollWidth<=child.clientWidth+1)
          && [...e.querySelectorAll('[data-testid="stProgress"], .element-container')]
            .every(child => {const c=child.getBoundingClientRect();return c.left>=r.left && c.right<=r.right;});
      }), 'Processing card must fit the viewport, including its contents');
      assert(await page.evaluate(() => !!document.elementFromPoint(5,innerHeight/2)
        ?.closest('.st-key-discovery_scan_progress')), 'Busy surface catches repeat pointer clicks');
      if (mode === 'displaced') await page.mouse.wheel(0,180);
      fs.writeFileSync(path.join(dir,'release'),'1');
      await card.waitFor({state:'detached'});
      assert.equal(await page.locator('[data-testid=stException]').count(),0);
      const target = ['success','displaced'].includes(mode) ? '#ss-scan-results' : '#ss-scan-outcome';
      await page.waitForTimeout(600);
      if (mode === 'displaced') {
        await page.locator('#ss-scan-completion-link').waitFor();
        assert.notEqual(await page.evaluate(() => document.activeElement?.id),'ss-scan-results');
        await page.locator('#ss-scan-completion-link').click();
      }
      await page.waitForFunction(selector => {
        const e=document.querySelector(selector);const r=e?.getBoundingClientRect();
        return e && document.activeElement===e && r.top>=0 && r.top<innerHeight;
      },target);
      if (mode === 'success') {
        await page.locator('#ss-back-to-pulse').click();
        await page.waitForFunction(() => document.activeElement?.textContent==='Sector Pulse');
        await page.getByText('How the pulse is measured',{exact:true}).click();
        await page.waitForTimeout(350);
        assert.notEqual(await page.evaluate(() => document.activeElement?.id),'ss-scan-results');
      }
      await page.close();
      console.log(`PASS ${width}px ${sector}: ${mode}`);
    }
    fs.writeFileSync(path.join(dir,'case.json'), JSON.stringify({credits:0}));
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    await page.goto(`http://localhost:${port}`);
    const button = page.locator('.st-key-ss_pulse_row_discovery_utilities button').first();
    await button.waitFor();
    assert(await button.isDisabled());
    for (const hover of [false,true]) {
      if (hover) await button.hover({force:true});
      assert(await button.evaluate(e => Number(getComputedStyle(e).opacity)<.6
        && getComputedStyle(e).color===getComputedStyle(e.querySelector('p')).color));
    }
    console.log('PASS zero-credit disabled styling, including hover');
  } finally {
    fs.writeFileSync(path.join(dir,'release'),'1');
    if (browser) await browser.close();
    server.kill();
    fs.rmSync(dir,{recursive:true,force:true});
  }
})().catch(error => {console.error(error);process.exitCode=1;});
