const assert=require('node:assert/strict');
const {tradingViewUrl,esc,card,sectors,tabStocks,hasValuationBasis}=require('../docs/app.js');
assert.match(tradingViewUrl('ABC','Android'),/^intent:\/\//);
assert.ok(tradingViewUrl('ABC','Android').includes('package=com.tradingview.tradingviewapp;'));
assert.ok(tradingViewUrl('ABC','Android').includes('S.browser_fallback_url=https%3A%2F%2F'));
assert.equal(tradingViewUrl('BRK.B','iPhone'),'https://www.tradingview.com/chart/?symbol=BRK.B');
assert.equal(tradingViewUrl('005930','Windows'),'https://www.tradingview.com/chart/?symbol=005930');
assert.equal(esc('<script>'), '&lt;script&gt;');
const s={state:'BUY',code:'ABC',name:'A<script>',market:'NYSE American',latest_q:'2026Q2',valuation:'LOW'};
const html=card(s);assert.ok(!html.includes('<script>'));assert.ok(!html.includes('details class="card" open'));
assert.ok(html.includes('예상 손실률'));assert.ok(html.includes('목표 수익률(2차)'));
const groups=sectors([{...s,sector:'A',marcap:2},{...s,code:'DEF',sector:'A',marcap:4},{...s,sector:'Z',marcap:5}]);
assert.ok(groups.indexOf('>A<')<groups.indexOf('>Z<'));assert.ok(groups.indexOf('DEF')<groups.indexOf('ABC'));
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
assert.ok(both.includes('class="pos">초강세'));assert.ok(both.includes('class="neg">패닉셀'));
assert.ok(card({...s,state_reasons:['패닉셀']}).includes('>투매<'));
const detail=card({...s,close:1.95,ma120:1.93,ma200:2.11,change_pct:-2.5,dist_ma120:1,dist_ma200:-7.4});
assert.match(detail,/현재가 \$1.95<span class="neg">\(-2.5%\)/);
assert.match(detail,/120일선 \$1.93<span class="pos">\(\+1.0%\)/);
assert.match(detail,/200일선 \$2.11<span class="neg">\(-7.4%\)/);
assert.equal((detail.split('<div class="grid">')[1].split('<div class="chart-cols">')[0].match(/class="m(?: |")/g)||[]).length,6);
console.log('State-based tabs and signal / loss badge / quote formatting checks passed');
