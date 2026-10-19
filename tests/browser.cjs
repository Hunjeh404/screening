// Browser checks use deterministic fixtures; production data and history are never modified.
const {chromium}=require('playwright');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const base={state:'BUY',code:'AAA',name:'샘플 기업',market:'Nasdaq',latest_q:'2026Q2',sector:'정보기술',trend:'상승추세',rev_q:true,rev_ttm:true,op_q:true,op_ttm:true,valuation:'LOW',close:100,ma120:90,ma200:80,ma20:95,per:10,per_med5:15,roe3:20,debt:30,marcap:1e9,dist_ma120:11.1,dist_ma200:25,metrics:{expected_loss:-7,target_1:14,target_2:50,stop_price:93,target_1_price:114,target_2_price:150},q:{labels:['2026Q1','2026Q2'],rev:[100,120],op:[10,20]},history:{first_entry_date:'2026-10-16',entry_price:100,highest_price:100,transitions:[]}};
const data={meta:{schema_version:2,count:7,generated:'2026-10-18 08:00',newest_q:'2026Q2'},stocks:[base,{...base,code:'BBB',name:'큰 기업',marcap:2e9,change_pct:-2.5,ni_ttm:-1,hunbot:{lc_age:1,sc:false,super_bull:true,panic:true,details:{sma5:95,bb_basis:95,prev_bb_basis:94,stoch20_ema2:55,prev_stoch20_ema2:54,panic_rsi:25}}},{...base,code:'CCC',sector:'금융'},{...base,code:'FAIR',state:'HOLD',valuation:'LOW'},{...base,code:'HIGH',state:'SELL',valuation:'LOW',state_reasons:['추세이탈']},{...base,code:'NO-PER-BUY',valuation:null,per:null},{...base,code:'NO-PER-SELL',state:'SELL',valuation:null,per:null}]};
const server=http.createServer((req,res)=>{
  const route=req.url.split('?')[0];
  if(route==='/qqq.json'){res.setHeader('content-type','application/json');res.end(JSON.stringify({date:'2026-10-16',close:500,change_pct:-1.2,ma200:480}));return;}
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
 assert.match(await page.locator('.sector-group>summary').last().innerText(),/데이터 부족/);
 await page.locator('.filters>summary').click();
 assert.equal(await page.locator('.filters input[type=number]').count(),0);
 assert.ok(await page.locator('.condition-tag').count()>10);
 assert.match(await page.locator('.sector-group>summary').first().innerText(),/추세 강화/);
 await page.locator('.sector-group>summary').first().click();
 const first=page.locator('.card').first();
 assert.equal(await first.locator('.ticker').innerText(),'▲(D+1) BBB');
 assert.equal(await first.locator('.chips .chip').first().innerText(),'정보기술');
 assert.equal(await first.locator('.chips .chip').nth(1).innerText(),'상승우위');
 assert.ok(!(await first.locator('.chips').innerText()).includes('Nasdaq'));
 assert.ok(await first.locator('.distances').isVisible());
 assert.ok(!(await first.locator('.distances').innerText()).includes('11.1%'));
 await first.locator('[data-tag="초강세"]').click();
 assert.equal(await first.getAttribute('open'),null);
 assert.equal(await page.locator('#tag-title').innerText(),'초강세');
 assert.match(await page.locator('#tag-reasons').innerText(),/55.0/);
 await page.locator('#tag-close').click();
 await first.locator('[data-tag="패닉셀"]').click();
 assert.match(await page.locator('#tag-reasons').innerText(),/25.0/);
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#tag-dialog').getAttribute('open'),null);
 const heading=await first.evaluate(el=>{const name=el.querySelector('.name').getBoundingClientRect(),ticker=el.querySelector('.ticker-actions').getBoundingClientRect(),row=el.querySelector('.top').getBoundingClientRect();return {sameRow:Math.abs((name.top+name.bottom)/2-(ticker.top+ticker.bottom)/2)<1,right:Math.abs(ticker.right-row.right)<1};});
 assert.deepEqual(heading,{sameRow:true,right:true});
 assert.match(await first.locator('.name').getAttribute('href'),/^intent:/);
 await first.locator('.distances').click();assert.equal(await first.getAttribute('open'),'');
 assert.equal(await first.locator('.returns .m').count(),3);
 await page.locator('#qqq:not([hidden])').waitFor();
 assert.equal(await page.locator('#qqq').innerText(),'QQQ +4.2%');
 assert.equal(await page.locator('#qqq').evaluate(el=>getComputedStyle(el).color),'rgb(95, 224, 194)');
 await page.locator('#qqq').click();
 assert.equal(await page.locator('#tag-title').innerText(),'QQQ');
 assert.match(await page.locator('#tag-reasons').innerText(),/QQQ \$500\.00 \(-1\.2%\)\s*200일선 \$480\.00\s*200일선 대비 \+4\.2%/);
 await page.locator('#tag-close').click();
 assert.equal(await page.locator('#quarter').count(),0);
 assert.equal(await first.locator('.cost-price').first().innerText(),'');
 await first.locator('.cost-input').fill('100');
 const prices=await first.locator('.cost-price').allInnerTexts();
 const pcts=await first.locator('.cost-price').evaluateAll(els=>els.map(el=>parseFloat(el.dataset.pct)));
 assert.deepEqual(prices,pcts.map(p=>'$'+(100*(1+p/100)).toLocaleString('ko-KR',{minimumFractionDigits:2,maximumFractionDigits:2})));
 assert.equal(await first.locator('.grid .m').count(),6);
 assert.match(await first.locator('.grid .m').last().innerText(),/기준정보\s*2026Q2/);
 assert.equal(await first.locator('.trend-metric').count(),0);
 assert.equal(await first.locator('.buy-signal').evaluate(el=>getComputedStyle(el).color),'rgb(230, 57, 70)');
 assert.equal(await first.locator('.quote-item .neg').first().evaluate(el=>getComputedStyle(el).color),'rgb(239, 145, 133)');
 assert.equal(await first.locator('.quote-item .pos').first().evaluate(el=>getComputedStyle(el).color),'rgb(95, 224, 194)');
 assert.ok((await first.locator('.chips').innerText()).includes('적자'));
 for(const width of [360,820]) {
  await page.setViewportSize({width,height:800});
  const grid=await first.locator('.grid .m').evaluateAll(els=>els.map(el=>({x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y})));
  assert.equal(new Set(grid.map(p=>p.x)).size,3);assert.equal(new Set(grid.map(p=>p.y)).size,2);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 await page.setViewportSize({width:360,height:800});
 assert.equal(await first.locator('.chart-cols svg').count(),2);
 assert.ok(await first.locator('.distances').isVisible());
 await page.evaluate(()=>window.scrollTo(0,250));
 const sticky=await page.locator('.navigation').boundingBox();
 assert.ok(Math.abs(sticky.y)<1);
 assert.ok(await page.locator('h1').evaluate(el=>el.getBoundingClientRect().bottom<0));
 await page.evaluate(()=>window.scrollTo(0,0));
 await first.locator('.distances').click();assert.equal(await first.getAttribute('open'),null);
 const downloadPromise=page.waitForEvent('download');
 await first.locator('.share-card').click();
 const download=await downloadPromise;
 assert.equal(download.suggestedFilename(),'BBB-card.jpg');
 const jpeg=fs.readFileSync(await download.path());
 assert.equal(jpeg[0],0xff);assert.equal(jpeg[1],0xd8);
 const picture=await page.evaluate(async encoded=>{
  const blob=await(await fetch('data:image/jpeg;base64,'+encoded)).blob(),img=await createImageBitmap(blob);
  const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;
  const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);const pixels=ctx.getImageData(0,0,img.width,img.height).data;
  let green=0,border=0;
  for(let i=0;i<pixels.length;i+=4){if(pixels[i+1]>120&&pixels[i+1]>pixels[i]*1.25)green++;if(Math.abs(pixels[i]-39)<12&&Math.abs(pixels[i+1]-66)<12&&Math.abs(pixels[i+2]-58)<12)border++;}
  return {width:img.width,height:img.height,green,border};
 },jpeg.toString('base64'));
 assert.ok(picture.width>=600);assert.ok(picture.height>800);assert.ok(picture.green>100);assert.ok(picture.border>100);
 assert.equal(await first.getAttribute('open'),null);
 assert.equal(await page.locator('.export-stage').count(),0);
 console.log('JPG full-card download:',picture);

 await page.locator('[data-value="FAIR"]').click();assert.equal(await page.locator('.state-heading').count(),0);assert.equal(await page.locator('.card').count(),1);
 await page.locator('[data-value="HIGH"]').click();assert.equal(await page.locator('.state-heading').count(),0);assert.equal(await page.locator('.card').count(),2);assert.match(await page.locator('#list').textContent(),/데이터 부족/);
 for(const width of [360,820]){
  await page.setViewportSize({width,height:1000});
  const geometry=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,market:document.querySelector('.markets').getBoundingClientRect().top,value:document.querySelector('.values').getBoundingClientRect().top}));
  assert.equal(geometry.overflow,false);assert.equal(geometry.market,geometry.value);
 }
 const sharePromise=page.waitForEvent('download');
 await page.locator('#share-all').click();
 const shared=await sharePromise;
 assert.match(shared.suggestedFilename(),/^summary-\d{4}-\d{2}-\d{2}\.jpg$/);
 const sj=fs.readFileSync(await shared.path());assert.equal(sj[0],0xff);assert.equal(sj[1],0xd8);
 assert.equal(await page.locator('.export-stage').count(),0);
 await page.locator('#tabKR').click();await page.locator('.sector-group').first().waitFor();
 assert.equal(await page.locator('#tabKR').getAttribute('aria-selected'),'true');
 assert.deepEqual(errors,[]);
 console.log('Mobile/tablet layout, collapse, valuation/status, sorting, Android links and market navigation passed');
 }finally{await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1});
