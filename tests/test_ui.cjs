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
assert.ok(card({...s,per:null}).includes('가치평가 기준 부족'));
console.log('State-based tabs and insufficient-basis badge checks passed');
