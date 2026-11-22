(()=>{
// IT-chokan glossary search (v1). Filters /glossary/ list by data-k (term, full name, aliases, short).
// NFKC + lowercase + katakana->hiragana. Space = AND. Enter with exactly one hit opens it.
const q=document.getElementById('gq'),count=document.getElementById('gcount'),none=document.getElementById('gnone');
if(!q)return;
const norm=s=>s.normalize('NFKC').toLowerCase().replace(/[ァ-ヶ]/g,c=>String.fromCharCode(c.charCodeAt(0)-0x60));
const items=[...document.querySelectorAll('ul.archive li[data-k]')].map(li=>({li,k:norm(li.dataset.k+' '+li.textContent)}));
const lists=[...document.querySelectorAll('ul.archive')];
let timer=0,hits=[];
function render(){
  const v=q.value.trim(),ws=norm(v).split(/\s+/).filter(Boolean);
  const url=new URL(location.href);
  if(v)url.searchParams.set('q',v);else url.searchParams.delete('q');
  history.replaceState(null,'',url);
  hits=[];
  for(const it of items){const ok=ws.every(w=>it.k.includes(w));it.li.hidden=!ok;if(ok)hits.push(it)}
  for(const ul of lists){const any=[...ul.children].some(li=>!li.hidden);ul.hidden=!any;const h=ul.previousElementSibling;if(h&&h.tagName==='H2')h.hidden=!any}
  if(!v){count.textContent='';none.hidden=true;return}
  count.textContent=hits.length?`${hits.length}語見つかりました`:'';
  if(hits.length){none.hidden=true;return}
  none.textContent='用語集には見つかりませんでした。';
  const a=document.createElement('a');a.href='../search/?q='+encodeURIComponent(v);a.textContent=`記事の全文検索で「${v}」を探す`;
  none.append(a);none.hidden=false;
}
q.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(render,80)});
q.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){render();if(hits.length===1)location.href=hits[0].li.querySelector('a').href}});
const init=new URLSearchParams(location.search).get('q');
if(init){q.value=init;render()}
})();
