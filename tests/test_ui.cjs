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

// Export both classifications, including missing valuation bases; never export HOLD or targets.
const {buildAnalysisText,quarterValue,growth}=require('../docs/app.js');
const labels=['2024Q3','2024Q4','2025Q1','2025Q2','2025Q3','2025Q4','2026Q1','2026Q2'];
const exportStock={...s,code:'005930',latest_q:'2026Q2',rev_q:true,rev_ttm:true,
 q:{labels,rev:[10,10,10,10,20,20,20,20],op:[-1,-1,-1,-1,2,2,2,2]},rev_ttm_value:80,op_ttm_value:8,
 metrics:{target_1:999},history:{first_entry_date:'2026-10-18'}};
const exportData={meta:{schema_version:2,generated:'2026-10-18 18:00',newest_q:'2026Q2'},stocks:[exportStock,
 {...exportStock,state:'HOLD',code:'DO-NOT-EXPORT'}, {...exportStock,state:'SELL',code:'BROKEN',state_reasons:['수급악화'],per:null}]};
const exported=buildAnalysisText(exportData,'kr');
const exportedRows=exported.split('\n').filter(l=>l.startsWith('{')).map(JSON.parse);
assert.equal(exportedRows.length,2);assert.equal(exportedRows[0].티커,'005930');
assert.equal(exportedRows[1].분류,'고평가');assert.deepEqual(exportedRows[1].분류사유,['수급악화']);
assert.equal(exportedRows[0].TTM매출_전년동기비,'+100.0%');
assert.equal(exportedRows[0].분기영업이익_전년동기비,'흑자전환');
assert.equal(exportedRows[1].가치평가근거,'가치평가 기준 부족');
assert.ok(exported.includes('시장: 한국 | 통화: KRW'));assert.ok(exported.includes('미래의 산업·경제 변화'));
assert.ok(!exported.includes('DO-NOT-EXPORT'));assert.ok(!exported.includes('999'));assert.ok(!exported.includes('stop_price'));
assert.equal(quarterValue({...exportStock,q:{labels:['2026Q2'],rev:[20]}},'rev',0,4),null);
assert.equal(growth(null,10),'미확인');assert.equal(growth(10,0),'전년 0으로 증감률 미산출');
assert.equal(growth(-1,10),'적자전환');assert.equal(growth(-1,-2),'적자지속');
assert.equal(buildAnalysisText({...exportData,meta:{schema_version:1}},'us'),'');
assert.equal(buildAnalysisText({...exportData,stocks:[{state:'HOLD'}]},'us'),'');
assert.ok(buildAnalysisText({...exportData,stocks:[exportStock]},'us').includes('[고평가 기업 목록]\n해당 종목 없음'));
console.log('Analysis prompt, full-state export, dates, currencies and growth checks passed');
