// Run against the actual portal launcher and a native Streamlit preview.
// NODE_PATH=<Playwright node_modules> CHROMIUM_EXECUTABLE=<optional> node tests/browser/education.cjs
// Start: python -m portal.run run app.py --server.port 18520
// Native: streamlit run app.py --server.port 18521
const assert=require('assert/strict');
const {chromium}=require('playwright');
const base=process.env.EDUCATION_TEST_URL||'http://localhost:18520';
const native=process.env.EDUCATION_NATIVE_URL||'http://localhost:18521';
(async()=>{const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});try{
for(const width of [390,768,1440]){
 const p=await browser.newPage({viewport:{width,height:960}});
 for(const route of ['/education','/education/ai-ed-shorts','/privacy/ai-ed-shorts']){
  const response=await p.goto(base+route);assert.equal(response.status(),200);
  assert.equal(await p.locator('main h1').count(),1);
  assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  assert.equal(await p.locator('footer a').filter({hasText:/^Education$/}).getAttribute('href'),'/education');
  await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.activeElement.textContent),'Skip to content');
  await p.keyboard.press('Enter');assert.equal(await p.evaluate(()=>document.activeElement.id),'education-main');
  if(route!=='/education')assert.equal(await p.locator('main').getByRole('link',{name:'Contact The Stock Sentinel'}).getAttribute('href'),'/Contact');
 }
 await p.goto(base+'/education');await p.getByRole('link',{name:'Explore AI Ed Shorts'}).click();
 await p.getByRole('link',{name:'AI Ed Shorts Privacy Policy'}).last().click();assert.equal(new URL(p.url()).pathname,'/privacy/ai-ed-shorts');
 await p.getByRole('link',{name:'Back to AI Ed Shorts'}).click();
 await p.locator('.ed-crumbs').getByRole('link',{name:'Education',exact:true}).click();
 console.log('PASS public navigation and responsive layout '+width+'px');await p.close();
}
const p=await browser.newPage({viewport:{width:1440,height:960}});let websocket=false;
p.on('websocket',socket=>{if(socket.url().includes('/_stcore/stream'))websocket=true;});
await p.goto(base+'/Home');await p.getByRole('heading',{name:/Finding short-term opportunities/}).waitFor({timeout:60000});
assert(websocket,'Original Streamlit websocket must work');
await p.locator('.st-key-footer_links').getByRole('link',{name:'Education',exact:true}).click();
await p.locator('body.ed-public').waitFor();assert.equal(new URL(p.url()).pathname,'/education');
console.log('PASS Home websocket and same-host Education footer');await p.close();
for(const [route,heading] of [['/Education','Education'],['/AI_Ed_Shorts','AI Ed Shorts'],['/AI_Ed_Shorts_Privacy','AI Ed Shorts Publisher Privacy Policy']]){
 const n=await browser.newPage({viewport:{width:390,height:900}});await n.goto(native+route);
 await n.getByRole('heading',{name:heading,exact:true}).waitFor({timeout:30000});
 assert.equal(await n.locator('[data-testid=stException]').count(),0);
 assert(await n.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(route==='/Education'){await n.getByRole('link',{name:'Explore AI Ed Shorts'}).click();await n.getByRole('heading',{name:'AI Ed Shorts',exact:true}).waitFor();}
 console.log('PASS native public preview '+route);await n.close();
}
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
