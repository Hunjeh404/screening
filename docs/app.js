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
function isNewState(s, generated = DATA?.meta?.generated) {
  const latest = s.history?.transitions?.at(-1);
  return Boolean(!s.data_missing && STATES[s.state] && generated && latest &&
    latest.to === s.state && latest.date === generated.slice(0,10));
}
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
const badge = (text, cls='', reasons=[]) => `<button type="button" class="chip tag-info ${cls}" data-tag="${esc(text)}" data-reasons="${esc(JSON.stringify(reasons))}" aria-label="${esc(text)} 태그 설명">${esc(text)}</button>`;
const metric = (label,value,cls='',extra='') => `<div class="m"><span>${esc(label)}</span><b class="${cls}">${esc(value)}</b>${extra?`<small>${esc(extra)}</small>`:''}</div>`;
const signClass = v => Number.isFinite(v) ? v>0?'pos':v<0?'neg':'neutral' : 'neutral';
function tagReasons(s,tag) {
  const h=s.hunbot?.details||{},q=s.q||{},last=values=>values?.[values.length-1];
  const ma=[`현재가 ${price(s.close)}`,`120일선 ${price(s.ma120)} · 200일선 ${price(s.ma200)}`];
  const losses=[`TTM 순이익 ${mil(s.ni_ttm)}`,`TTM 영업이익 ${mil(s.op_ttm_value)}`];
  const vol=`20일 변동성 ${unit(s.vol20==null?null:s.vol20*100,'%')} · 60일 ${unit(s.vol60==null?null:s.vol60*100,'%')}`;
  const opTrend=[`분기 영업이익 ${mil(s.prev2_op)} → ${mil(s.prev_op)} → ${mil(s.latest_op)}`];
  const fscoreLine=`F-스코어 ${s.fscore??'–'}/${s.fscore_max??'–'}점 (통과 ${({9:7,6:5})[s.fscore_max]??'–'}점)`;
  const totals=[`TTM 매출 ${mil(s.prev_rev_ttm)} → ${mil(s.rev_ttm_value)}`,`TTM 영업이익 ${mil(s.prev_op_ttm)} → ${mil(s.op_ttm_value)}`];
  switch(tag) {
    case '상승우위': return ['현재가가 120일선·200일선 이상',...ma,`PER ${num(s.per)}배 · 적자 턴어라운드 조건 제외`];
    case '턴어라운드': {
      const lines=[];
      if(s.close>=s.ma120&&s.close<s.ma200)lines.push('120일선 회복 · 200일선 아래',...ma);
      const opLoss=[s.op_ttm_value,s.op_ttm_year_ago].some(v=>Number.isFinite(v)&&v<0);
      if(opLoss&&s.rev_q&&s.rev_ttm)lines.push('TTM 영업적자 또는 1년 전 영업적자 · 분기 및 TTM 매출 신고가',...losses,`1년 전 TTM 영업이익 ${mil(s.op_ttm_year_ago)}`,...opTrend,fscoreLine);
      return lines.length?lines:['편입 당시 턴어라운드 조건 충족',...ma];
    }
    case '매출': return ['최근 단일 분기·TTM 매출 모두 과거 최고',`${s.latest_q||'최근 분기'} 매출 ${mil(last(q.rev))}`,`최근 4분기 매출 합계 ${mil(s.rev_ttm_value)}`];
    case '영익': return [`최근 단일 분기·TTM ${s.fin?'세전':'영업'}이익 모두 과거 최고`,`${s.latest_q||'최근 분기'} ${s.fin?'세전':'영업'}이익 ${mil(s.latest_op??last(q.op))}`,`최근 4분기 합계 ${mil(s.op_ttm_value)}`];
    case '적자': return ['최근 4분기 합산 순이익 또는 영업이익이 음수',...losses];
    case '추세이탈': return ['두 장기 이평선의 98%를 모두 하회',...ma,`이탈 기준 ${price(s.ma120==null?null:s.ma120*.98)} · ${price(s.ma200==null?null:s.ma200*.98)}`];
    case '수급악화': return ['끝난 주봉 52주 OBV 다이버전스 이탈',`현재 판정: ${s.obv_signal||'자료 없음'}`,`확보 주봉 ${s.wk?.c?.length??0}개`];
    case '투매': return ['변동성 확대 + 20일선 하회 + 당일 −7% 이하',vol,`현재가 ${price(s.close)} · 20일선 ${price(s.ma20)}`,`당일 등락률 ${signed(s.change_pct)}`];
    case '피크아웃': return ['TTM 매출·영업이익 동반 감소',...totals];
    case '적자전환': {
      const lines=[];
      const special=s.fin||s.history?.entry_turnaround||([s.op_ttm_value,s.op_ttm_year_ago].some(v=>Number.isFinite(v)&&v<0)&&s.rev_q&&s.rev_ttm);
      if(s.fin&&s.roe3!=null&&s.roe3<8)lines.push(`금융업 3년 평균 ROE ${unit(s.roe3,'%')} < 8%`);
      if(s.history?.entry_op_loss&&s.rev_q===false)lines.push('영업적자로 편입한 종목의 분기 매출 신고가 실패',`최근 분기 매출 ${mil(last(q.rev))}`);
      if(!special){
        if(s.latest_op!=null&&s.latest_op<0)lines.push(`최근 분기 영업이익 ${mil(s.latest_op)} < 0`);
        if(s.roe3!=null&&s.roe3<12)lines.push(`3년 평균 ROE ${unit(s.roe3,'%')} < 12%`);
        if(s.debt!=null&&s.debt>70)lines.push(`부채비율 ${unit(s.debt,'%')} > 70%`);
      }
      return lines.length?lines:[`최근 분기 영업이익 ${mil(s.latest_op)}`,`ROE ${unit(s.roe3,'%')} · 부채비율 ${unit(s.debt,'%')}`];
    }
    case '상승중': return ['변동성 확대·유지 중 20일선 이상',vol,`현재가 ${price(s.close)} · 20일선 ${price(s.ma20)}`];
    case '적정가치': return s.fin?['금융업 PBR > 1배',`PBR ${unit(s.pbr,'배',2)}`]:['PER이 5년 중앙값 이상',`PER ${num(s.per)} · 5년 중앙값 ${num(s.per_med5)}`];
    case '눌림목': return ['변동성 확대·유지 중 20일선 하회',vol,`현재가 ${price(s.close)} · 20일선 ${price(s.ma20)}`,`당일 등락률 ${signed(s.change_pct)} · 고평가 전환 조건 미충족`];
    case '지지 테스트': return ['두 장기 이평선 아래 · 적어도 한 선의 −2% 이내',...ma];
    case '실적유지': return ['분기 신고가 조건 일부 미충족 · TTM 동반 감소는 아님',`분기 매출 신고가 ${s.rev_q?'충족':'미충족'} · 영업이익 신고가 ${s.op_q?'충족':'미충족'}`,...totals];
    case '재무보합': return [s.fin?'금융업 ROE 8% 이상·10% 미만':'ROE 12% 이상·15% 미만 또는 부채비율 50% 초과·70% 이하',`3년 평균 ROE ${unit(s.roe3,'%')}`,`부채비율 ${unit(s.debt,'%')}`];
    case '영익 개선 미달': return ['적자 턴어라운드: 분기 영업이익 2분기 연속 개선 필요',...opTrend];
    case 'F-스코어 미달': return ['적자 턴어라운드: F-스코어 통과 점수 필요',fscoreLine,
      ...Object.entries(s.fscore_items||{}).map(([k,v])=>`${v===true?'○':v===false?'×':'–'} ${k}`)];
    case '이익 감소': return ['최근 4분기 '+(s.fin?'세전':'영업')+'이익이 1년 전보다 10% 넘게 감소','매출총이익이 늘었으면 투자로 인한 감익으로 보고 예외',
      `${s.fin?'세전':'영업'}이익(4분기 합계) ${mil(s.op_ttm_year_ago)} → ${mil(s.op_ttm_value)}`,`매출총이익(4분기 합계) ${mil(s.gp_ttm_year_ago)} → ${mil(s.gp_ttm_value)}`];
    case '세전이익 적자': return ['금융업: 최근 4분기 세전이익 흑자 필요',`세전이익(4분기 합계) ${mil(s.op_ttm_value)}`];
    case '흑자 연수 미달': return ['일반 기업: 연속 영업흑자 5년 이상 필요',`연속 흑자 ${s.profit_years??'–'}년`];
    case '매수조건 미충족': return ['과거 편입 이력 있음 · 현재 저평가 조건 일부 미충족','고평가 전환 사유 없음',`최근 분기 영업이익 ${mil(s.prev_op)} → ${mil(s.latest_op)}`,`ROE ${unit(s.roe3,'%')} · PER ${num(s.per)} · 중앙값 ${num(s.per_med5)}`];
    case '주식 수 확인 필요': return ['주식 수 누락·시차 또는 가치지표 이상으로 계산 제한',`최근 재무분기 ${s.latest_q||'–'}`,`시가총액 ${bil(s.marcap)}`];
    case '수집 지연 · 이전 데이터': return ['최신 수집 실패로 직전 유효 데이터 유지',`마지막 정상 수집 ${s.history?.last_seen||'–'}`];
    default: return ['해당 조건 충족',`최근 재무분기 ${s.latest_q||'–'}`];
  }
}
const signalAge = s => {
  const h=s.hunbot||{};
  return !s.data_missing && !h.sc && Number.isInteger(h.lc_age) && h.lc_age>=0 && h.lc_age<3 ? h.lc_age : null;
};
const lossKey = s => Number.isFinite(s.metrics?.expected_loss) ? Math.abs(s.metrics.expected_loss) : Infinity;
const targetKey = s => Number.isFinite(s.metrics?.target_1) ? s.metrics.target_1 : -Infinity;
// 손절률 낮은 순, 1차 목표 수익률 높은 순
const byPriority = (a,b) => (lossKey(a)-lossKey(b)) || (targetKey(b)-targetKey(a)) || String(a.code).localeCompare(String(b.code));
// 신규 -> 추세 강화(▲가 뜬 기존 카드, 고평가 제외, 신호 최근 순) -> 기존. gen(s): 그 종목 시장의 갱신 시각
function priorityGroups(stocks, gen = () => DATA?.meta?.generated) {
  const fresh=[],trend=[],rest=[];
  stocks.forEach(s=>(isNewState(s,gen(s))?fresh:s.state!=='SELL'&&signalAge(s)!=null?trend:rest).push(s));
  const lk=v=>Number.isFinite(v)?v:Infinity;
  return [['신규',fresh.sort(byPriority)],['추세 강화',trend.sort((a,b)=>lk(signalAge(a))-lk(signalAge(b))||byPriority(a,b))],['기존',rest.sort(byPriority)]];
}
function card(s, generated) {
  const chips=[],h=s.hunbot||{},add=(label,cls='')=>chips.push(badge(label,cls,tagReasons(s,label)));
  chips.push(badge(s.sector||'미분류','',['야후 기준 섹터 분류']));
  if(s.trend)add(s.trend==='상승추세'?'상승우위':s.trend);
  if(s.rev_q&&s.rev_ttm)add('매출');
  if(s.op_q&&s.op_ttm)add('영익');
  (s.state_reasons||[]).forEach(r=>add(r==='패닉셀'?'투매':r,s.state.toLowerCase()));
  if([s.ni_ttm,s.op_ttm_value].some(v=>Number.isFinite(v)&&v<0))add('적자','sell');
  if(s.share_warn)add('주식 수 확인 필요','muted');
  if(s.data_missing)add('수집 지연 · 이전 데이터','sell');
  const href=esc(tradingViewUrl(s.code)),m=s.metrics||{},q=s.q||{};
  const signal=signalAge(s)!=null ? `<span class="buy-signal">▲(D+${h.lc_age})</span> ` : '';
  const maQuote=(label,value)=>`<span class="quote-item">${label} <span class="${signClass(Number.isFinite(s.close)&&Number.isFinite(value)?s.close-value:null)}">${price(value)}</span></span>`;
  const newMark=isNewState(s,generated)?'<span class="new-marker" aria-label="신규" title="최신 갱신일에 이 분류로 신규 편입">·</span>':'';
  return `<details class="card${s.obv_signal==='매집'?' bull':''}" data-code="${esc(s.code)}">
    <summary><div class="top"><a class="name" href="${href}" aria-label="${esc(s.name)}${newMark?' 신규':''} TradingView 열기"><span class="name-text">${esc(s.name)}</span>${newMark}</a><div class="ticker-actions"><a class="ticker" href="${href}">${signal}${esc(s.code)}</a><button type="button" class="share-card" aria-label="${esc(s.code)} 카드 JPG 저장" title="카드 JPG 저장" data-html2canvas-ignore><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.7 7.6-4.4m-7.6 7 7.6 4.4"/></svg></button></div></div><div class="chips">${chips.join('')}</div>
    <div class="distances"><span class="quote-item">현재가 ${price(s.close)}<span class="${signClass(s.change_pct)}">(${signed(s.change_pct)})</span></span> · ${maQuote('120일선',s.ma120)} · ${maQuote('200일선',s.ma200)}</div></summary>
    <div class="detail"><div class="grid">
      ${metric('ROE 3년',unit(s.roe3,'%'))}${metric(s.fin?'PBR':'부채비율',s.fin?unit(s.pbr,'배',2):unit(s.debt,'%',1))}
      ${metric('PER (5년 중앙)',num(s.per)+' ('+num(s.per_med5)+')')}${metric('52주 고점 대비',unit(s.dd52,'%'))}${metric('시가총액',bil(s.marcap))}
      ${metric('기준정보',s.latest_q||'–')}
    </div>
    <div class="chart-cols"><div><h4>분기 매출</h4>${bars(q.labels,q.rev)}</div><div><h4>분기 영업이익 (금융업은 세전이익)</h4>${bars(q.labels,q.op)}</div></div>
    <div class="returns">${[['예상 손실률',m.expected_loss,'neg'],['목표 수익률(1차)',m.target_1,''],['목표 수익률(2차)',m.target_2,'']]
      .map(([l,v,c])=>`<div class="m"><span>${l}</span><b class="${c}">${esc(signed(v))}</b><small class="cost-price" data-pct="${Number.isFinite(v)?v:''}" data-html2canvas-ignore></small></div>`).join('')}</div>
    <label class="cost-row" data-html2canvas-ignore>매입단가<input type="number" inputmode="decimal" min="0" step="any" class="cost-input" placeholder="가격 입력" aria-label="${esc(s.code)} 매입단가"></label></div>
  </details>`;
}
function groupSection(label, stocks) {
  return stocks.length?`<details class="sector-group"${label==='신규'?' open':''}><summary>${esc(label)}<span class="sector-count">${stocks.length}종목</span></summary>${stocks.map(s=>card(s)).join('')}</details>`:'';
}
function listHtml(stocks, gen) {
  const basis=stocks.filter(hasValuationBasis),lack=stocks.filter(s=>!hasValuationBasis(s));
  return priorityGroups(basis,gen).map(([label,rows])=>groupSection(label,rows)).join('')
    +groupSection('가치평가 불가',priorityGroups(lack,gen).flatMap(([,rows])=>rows));
}
function showTagDialog(title,lines) {
  const dialog=$('tag-dialog');
  $('tag-title').textContent=title;
  $('tag-reasons').replaceChildren(...lines.map(line=>{const li=document.createElement('li');li.textContent=line;return li;}));
  dialog.showModal();
}
function render() {
  if(!DATA) return;
  const selected=tabStocks(DATA.stocks.filter(s=>STATES[s.state]),VALUE);
  $('list').innerHTML=selected.length?listHtml(selected):`<div class="empty">${TAB_LABEL[VALUE]}에 해당하는 종목이 없습니다</div>`;
  $('list').querySelectorAll('a').forEach(a=>a.addEventListener('click',e=>e.stopPropagation()));
  $('list').querySelectorAll('.tag-info').forEach(button=>button.addEventListener('click',event=>{
    event.preventDefault();event.stopPropagation();
    showTagDialog(button.dataset.tag,JSON.parse(button.dataset.reasons));
  }));
  $('list').querySelectorAll('.cost-input').forEach(input=>input.addEventListener('input',()=>{
    const cost=parseFloat(input.value);
    input.closest('.detail').querySelectorAll('.cost-price').forEach(el=>{
      const pct=parseFloat(el.dataset.pct);
      el.textContent=cost>0&&Number.isFinite(pct)?price(cost*(1+pct/100)):'';
    });
  }));
  $('list').querySelectorAll('.share-card').forEach(button=>button.addEventListener('click',async event=>{
    event.preventDefault();event.stopPropagation();
    if(button.disabled)return;
    button.disabled=true;button.setAttribute('aria-busy','true');
    try{await saveCardJpg(button.closest('.card'));}
    catch(error){showTagDialog('이미지 저장 실패',[error.message||'다시 시도해 주세요.']);}
    finally{button.disabled=false;button.removeAttribute('aria-busy');}
  }));
}
const getData = m => CACHE[m]||(CACHE[m]=fetch(FILES[m],{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();}).catch(e=>{delete CACHE[m];throw e;}));
// 요약 이미지: 미국·한국을 섞어 저평가(신규 -> 추세 강화 -> 기존 -> 가치평가 불가), 맨 아래 고평가 신규(가치평가 불가은 뒤에)
async function summaryHtml() {
  const sets=(await Promise.allSettled(['us','kr'].map(getData))).map((r,i)=>r.status==='fulfilled'&&r.value.meta?.schema_version===2?[['us','kr'][i],r.value]:null).filter(Boolean);
  if(!sets.length)throw Error('데이터를 불러오지 못했습니다.');
  const mk=new Map(),gm=new Map(),all=[];
  sets.forEach(([m,d])=>d.stocks.forEach(s=>{if(!STATES[s.state])return;mk.set(s,m);gm.set(s,d.meta.generated);all.push(s);}));
  const gen=s=>gm.get(s),keep=MKT,keepData=DATA;
  const draw=s=>{MKT=mk.get(s);return card(s,gen(s));};
  try{
    const head=label=>`<h2 class="summary-title">${esc(label)}</h2>`,sub=(label,rows)=>rows.length?`<h3 class="summary-sub">${esc(label)} ${rows.length}</h3>${rows.map(draw).join('')}`:'';
    const flat=rows=>priorityGroups(rows,gen).flatMap(([,r])=>r),buyAll=all.filter(s=>s.state==='BUY');
    const buy=priorityGroups(buyAll.filter(hasValuationBasis),gen).map(([l,r])=>sub(l,r)).join('')
      +sub('가치평가 불가',flat(buyAll.filter(s=>!hasValuationBasis(s))));
    const newSell=all.filter(s=>s.state==='SELL'&&isNewState(s,gen(s)));
    const sell=[...newSell.filter(hasValuationBasis).sort(byPriority),...newSell.filter(s=>!hasValuationBasis(s)).sort(byPriority)];
    const generated=sets.map(([,d])=>d.meta.generated).sort().at(-1);
    const qqq=$('qqq')&&!$('qqq').hidden?`<span class="${esc($('qqq').className)}">${esc($('qqq').textContent)}</span>`:'';
    return `<div class="summary"><div class="title-row"><h1>종목 검색기</h1><span class="chip muted">${esc(generated)}</span>${qqq}</div>
      ${head('저평가')}${buy||'<p class="note">해당 종목 없음</p>'}${sell.length?head('고평가 신규')+sell.map(draw).join(''):''}
      <p class="note disclaimer">투자 판단의 책임은 본인에게 있습니다</p></div>`;
  }finally{MKT=keep;DATA=keepData;}
}
async function load(m) {
  const id=++requestId;MKT=m;DATA=null;
  document.querySelectorAll('[data-market]').forEach(b=>{const on=b.dataset.market===m;b.classList.toggle('on',on);b.setAttribute('aria-selected',String(on));});
  $('banner').innerHTML='';$('list').innerHTML='<div class="empty">불러오는 중…</div>';$('generated').textContent='…';
  try {
    const d=await getData(m);
    if(id!==requestId)return;
    $('generated').textContent=d.meta.generated;
    if(d.meta.schema_version!==2){$('banner').innerHTML='<div class="banner">200일선 및 카드 이력 데이터를 갱신 중입니다. 새 데이터가 생성되면 표시됩니다.</div>';$('list').innerHTML='';return;}
    DATA=d;
    if(d.meta.sample)$('banner').innerHTML='<div class="banner">가상의 샘플 데이터입니다.</div>';
    render();
  }catch(e){delete CACHE[m];if(id===requestId){$('list').innerHTML='';$('banner').innerHTML='<div class="banner">'+(m==='kr'?'한국 데이터를 불러오지 못했습니다.':'데이터를 불러오지 못했습니다.')+' 잠시 후 새로고침해 주세요.</div>';}}
}
function qqqChip(q) {
  if(!q||!Number.isFinite(q.close)||!Number.isFinite(q.ma200)||q.ma200<=0)return null;
  const gap=(q.close/q.ma200-1)*100,usd=v=>'$'+num(v,2);
  return {text:`QQQ ${signed(gap)}`,cls:gap<0?'sell':'',
    lines:[`QQQ ${usd(q.close)} (${signed(q.change_pct)})`,`200일선 ${usd(q.ma200)}`,`200일선 대비 ${signed(gap)}`,`기준일 ${q.date||'–'}`]};
}
if(typeof document!=='undefined'){
  $('share-all').addEventListener('click',async()=>{
    const b=$('share-all');if(b.disabled)return;b.disabled=true;b.setAttribute('aria-busy','true');
    try{await saveHtmlJpg(await summaryHtml(),'summary-'+new Date().toISOString().slice(0,10)+'.jpg');}
    catch(error){showTagDialog('이미지 저장 실패',[error.message||'다시 시도해 주세요.']);}
    finally{b.disabled=false;b.removeAttribute('aria-busy');}
  });
  fetch('qqq.json',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(q=>{
    const c=qqqChip(q),el=$('qqq');if(!c)return;
    el.textContent=c.text;el.className='chip '+c.cls;el.hidden=false;el.setAttribute('aria-label',c.text+' 설명');
    el.onclick=()=>showTagDialog('QQQ',c.lines);
  }).catch(()=>{});
  $('tag-close').addEventListener('click',()=>$('tag-dialog').close());
  $('tag-dialog').addEventListener('click',event=>{if(event.target===$('tag-dialog')){
    const r=event.target.getBoundingClientRect();
    if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();
  }});
  document.querySelectorAll('[data-market]').forEach(b=>b.addEventListener('click',()=>load(b.dataset.market)));
  document.querySelectorAll('[data-value]').forEach(b=>b.addEventListener('click',()=>{
    VALUE=b.dataset.value;document.querySelectorAll('[data-value]').forEach(x=>{const on=x===b;x.classList.toggle('on',on);x.setAttribute('aria-selected',String(on));});render();
  }));load('us');
}
if(typeof module!=='undefined')module.exports={qqqChip,tradingViewUrl,esc,bars,card,listHtml,priorityGroups,byPriority,tabStocks,hasValuationBasis,tagReasons,isNewState};
