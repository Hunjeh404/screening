const assert=require('node:assert/strict');
const {tradingViewUrl,esc,card,priorityGroups,listHtml,tabStocks,hasValuationBasis}=require('../docs/app.js');
assert.match(tradingViewUrl('ABC','Android'),/^intent:\/\//);
assert.ok(tradingViewUrl('ABC','Android').includes('package=com.tradingview.tradingviewapp;'));
assert.ok(tradingViewUrl('ABC','Android').includes('S.browser_fallback_url=https%3A%2F%2F'));
assert.equal(tradingViewUrl('BRK.B','iPhone'),'https://www.tradingview.com/chart/?symbol=BRK.B');
assert.equal(tradingViewUrl('005930','Windows'),'https://www.tradingview.com/chart/?symbol=005930');
assert.equal(esc('<script>'), '&lt;script&gt;');
const s={state:'BUY',code:'ABC',name:'A<script>',market:'NYSE American',latest_q:'2026Q2',valuation:'LOW'};
const html=card(s);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('details class="card" open'));
assert.ok(html.includes('예상 손실률'));assert.ok(html.includes('목표 수익률(2차)'));
const M=(loss,t1)=>({expected_loss:loss,target_1:t1}),G='2026-10-19 08:00';
const fresh=(code,loss,t1,state='BUY')=>({...s,code,state,metrics:M(loss,t1),history:{transitions:[{to:state,date:'2026-10-19'}]}});
const old=(code,loss,t1,age=null,state='BUY')=>({...s,code,state,metrics:M(loss,t1),hunbot:{lc_age:age,sc:false}});
const g=priorityGroups([old('O1',-3,10),fresh('N1',-6,10),old('T2',-2,10,2),fresh('N2',-4,20),old('T0',-7,10,0),old('O2',-3,30),fresh('N3',-4,30)],()=>G);
assert.deepEqual(g.map(([l,r])=>[l,r.map(x=>x.code)]),[['신규',['N3','N2','N1']],['추세 강화',['T0','T2']],['기존',['O2','O1']]]);
const gs=priorityGroups([old('S1',-3,10,0,'SELL')],()=>G);assert.deepEqual(gs[1][1],[]);assert.equal(gs[2][1][0].code,'S1');
const lh=listHtml([{...old('A',-3,10),per:5,per_med5:10,sector:'금융'},{...old('B',-3,10),per:null}]);
assert.ok(lh.indexOf('>기존<')<lh.indexOf('데이터 부족'));
const ln=listHtml([{...fresh('N',-3,10),per:5,per_med5:10}],()=>G);assert.ok(ln.includes('<details class="sector-group" open>'));assert.ok(!lh.includes('sector-group" open'));assert.ok(lh.includes('>금융<'));
console.log('UI link / escaping / collapse / sorting checks passed');

// Old valuation fields may still be present in a batch started before this update.
const states=[
 {state:'BUY',code:'BUY-HIGH-PER',per:100,per_med5:10,valuation:'HIGH'},
 {state:'HOLD',code:'HOLD-LOW-PER',per:2,per_med5:10,valuation:'LOW'},
 {state:'SELL',code:'SELL-NO-PER',per:null,per_med5:null,valuation:null},
 {state:'BUY',code:'BUY-NO-PER',per:null,per_med5:null,valuation:null}
];
assert.deepEqual(tabStocks(states,'LOW').map(s=>s.code),['BUY-HIGH-PER','BUY-NO-PER']);
assert.deepEqual(tabStocks(states,'FAIR').map(s=>s.code),['HOLD-LOW-PER']);
assert.deepEqual(tabStocks(states,'HIGH').map(s=>s.code),['SELL-NO-PER']);
assert.equal(hasValuationBasis(states[0]),true);assert.equal(hasValuationBasis(states[2]),false);
assert.ok(!card({...s,per:null}).includes('가치평가 기준'));
assert.ok(!card({...s,per:null}).includes('>적자<'));
assert.ok(card({...s,ni_ttm:-1}).includes('>적자<'));
for(const age of [0,1,2]) assert.ok(card({...s,hunbot:{lc_age:age,sc:false}}).includes(`▲(D+${age})`));
for(const age of [null,-1,3,4]) assert.ok(!card({...s,hunbot:{lc_age:age}}).includes('▲'));
assert.ok(!card({...s,hunbot:{lc_age:1,sc:true}}).includes('▲'));
assert.ok(!card({...s,data_missing:true,hunbot:{lc_age:1}}).includes('▲'));
const both=card({...s,hunbot:{super_bull:true,panic:true}});
assert.ok(both.includes('data-tag="초강세"'));assert.ok(both.includes('class="chip tag-info sell" data-tag="패닉셀"'));
assert.ok(card({...s,state_reasons:['패닉셀']}).includes('>투매<'));
const detail=card({...s,close:1.95,ma120:1.93,ma200:2.11,change_pct:-2.5,dist_ma120:1,dist_ma200:-7.4});
assert.match(detail,/현재가 \$1.95<span class="neg">\(-2.5%\)/);
assert.match(detail,/120일선 <span class="pos">\$1.93/);
assert.match(detail,/200일선 <span class="neg">\$2.11/);
assert.equal((detail.split('<div class="grid">')[1].split('<div class="chart-cols">')[0].match(/class="m(?: |")/g)||[]).length,6);
console.log('State-based tabs and signal / loss badge / quote formatting checks passed');

assert.ok(!detail.includes('+1.0%'));assert.ok(!detail.includes('-7.4%'));
assert.ok(detail.indexOf('class="distances"')<detail.indexOf('</summary>'));
assert.ok(detail.includes('기준정보'));assert.ok(!detail.includes('trend-metric'));
const labels=card({...s,trend:'상승추세',rev_q:true,rev_ttm:true,op_q:true,op_ttm:true});
assert.ok(labels.includes('data-tag="상승우위"'));assert.ok(labels.includes('data-tag="매출"'));assert.ok(labels.includes('data-tag="영익"'));
assert.ok(!labels.includes('NYSE American · 2026Q2'));assert.ok(labels.includes('share-card'));
