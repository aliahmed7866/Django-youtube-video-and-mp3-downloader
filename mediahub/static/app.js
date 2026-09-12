'use strict';
const $ = s => document.querySelector(s);
const token = $('meta[name="csrf-token"]').content;
let jobs = [], filter = 'all', previous = '';
const active = j => ['queued','downloading','processing','cancelling'].includes(j.status);
async function api(url, method='GET', data) {
  const response = await fetch(url, {method, headers:{'Content-Type':'application/json','X-CSRF-Token':token}, body:data ? JSON.stringify(data):undefined});
  if (response.status === 204) return;
  const body = await response.json().catch(()=>({error: response.status === 403 ? 'Session expired. Refresh the page.' : 'Request failed. Try again.'}));
  if (!response.ok) throw new Error(body.error || 'Request failed.');
  return body;
}
function node(tag, text, cls) {const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function render() {
  const search=$('#search').value.toLowerCase();
  const visible=jobs.filter(j=>(filter==='all'||(filter==='active'?active(j):j.status===filter)) && (j.title+' '+j.url).toLowerCase().includes(search));
  const signature=JSON.stringify(visible);
  $('#count').textContent=jobs.length;
  if(signature===previous)return;
  previous=signature;
  $('#jobs').replaceChildren();$('#empty').hidden=visible.length>0;
  $('#empty h3').textContent=jobs.length?'No downloads match this view.':'A little space for your favourites.';
  $('#empty p').textContent=jobs.length?'Try a different filter or search.':'Paste a YouTube link above to save your first video or track.';
  for(const j of visible){
    const card=node('article','','job');card.append(node('div',j.kind==='audio'?'♫':'▷','job-icon'));
    const content=node('div','');content.append(node('h3',j.title||j.url),node('p',`${j.kind==='audio'?'MP3':'MP4'} · ${j.quality}${j.kind==='audio'?' kbps':'p max'} · ${new Date(j.created*1000).toLocaleDateString()}`));
    content.append(node('p',j.status==='complete'?'Ready to save':j.status==='processing'?'Finishing your file…':j.status==='downloading'?`Downloading · ${Math.round(j.progress)}%`:j.status[0].toUpperCase()+j.status.slice(1),'status'));
    if(active(j)){const progress=document.createElement('progress');progress.max=100;if(j.status==='downloading')progress.value=j.progress;progress.setAttribute('aria-label','Download progress');content.append(progress);}
    if(j.error)content.append(node('p',j.error));card.append(content);
    const actions=node('div','','actions');
    if(j.status==='complete'){const a=node('a','Save file');a.href=`/api/jobs/${j.id}/file`;actions.append(a);}
    const addAction=(label,action,method='POST')=>{const b=node('button',label);b.onclick=async()=>{if(action===''&&!confirm('Remove this download and its saved file?'))return;b.disabled=true;try{await api(`/api/jobs/${j.id}${action}`,method);await refresh();}catch(e){$('#message').textContent=e.message;b.disabled=false;}};actions.append(b);};
    if(active(j)&&j.status!=='cancelling')addAction('Cancel','/cancel');
    if(['failed','cancelled'].includes(j.status))addAction('Retry','/retry');
    if(['complete','failed','cancelled'].includes(j.status))addAction('Remove','','DELETE');
    card.append(actions);$('#jobs').append(card);
  }
}
async function refresh(){try{const data=await api('/api/jobs');jobs=data.jobs;$('#storage').textContent=`${(data.free_bytes/1024**3).toFixed(1)} GB free on device`;$('#connection').textContent='';render();}catch(e){$('#connection').textContent='Cannot reach Media Hub. Check the service in your admin hub.';}}
$('form').addEventListener('submit',async e=>{e.preventDefault();$('#submit').disabled=true;try{await api('/api/jobs','POST',{url:$('#url').value,kind:$('input[name=kind]:checked').value,quality:$('#quality').value});$('#url').value='';$('#message').textContent='Added to your download queue.';await refresh();}catch(e){$('#message').textContent=e.message;}finally{$('#submit').disabled=false;}});
for(const radio of document.querySelectorAll('input[name=kind]'))radio.addEventListener('change',()=>{const audio=radio.value==='audio';$('#quality').replaceChildren();for(const [value,label] of audio?[['128','128 kbps · Small'],['192','192 kbps · Balanced'],['320','320 kbps · High']]:[['360','360p · Small'],['480','480p · Standard'],['720','720p · HD'],['1080','1080p · Full HD']]){const option=node('option',label);option.value=value;option.selected=value===(audio?'192':'720');$('#quality').append(option);}});
$('#paste').onclick=async()=>{try{$('#url').value=await navigator.clipboard.readText();}catch(e){$('#url').focus();$('#message').textContent='Press and hold the link field, then choose Paste.';}};
for(const button of document.querySelectorAll('[data-filter]'))button.onclick=()=>{filter=button.dataset.filter;for(const b of document.querySelectorAll('[data-filter]'))b.setAttribute('aria-pressed',String(b===button));render();};
$('#search').addEventListener('input',render);
async function poll(){if(!document.hidden)await refresh();setTimeout(poll,2500);}poll();
