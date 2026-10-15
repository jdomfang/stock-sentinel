// Offline real-page layout regression. No credentials or paid services.
// NODE_PATH=<playwright node_modules> node tests/browser/mobile_layout.cjs
// BROWSERS=chromium,webkit; ARTIFACTS=/tmp/ss-mobile-review; PYTHON=python3
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert/strict');
const {spawn}=require('child_process');const engines=require('playwright');
const repo=process.env.REVIEW_REPO||path.resolve(__dirname,'../..');
const output=process.env.ARTIFACTS||'/tmp/ss-mobile-review';fs.mkdirSync(output,{recursive:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
const low=[{Ticker:'OGN','Company Name':'Organon & Co. Common Stock','Overall Sentiment':'Unknown',Mentions:4,Evidence:1},
{Ticker:'HALO','Company Name':'Halozyme Therapeutics Inc. Common Stock with a deliberately long company name','Overall Sentiment':'Unknown',Mentions:1,Evidence:1}];
const scored={Ticker:'DUK','Company Name':'Duke Energy','Overall Sentiment':'Neutral',Mentions:4,Evidence:4};
async function fixture(kind,port){
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ss-mobile-'));fs.mkdirSync(path.join(dir,'pages'));
 fs.copyFileSync(path.join(__dirname,kind==='home'?'mobile_home_app.py':'scan_feedback_app.py'),path.join(dir,'app.py'));
 for(const n of fs.readdirSync(path.join(repo,'pages')).filter(n=>n.endsWith('.py')))fs.writeFileSync(path.join(dir,'pages',n),'# offline');
 if(kind==='home')fs.copyFileSync(path.join(__dirname,'scan_feedback_app.py'),path.join(dir,'pages/Discovery.py'));
 const child=spawn(process.env.PYTHON||'python3',['-m','streamlit','run',path.join(dir,'app.py'),'--server.port',String(port),'--server.address','127.0.0.1','--server.headless','true','--browser.gatherUsageStats','false'],{cwd:repo,env:{...process.env,SCAN_FEEDBACK_REPO:repo,SCAN_FEEDBACK_CONTROL:dir,MOBILE_REVIEW_REPO:repo,MOBILE_REVIEW_CONTROL:dir},stdio:'ignore'});
 for(let i=0;i<100;i++){try{if((await fetch(`http://127.0.0.1:${port}/_stcore/health`)).ok)return {dir,child,url:`http://127.0.0.1:${port}`}}catch{}await pause(100)}throw Error('fixture startup');
}
async function inspectRows(p){
 for(const ticker of ['OGN','HALO'])await p.locator('.st-key-scan_row_'+ticker+' .scan-stock-cell, .st-key-scan_row_selected_'+ticker+' .scan-stock-cell').waitFor({state:'visible'});
 assert(await p.locator('[class*="st-key-scan_row_"]').count()>=2,'Expected populated result cards');
 const issues=await p.locator('[class*="st-key-scan_row_"]').evaluateAll(rows=>{
  const problems=[];
  for(const row of rows){
   const cells=[...row.querySelectorAll('.scan-stock-cell,.scan-meta-cell,.stButton button,.scan-view-result')];
   for(let i=0;i<cells.length;i++){
    const a=cells[i].getBoundingClientRect();
    if(cells[i].scrollWidth>cells[i].clientWidth+2)problems.push('clipped cell '+cells[i].textContent);
    if(a.left<0||a.right>innerWidth+1)problems.push('outside viewport');
    for(let j=i+1;j<cells.length;j++){const b=cells[j].getBoundingClientRect();if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)problems.push('overlap '+cells[i].textContent+' / '+cells[j].textContent)}
   }
  }return problems;
 });assert.deepEqual(issues,[]);
}
(async()=>{let home,scan;try{
 const port=Number(process.env.PORT||18520);
 home=await fixture('home',port);scan=await fixture('scan',port+1);
 for(const engine of (process.env.BROWSERS||'chromium,webkit').split(',')){
 const browser=await engines[engine].launch({executablePath:engine==='webkit'?process.env.WEBKIT_EXECUTABLE:undefined,args:engine==='chromium'?['--no-sandbox']:[]});
 try{
 for(const width of (process.env.WIDTHS||"320,375,390,430,720,1024,1440").split(",").map(Number)){
  const mobile=width<=720;
  for(const signed_in of (process.env.ONLY_DELIVERED==='1'?[]:[false,true])){
   fs.writeFileSync(path.join(home.dir,'case.json'),JSON.stringify({signed_in,admin:signed_in}));
   const p=await browser.newPage({viewport:{width,height:900},isMobile:mobile,hasTouch:mobile});
   await p.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
   await p.goto(home.url);await p.getByRole('heading',{name:'Sector Pulse',exact:true}).waitFor();
   assert.equal(await p.locator('[data-testid=stException]').count(),0);
   const cta=signed_in?p.getByRole('link',{name:'Open Market Scan',exact:true}):p.getByRole('button',{name:'Start with 2 free credits',exact:true});
   assert(await cta.isVisible());
   assert(await p.evaluate(()=>document.querySelector('[data-testid="stMain"]').scrollWidth<=innerWidth+1),JSON.stringify(await p.evaluate(()=>[...document.querySelectorAll('a,button')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>({text:e.textContent,right:e.getBoundingClientRect().right})))));
   if(signed_in){for(const name of ['Market Scan','Deep Analyze','Admin']){const links=p.getByRole('link',{name,exact:true});assert(await links.count()>0);assert(await links.evaluateAll(es=>es.some(e=>e.getBoundingClientRect().width>0)));}}
   if(width<=650&&signed_in)assert(await cta.evaluate(e=>getComputedStyle(e).borderTopStyle==='solid'&&e.getBoundingClientRect().height>=44));
   if(width===390||width===1440)await p.screenshot({path:path.join(output,`${engine}-home-${width}-${signed_in}.png`),fullPage:true});
   assert(!fs.existsSync(path.join(home.dir,'network-attempts.log')),'Home attempted external network');
   if(width===390){
    const explore=p.getByRole('button',{name:'Explore Healthcare →',exact:true});
    if(!await explore.isVisible())await p.getByText('View all 10 sectors',{exact:true}).click();
    await explore.tap();
    if(signed_in){
     await p.getByRole('button',{name:'Run Healthcare scan · 1 credit',exact:true}).waitFor();
     assert(!fs.existsSync(path.join(home.dir,'events.jsonl')),'Home handoff charged credits');
    }else await p.waitForURL('**/Auth');
   }
   await p.close();
  }
  for(const mode of (process.env.ONLY_DELIVERED==='1'?[]:['low','mixed'])){
   fs.writeFileSync(path.join(scan.dir,'case.json'),JSON.stringify({intent:'healthcare',rows:mode==='low'?low:[scored,...low]}));
   fs.rmSync(path.join(scan.dir,'events.jsonl'),{force:true});fs.rmSync(path.join(scan.dir,'release'),{force:true});
   const record=process.env.RECORD==='1'&&width===390&&mode==='low';
   const p=await browser.newPage({viewport:{width,height:900},isMobile:mobile,hasTouch:mobile,...(record?{recordVideo:{dir:output,size:{width,height:900}}}:{})});
   await p.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
   await p.goto(scan.url);const run=p.getByRole('button',{name:'Run Healthcare scan · 1 credit',exact:true});await run.waitFor();
   assert(!fs.existsSync(path.join(scan.dir,'events.jsonl')),'Navigation must not debit');
   await run.click();await p.locator('.st-key-discovery_scan_progress').waitFor();
   // A second touch hits the blocking surface, not another scan control.
   if(mobile)await p.touchscreen.tap(20,450);
   if(record)await pause(1800);
   fs.writeFileSync(path.join(scan.dir,'release'),'1');await p.locator('#ss-scan-results').waitFor();
   await p.waitForFunction(()=>{const e=document.querySelector('#ss-scan-results');const r=e?.getBoundingClientRect();return e===document.activeElement&&r.top>=0&&r.bottom<innerHeight;});
   if(mode==='mixed')await p.getByText('Needs more evidence (2)',{exact:true}).click();
   await inspectRows(p);
   assert.equal(await p.locator('[data-testid=stException]').count(),0);
   const events=fs.readFileSync(path.join(scan.dir,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse);
   assert.equal(events.filter(e=>e.phase==='debit').length,1);assert.equal(events.filter(e=>e.phase==='remote').length,1);
   if(width===390||width===1440){
     await p.screenshot({path:path.join(output,`${engine}-results-${width}-${mode}.png`),fullPage:true});
     await p.locator('.st-key-scan_row_OGN').screenshot({path:path.join(output,`${engine}-card-${width}-${mode}.png`)});
   }
   if(record)await pause(1800);
   if(width===390){await p.addStyleTag({content:'html{font-size:20px!important}'});await inspectRows(p);}
   const video=record?p.video():null;await p.close();if(video)await video.saveAs(path.join(output,`${engine}-mobile-scan.webm`));
  }
  if(process.env.DELIVERED==='1'){
   fs.writeFileSync(path.join(scan.dir,'case.json'),JSON.stringify({delivered:true,rows:low}));
   fs.rmSync(path.join(scan.dir,'events.jsonl'),{force:true});
   const p=await browser.newPage({viewport:{width,height:900},isMobile:mobile,hasTouch:mobile});
   await p.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
   await p.goto(scan.url);await p.locator('.st-key-selected_analysis_panel').waitFor();
   assert.equal(await p.locator('[data-testid=stException]').count(),0);
   await inspectRows(p);
   assert(!fs.existsSync(path.join(scan.dir,'events.jsonl')),'Saved analysis must not charge');
   assert(await p.evaluate(()=>document.querySelector('[data-testid="stMain"]').scrollWidth<=innerWidth+1));
   await p.locator('.st-key-selected_analysis_panel').screenshot({path:path.join(output,`${engine}-analysis-${width}.png`)});
   await p.close();
   console.log(`PASS ${engine} ${width}px: delivered analysis layout without debit`);
  }
  if(process.env.ONLY_DELIVERED!=='1')console.log(`PASS ${engine} ${width}px: public/signed-in Home, intent, single debit, low/mixed result geometry`);
 }
 }finally{await browser.close()}
 }
}finally{for(const f of [home,scan])if(f){fs.writeFileSync(path.join(f.dir,'release'),'1');f.child.kill();fs.rmSync(f.dir,{recursive:true,force:true})}}})().catch(e=>{console.error(e);process.exitCode=1});
