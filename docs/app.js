'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, d = 1) => v == null || !Number.isFinite(v) ? '–' : v.toLocaleString('ko-KR', {maximumFractionDigits:d, minimumFractionDigits:d});
const unit = (v, u, k = 1) => v == null ? '–' : num(v,k) + u;
const signed = v => v == null ? '–' : (v > 0 ? '+' : '') + num(v) + '%';
let DATA = null, MKT = 'us', VALUE = 'LOW', requestId = 0;
const FILES = {us:'data.json', kr:'data_kr.json'}, CACHE = {};
const STATES = {BUY:'저평가', HOLD:'적정가치', SELL:'고평가'};
const TAB_STATE = {LOW:'BUY', FAIR:'HOLD', HIGH:'SELL'};
const TAB_LABEL = {LOW:'저평가', FAIR:'적정가치', HIGH:'고평가'};
const hasValuationBasis = s => s.fin
  ? Number.isFinite(s.pbr) && s.pbr > 0
  : Number.isFinite(s.per) && s.per > 0 && Number.isFinite(s.per_med5) && s.per_med5 > 0;
const tabStocks = (stocks, tab) => stocks.filter(s => s.state === TAB_STATE[tab]);
const price = v => v == null ? '–' : (MKT === 'kr' ? num(v,0)+'원' : '$'+num(v,2));
const mil = v => v == null ? '–' : MKT === 'kr' ? num(v/1e8,0)+'억' : '$'+num(v/1e6,0)+'M';
const bil = v => v == null ? '–' : MKT === 'kr' ? (v >= 1e12 ? num(v/1e12)+'조' : num(v/1e8,0)+'억') : '$'+num(v/1e9)+'B';

