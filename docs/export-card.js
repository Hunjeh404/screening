'use strict';
// Entire expanded card, including its border. Exporting does not change the visible card.
async function saveCardJpg(source) {
  if(typeof html2canvas!=='function')throw Error('이미지 저장 도구를 불러오지 못했습니다. 새로고침 후 다시 시도해 주세요.');
  if(document.fonts)await document.fonts.ready;
  const stage=document.createElement('div');stage.className='export-stage';
  stage.id='export-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  stage.style.width=Math.max(300,Math.ceil(source.getBoundingClientRect().width)+4)+'px';
  const clone=source.cloneNode(true);clone.open=true;clone.classList.add('export-card');
  clone.querySelectorAll('[data-html2canvas-ignore]').forEach(el=>el.remove());
  stage.append(clone);document.body.append(stage);
  try {
    // Inline SVGs otherwise lose CSS variables and chart text styles in the image renderer.
    clone.querySelectorAll('svg').forEach(svg=>{
      svg.setAttribute('xmlns','http://www.w3.org/2000/svg');
      [svg,...svg.querySelectorAll('*')].forEach(node=>{
        const style=getComputedStyle(node);
        for(const key of ['fill','stroke','font-family','font-size','font-weight'])node.style.setProperty(key,style.getPropertyValue(key));
      });
    });
    const canvas=await html2canvas(stage,{
      scale:2,backgroundColor:getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(),
      logging:false,useCORS:true,scrollX:0,scrollY:0,
      onclone:doc=>{
        const copy=doc.getElementById(stage.id);
        copy.style.position='absolute';copy.style.left='0';copy.style.top='0';
      }
    });
    const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('JPG 생성에 실패했습니다.')),'image/jpeg',.95));
    const url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=`${(source.dataset.code||'stock').replace(/[^a-zA-Z0-9._-]/g,'_')}-card.jpg`;
    document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  } finally {stage.remove();}
}
