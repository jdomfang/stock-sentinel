// Offline real-page regression: edit a ticker and submit once, without blur.
// PLAYWRIGHT_MODULE may name playwright or playwright-core; PYTHON selects env.
const fs=require('fs'),path=require('path'),os=require('os'),assert=require('assert/strict');
const {spawn}=require('child_process');
const engines=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=process.env.REVIEW_REPO||path.resolve(__dirname,'../..');
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ss-deep-submit-'));
const artifacts=process.env.ARTIFACTS||'/tmp/ss-deep-submit-review';fs.mkdirSync(artifacts,{recursive:true});
fs.mkdirSync(path.join(dir,'pages'));
for(const n of fs.readdirSync(path.join(repo,'pages')).filter(n=>n.endsWith('.py')))fs.writeFileSync(path.join(dir,'pages',n),'# offline');
fs.copyFileSync(path.join(__dirname,'deep_submit_app.py'),path.join(dir,'app.py'));
const port=Number(process.env.PORT||18562);
const server=spawn(process.env.PYTHON||'python3',['-m','streamlit','run',path.join(dir,'app.py'),'--server.port',String(port),'--server.address','127.0.0.1','--server.headless','true','--browser.gatherUsageStats','false'],{cwd:repo,env:{...process.env,DEEP_REVIEW_REPO:repo,DEEP_REVIEW_CONTROL:dir},stdio:'ignore'});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const events=()=>fs.existsSync(path.join(dir,'events.jsonl'))?fs.readFileSync(path.join(dir,'events.jsonl'),'utf8').trim().split('\n').filter(Boolean).map(JSON.parse):[];
async function waitEvent(phase){for(let i=0;i<150;i++){if(events().some(e=>e.phase===phase))return;await pause(50)}throw Error('Missing '+phase+' event')}
(async()=>{let browser;try{
 for(let i=0;i<100;i++){try{if((await fetch(`http://127.0.0.1:${port}/_stcore/health`)).ok)break}catch{}await pause(100)}
 const engine=process.env.BROWSER||'chromium';
 browser=await engines[engine].launch({executablePath:process.env.BROWSER_EXECUTABLE,args:engine==='chromium'?['--no-sandbox']:[]});
 const cases=[[390,'success'],[820,'success'],[1024,'success'],[1440,'success'],...[ 'failure','empty','refused','unconfigured','debit_exception','stop','refund_failure','zero','blank','prefill'].map(m=>[820,m])];
 for(const [width,mode] of (process.env.SMOKE==='1'?cases.slice(0,1):cases)){
  for(const f of ['events.jsonl','release-debit','release-remote'])fs.rmSync(path.join(dir,f),{force:true});
  fs.writeFileSync(path.join(dir,'case.json'),JSON.stringify({mode,credits:mode==='zero'?0:2,...(mode==='prefill'?{prefill:'GME'}:{})}));
  const p=await browser.newPage({viewport:{width,height:1180},hasTouch:width<1440});
  await p.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await p.goto(`http://127.0.0.1:${port}`);
  const input=p.getByRole('textbox',{name:'Ticker',exact:true});await input.waitFor();
  const submit=p.getByRole('button',{name:'Analyze · 1 credit',exact:true});
  assert.equal(await p.locator('[data-testid=stException]').count(),0);
  assert.equal(await submit.isDisabled(),mode==='zero');
  assert(await p.evaluate(()=>document.querySelector('[data-testid=stMain]').scrollWidth<=innerWidth+1));
  if(mode==='zero'||mode==='prefill'){
   if(mode==='prefill')assert.equal(await input.inputValue(),'GME');
   await pause(250);assert.equal(events().length,0);await p.close();console.log(`PASS ${engine} ${width}px ${mode}: no charge`);continue;
  }
  await input.fill(mode==='blank'?' ':'GME');
  // Return must not spend a credit; the paid action remains an explicit tap.
  await input.press('Enter');await pause(150);assert.equal(events().length,0);
  if(mode==='success')await submit.screenshot({path:path.join(artifacts,`${engine}-${width}-button.png`)});
  // Leave a fresh uncommitted edit immediately before the single tap.
  await input.fill(mode==='blank'?' ':'gme');
  if(width<1440)await submit.tap();else await submit.click();
  if(['blank','unconfigured'].includes(mode)){
   const text=mode==='blank'?'Please enter a stock ticker.':'Deep Analyze is temporarily unavailable';
   await p.getByText(text,{exact:true}).waitFor();assert.equal(events().length,0);
  }else{
   await waitEvent('debit');
   const surface=p.locator('.st-key-deep_analysis_progress');
   await surface.getByText('Preparing secure analysis…',{exact:true}).waitFor();
   assert.equal(events().filter(e=>e.phase==='remote').length,0,'Must paint before debit returns');
   assert(await surface.evaluate(e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth}));
   if(mode==='success'){
    await p.screenshot({path:path.join(artifacts,`${engine}-${width}-progress.png`)});
    // Repeated pointer taps must land on the busy surface, not submit again.
    assert(await p.evaluate(()=>!!document.elementFromPoint(5,innerHeight/2)?.closest('.st-key-deep_analysis_progress')));
    if(width<1440)await p.touchscreen.tap(5,600);else await p.mouse.click(5,600);
   }
   fs.writeFileSync(path.join(dir,'release-debit'),'1');
   if(!['refused','debit_exception','stop'].includes(mode)){
    await waitEvent('remote');fs.writeFileSync(path.join(dir,'release-remote'),'1');
   }
   await surface.waitFor({state:'detached'});
   if(mode==='success'){
    await p.getByText('Offline test evidence.',{exact:true}).waitFor();
    await waitEvent('complete');
    assert.equal(events().filter(e=>e.phase==='remote').length,1);
    await p.screenshot({path:path.join(artifacts,`${engine}-${width}-result.png`)});
   }else if(['failure','empty','refund_failure'].includes(mode))await waitEvent('refund');
   assert.equal(events().filter(e=>e.phase==='debit').length,1);
  }
  assert.equal(await p.locator('[data-testid=stException]').count(),mode==='debit_exception'?1:0);
  assert.equal(await p.locator('.st-key-deep_analysis_progress').count(),0);
  assert.equal(events().filter(e=>e.phase==='blocked_network').length,0);
  await p.close();console.log(`PASS ${engine} ${width}px ${mode}: one submission, feedback, cleanup`);
 }
}finally{
 fs.writeFileSync(path.join(dir,'release-debit'),'1');fs.writeFileSync(path.join(dir,'release-remote'),'1');
 if(browser)await browser.close();server.kill();fs.rmSync(dir,{recursive:true,force:true});
}})().catch(e=>{console.error(e);process.exitCode=1});
