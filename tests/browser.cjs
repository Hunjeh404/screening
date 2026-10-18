// Browser checks use deterministic fixtures; production data and history are never modified.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base={state:'BUY',code:'AAA',name:'샘플 기업',market:'Nasdaq',latest_q:'2026Q2',sector:'정보기술',valuation:'LOW',close:100,ma120:90,ma200:80,ma20:95,per:10,per_med5:15,roe3:20,debt:30,marcap:1e9,dist_ma120:11.1,dist_ma200:25,metrics:{expected_loss:-7,target_1:14,target_2:50,stop_price:93,target_1_price:114,target_2_price:150},q:{labels:['2026Q1','2026Q2'],rev:[100,120],op:[10,20]},history:{first_entry_date:'2026-10-16',entry_price:100,highest_price:100,transitions:[]}};
const data={meta:{schema_version:2,count:7,generated:'2026-10-18 08:00',newest_q:'2026Q2'},stocks:[base,{...base,code:'BBB',name:'큰 기업',marcap:2e9},{...base,code:'CCC',sector:'금융'},{...base,code:'FAIR',state:'HOLD',valuation:'LOW'},{...base,code:'HIGH',state:'SELL',valuation:'LOW',state_reasons:['추세이탈']},{...base,code:'NO-PER-BUY',valuation:null,per:null},{...base,code:'NO-PER-SELL',state:'SELL',valuation:null,per:null}]};
const server=http.createServer((req,res)=>{
  const route=req.url.split('?')[0];
  if(route==='/data.json'||route==='/data_kr.json'){res.setHeader('content-type','application/json');res.end(JSON.stringify(data));return;}
  const p=path.join(__dirname,'../docs',route==='/'?'index.html':route.slice(1));
  if(!fs.existsSync(p)){res.statusCode=404;res.end();return;}
  res.setHeader('content-type',p.endsWith('.js')?'text/javascript':p.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(p));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true});
 try{
 const page=await browser.newPage({viewport:{width:360,height:800},userAgent:'Mozilla/5.0 (Linux; Android 15)'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/`);
 await page.locator('.sector-group').first().waitFor();
 assert.equal(await page.locator('h1').innerText(),'종목 검색기');
 assert.equal(await page.locator('.filters').getAttribute('open'),null);
 assert.equal(await page.locator('.sector-group[open]').count(),0);
 assert.equal(await page.locator('.card[open]').count(),0);
 assert.equal(await page.locator('.card').count(),4);
 assert.equal(await page.locator('.unknown-title').innerText(),'가치평가 기준 부족');
 await page.locator('.filters>summary').click();
 assert.equal(await page.locator('.filters input[type=number]').count(),0);
 assert.ok(await page.locator('.condition-tag').count()>10);
 assert.match(await page.locator('.sector-group>summary').first().innerText(),/정보기술/);
 await page.locator('.sector-group>summary').first().click();
 const first=page.locator('.card').first();
 assert.equal(await first.locator('.ticker').innerText(),'BBB');
 assert.equal(await first.locator('.chips .chip').first().innerText(),'Nasdaq · 2026Q2');
 const heading=await first.evaluate(el=>{const name=el.querySelector('.name').getBoundingClientRect(),ticker=el.querySelector('.ticker').getBoundingClientRect(),row=el.querySelector('.top').getBoundingClientRect();return {sameRow:Math.abs((name.top+name.bottom)/2-(ticker.top+ticker.bottom)/2)<1,right:Math.abs(ticker.right-row.right)<1};});
 assert.deepEqual(heading,{sameRow:true,right:true});
 assert.match(await first.locator('.name').getAttribute('href'),/^intent:/);
 await first.locator('.chips').click();assert.equal(await first.getAttribute('open'),'');
 assert.equal(await first.locator('.returns .m').count(),3);
 assert.equal(await first.locator('svg').count(),2);
 assert.ok(await first.locator('.distances').isVisible());
 await page.evaluate(()=>window.scrollTo(0,250));
 const sticky=await page.locator('.navigation').boundingBox();
 assert.ok(Math.abs(sticky.y)<1);
 assert.ok(await page.locator('h1').evaluate(el=>el.getBoundingClientRect().bottom<0));
 await page.evaluate(()=>window.scrollTo(0,0));
 await first.locator('.chips').click();assert.equal(await first.getAttribute('open'),null);
 await page.locator('[data-value="FAIR"]').click();assert.match(await page.locator('#list').innerText(),/적정가치/);assert.equal(await page.locator('.card').count(),1);
 await page.locator('[data-value="HIGH"]').click();assert.match(await page.locator('#list').innerText(),/고평가/);assert.equal(await page.locator('.card').count(),2);assert.match(await page.locator('#list').textContent(),/가치평가 기준 부족/);
 for(const width of [360,820]){
  await page.setViewportSize({width,height:1000});
  const geometry=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,market:document.querySelector('.markets').getBoundingClientRect().top,value:document.querySelector('.values').getBoundingClientRect().top}));
  assert.equal(geometry.overflow,false);assert.equal(geometry.market,geometry.value);
 }
 await page.locator('#tabKR').click();await page.locator('.sector-group').first().waitFor();
 assert.equal(await page.locator('#tabKR').getAttribute('aria-selected'),'true');
 assert.deepEqual(errors,[]);
 console.log('Mobile/tablet layout, collapse, valuation/status, sorting, Android links and market navigation passed');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1});