function tradingViewUrl(ticker, userAgent = navigator.userAgent) {
  const web = 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent(ticker);
  return /Android/i.test(userAgent)
    ? `intent://www.tradingview.com/chart/?symbol=${encodeURIComponent(ticker)}#Intent;scheme=https;package=com.tradingview.tradingviewapp;S.browser_fallback_url=${encodeURIComponent(web)};end`
    : web;
}
function bars(labels = [], vals = [], color = 'var(--accent)') {
  if (!vals.length) return '<p class="note">데이터 없음</p>';
  const W=320,H=100,pad=18,n=vals.length,bw=(W-8)/n,nums=vals.filter(Number.isFinite);
  const max=Math.max(0,...nums),min=Math.min(0,...nums),span=max-min||1;
  const y=v=>pad+(max-v)/span*(H-pad*2);
  let g='';
  vals.forEach((v,i)=>{
    if (!Number.isFinite(v)) return;
    const x=4+i*bw+bw*.15,top=Math.min(y(v),y(0)),h=Math.max(1,Math.abs(y(v)-y(0)));
    g+=`<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${(bw*.7).toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${v<0?'var(--warn)':i===n-1?color:'var(--accent2)'}"><title>${esc(labels[i])}: ${esc(mil(v))}</title></rect>`;
  });
  g+=`<text x="4" y="${H-1}">${esc(labels[0])}</text><text x="${W-4}" y="${H-1}" text-anchor="end">${esc(labels[n-1])}</text><text x="${W-4}" y="10" text-anchor="end">최근 ${esc(mil(vals[n-1]))}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="분기별 실적 그래프">${g}</svg>`;
}
const badge = (text, cls='') => `<span class="chip ${cls}">${esc(text)}</span>`;
const metric = (label,value,cls='',extra='') => `<div class="m"><span>${esc(label)}</span><b class="${cls}">${esc(value)}</b>${extra?`<small>${esc(extra)}</small>`:''}</div>`;
function card(s) {
  const chips=[badge(s.latest_q,'muted')];
  if(s.trend) chips.push(badge(s.trend));
  if(s.rev_q&&s.rev_ttm) chips.push(badge('매출 신고가'));
  if(s.op_q&&s.op_ttm) chips.push(badge('영업이익 신고가'));
  (s.state_reasons||[]).forEach(r=>chips.push(badge(r,s.state.toLowerCase())));
  if(!hasValuationBasis(s)) chips.push(badge('가치평가 기준 부족','muted'));
  if(s.share_warn) chips.push(badge('주식 수 확인 필요','muted'));
  if(s.data_missing) chips.push(badge('수집 지연 · 이전 데이터','sell'));
  const href=esc(tradingViewUrl(s.code)),m=s.metrics||{},q=s.q||{};
  return `<details class="card${s.obv_signal==='매집'?' bull':''}">
    <summary><div class="top"><div class="identity"><a class="name" href="${href}" aria-label="${esc(s.name)} TradingView 열기">${esc(s.name)}</a><a class="ticker" href="${href}">${esc(s.code)} · ${esc(s.market)}</a></div><div class="header-right"><span class="caret" aria-hidden="true">⌄</span><div class="distances">120선 ${signed(s.dist_ma120)}<br>200선 ${signed(s.dist_ma200)}</div></div></div><div class="chips">${chips.join('')}</div></summary>
    <div class="detail"><div class="grid">
      ${metric('현재가',price(s.close))}${metric('ROE 3년',unit(s.roe3,'%'))}${metric(s.fin?'PBR':'부채비율',s.fin?unit(s.pbr,'배',2):unit(s.debt,'%',1))}
      ${metric('PER (5년 중앙)',num(s.per)+' ('+num(s.per_med5)+')')}${metric('52주 고점 대비',unit(s.dd52,'%'))}${metric('시가총액',bil(s.marcap))}
      ${metric('120일선',price(s.ma120))}${metric('200일선',price(s.ma200))}${metric('20일선',price(s.ma20))}${metric('당일 등락률',signed(s.change_pct))}
      ${metric('20일 변동성',unit(s.vol20==null?null:s.vol20*100,'%'))}${metric('60일 변동성',unit(s.vol60==null?null:s.vol60*100,'%'))}
    </div><p class="note">시세 ${esc(s.price_date||'–')}</p>
    <div class="chart-cols"><div><h4>분기 매출</h4>${bars(q.labels,q.rev)}</div><div><h4>분기 영업이익 (금융업은 세전이익)</h4>${bars(q.labels,q.op)}</div></div>
    <div class="grid">${metric('TTM 매출',mil(s.rev_ttm_value))}${metric('직전 TTM 매출',mil(s.prev_rev_ttm))}${metric('TTM 영업이익',mil(s.op_ttm_value))}${metric('직전 TTM 영업이익',mil(s.prev_op_ttm))}${metric('TTM 순이익',mil(s.ni_ttm))}${metric('최근 분기 영업이익',mil(s.latest_op))}</div>
    <div class="returns">${metric('예상 손실률',signed(m.expected_loss),'neg')}${metric('목표 수익률(1차)',signed(m.target_1))}${metric('목표 수익률(2차)',signed(m.target_2))}</div></div>
  </details>`;
}
function sectors(stocks) {
  const groups={};
  stocks.forEach(s=>(groups[s.sector||'미분류']||=[]).push(s));
  return Object.keys(groups).sort((a,b)=>groups[b].length-groups[a].length||a.localeCompare(b,'ko')).map(g=>{
    const sorted=groups[g].sort((a,b)=>(b.marcap??-Infinity)-(a.marcap??-Infinity)||a.code.localeCompare(b.code));
    // 섹터 내 시가총액 내림차순을 유지한다.
    return `<details class="sector-group"><summary>${esc(g)}<span class="sector-count">${sorted.length}종목</span></summary>${sorted.map(card).join('')}</details>`;
  }).join('');
}
function stateSections(stocks) {
  return Object.keys(STATES).map(st=>{
    const rows=stocks.filter(s=>s.state===st);
    return rows.length?`<section><h2 class="state-heading ${st.toLowerCase()}">${STATES[st]} · ${rows.length}종목</h2>${sectors(rows)}</section>`:'';
  }).join('');
}
function render() {
  if(!DATA) return;
  const all=DATA.stocks.filter(s=>STATES[s.state]);
  const selected=tabStocks(all,VALUE),insufficient=selected.filter(s=>!hasValuationBasis(s));
  const withBasis=selected.filter(hasValuationBasis);
  $('list').innerHTML=selected.length
    ? stateSections(withBasis)+(insufficient.length?'<h2 class="unknown-title">가치평가 기준 부족</h2>'+stateSections(insufficient):'')
    : `<div class="empty">${TAB_LABEL[VALUE]}에 해당하는 종목이 없습니다</div>`;
  $('list').querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>e.stopPropagation()));
}
async function load(m) {
  const id=++requestId;MKT=m;DATA=null;
  document.querySelectorAll('[data-market]').forEach(b=>{const on=b.dataset.market===m;b.classList.toggle('on',on);b.setAttribute('aria-selected',String(on));});
  $('banner').innerHTML='';$('list').innerHTML='<div class="empty">불러오는 중…</div>';$('generated').textContent='…';$('quarter').textContent='…';
  try {
    const d=await(CACHE[m]||(CACHE[m]=fetch(FILES[m],{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();})));
    if(id!==requestId)return;
    $('generated').textContent=d.meta.generated;$('quarter').textContent=d.meta.newest_q;
    if(d.meta.schema_version!==2){$('banner').innerHTML='<div class="banner">200일선 및 카드 이력 데이터를 갱신 중입니다. 새 데이터가 생성되면 표시됩니다.</div>';$('list').innerHTML='';return;}
    DATA=d;
    if(d.meta.sample)$('banner').innerHTML='<div class="banner">가상의 샘플 데이터입니다.</div>';
    render();
  }catch(e){delete CACHE[m];if(id===requestId){$('list').innerHTML='';$('banner').innerHTML='<div class="banner">'+(m==='kr'?'한국 데이터를 불러오지 못했습니다.':'데이터를 불러오지 못했습니다.')+' 잠시 후 새로고침해 주세요.</div>';}}
}
if(typeof document!=='undefined'){
  document.querySelectorAll('[data-market]').forEach(b=>b.addEventListener('click',()=>load(b.dataset.market)));
  document.querySelectorAll('[data-value]').forEach(b=>b.addEventListener('click',()=>{
    VALUE=b.dataset.value;document.querySelectorAll('[data-value]').forEach(x=>{const on=x===b;x.classList.toggle('on',on);x.setAttribute('aria-selected',String(on));});render();
  }));load('us');
}
if(typeof module!=='undefined')module.exports={tradingViewUrl,esc,bars,card,sectors,tabStocks,hasValuationBasis};
