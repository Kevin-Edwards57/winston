const state={leads:[],filtered:[],selected:null,selectedIndex:-1,dashboard:null,jobId:null};
const $=id=>document.getElementById(id);

function toast(message,error=false){const el=$('toast');el.textContent=message;el.className='toast show'+(error?' error':'');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.className='toast',3200)}
async function api(url,options={}){const response=await fetch(url,options);const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||`Request failed (${response.status})`);return data}
function requestJSON(method,body){return{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}}
function money(value){return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(value||0)}
function safeURL(value){try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)?url.href:''}catch{return''}}
function stage(lead){return lead.workflow_stage||'draft'}

async function loadDashboard(){
  const data=await api('/api/dashboard');state.dashboard=data;
  $('metric-contacts').textContent=data.metrics.contacts.toLocaleString();
  $('metric-drafts').textContent=data.metrics.drafts_ready;
  $('metric-sent').textContent=data.metrics.emails_sent;
  $('metric-approved').textContent=data.metrics.approved;
  $('metric-queued').textContent=data.metrics.queued;
  $('metric-social').textContent=data.metrics.social_leads;
  $('ai-mode').textContent=data.ai.mode.toUpperCase();$('top-ai-mode').textContent=data.ai.mode.toUpperCase();
  const eligible=data.ai.providers.filter(p=>p.eligible);$('primary-provider').textContent=eligible[0]?.name==='gemini'?'Gemini (Free)':eligible[0]?.name||'Unavailable';
  $('fallback-provider').textContent=eligible[1]?.name||'Not configured';
  const cost=Object.values(data.ai.usage).reduce((sum,row)=>sum+(row.estimated_cost_usd||0),0);$('ai-cost').textContent=money(cost);
  const working=['scanning','drafting_existing'].includes(data.scan_status);$('online-label').textContent=data.scan_status==='drafting_existing'?'LOCAL DRAFTING':data.scan_status==='scanning'?'GOOGLE SCANNING':'ONLINE';$('nav-live').classList.toggle('active',working);
  renderEvents(data.events);
}

async function loadLeads(keepSelection=true){
  const data=await api('/leads');const previous=keepSelection&&state.selected?.draft_id;state.leads=data.leads||[];
  state.leads.forEach((lead,index)=>lead._index=index);applyFilters();
  if(previous){const match=state.leads.find(l=>l.draft_id===previous);if(match)selectLead(match);}
}

function applyFilters(){
  const query=$('queue-search').value.trim().toLowerCase();const global=$('global-search').value.trim().toLowerCase();const status=$('status-filter').value;const contact=$('contact-filter').value;
  state.filtered=state.leads.filter(lead=>{
    const hay=[lead.name,lead.email,lead.address,lead.type].join(' ').toLowerCase();
    if((query&&!hay.includes(query))||(global&&!hay.includes(global)))return false;
    if(status!=='all'&&stage(lead)!==status)return false;
    if(contact!=='all'&&!lead[contact])return false;
    return true;
  });renderQueue();
  if(!state.filtered.includes(state.selected))selectLead(state.filtered[0]||null);
}

function renderQueue(){
  const list=$('queue-list');list.replaceChildren();$('queue-count').textContent=state.filtered.length;$('nav-review-count').textContent=state.leads.length;$('queue-range').textContent=`${state.filtered.length} leads`;
  if(!state.filtered.length){const empty=document.createElement('div');empty.className='empty-state';empty.textContent=state.leads.length?'No leads match these filters.':'No drafts waiting. Start a scan to find leads.';list.append(empty);return}
  state.filtered.forEach(lead=>{
    const button=document.createElement('button');button.className='queue-item'+(lead===state.selected?' active':'');button.type='button';
    const dot=document.createElement('span');dot.className=`queue-dot ${stage(lead)}`;
    const copy=document.createElement('span');const name=document.createElement('b');name.textContent=lead.name||'Unnamed business';const meta=document.createElement('small');meta.textContent=[lead.address||'New York',stage(lead).replace('_',' ')].join(' · ');copy.append(name,meta);
    const score=document.createElement('span');score.className='queue-score';score.textContent=contactCompleteness(lead);
    button.append(dot,copy,score);button.addEventListener('click',()=>selectLead(lead));list.append(button);
  });
}

function contactCompleteness(lead){return [lead.email,lead.phone,lead.website,lead.address].filter(Boolean).length*25}
function yesNo(value){return value?'Available':'Missing'}

function selectLead(lead){
  state.selected=lead;state.selectedIndex=lead?state.filtered.indexOf(lead):-1;state.jobId=lead?.job_id||null;renderQueue();
  $('position-label').textContent=lead?`${state.selectedIndex+1} of ${state.filtered.length}`:`0 of ${state.filtered.length}`;
  $('empty-workspace').classList.toggle('hidden',!!lead);$('lead-workspace').classList.toggle('hidden',!lead);if(!lead)return;
  $('lead-name').textContent=lead.name||'Unnamed business';$('lead-meta').textContent=[lead.type||'Business',lead.address||'New York',stage(lead)].join(' · ');
  $('lead-email').textContent=lead.email?`✉ ${lead.email}`:'✉ No email';$('lead-phone').textContent=lead.phone?`⌕ ${lead.phone}`:'⌕ No phone';
  const website=safeURL(lead.website);$('lead-site').textContent=website?lead.website:'No website';$('lead-site').href=website||'#';$('open-website').href=website||'#';$('open-website').classList.toggle('hidden',!website);$('preview-url').textContent=website||'No website found';
  $('draft-subject').value=lead.subject||'';$('draft-body').value=lead.draft||'';$('subject-count').textContent=$('draft-subject').value.length;$('body-count').textContent=$('draft-body').value.length;
  const score=contactCompleteness(lead);$('completeness-score').textContent=score;$('score-ring').style.background=`conic-gradient(var(--green) ${score*3.6}deg,#17232d 0)`;
  $('has-email').textContent=yesNo(lead.email);$('has-phone').textContent=yesNo(lead.phone);$('has-website').textContent=yesNo(lead.website);renderIntel(lead);
  const current=stage(lead);$('approve-button').classList.toggle('hidden',current!=='draft');$('queue-button').classList.toggle('hidden',current!=='approved');$('confirm-button').classList.toggle('hidden',current!=='queued');
}

function renderIntel(lead){
  const root=$('intel-content');root.className='intel-stack';root.replaceChildren();
  root.append(intelSection('Business',[['Industry',lead.type||'Unknown'],['Location',lead.address||'Unknown'],['Email',lead.email||'Not found'],['Phone',lead.phone||'Not found'],['Website',lead.website||'Not found']]));
  if(!lead.draft_id){root.append(note('No draft generated yet. Winston writes only from researched evidence.'));return}
  const pending=note('Loading Winston\u2019s reasoning\u2026');root.append(pending);
  fetch(`/drafts/${encodeURIComponent(lead.draft_id)}/intelligence`).then(r=>r.ok?r.json():null).then(data=>{
    pending.remove();
    if(!data){root.append(note('No reasoning recorded for this draft.'));return}
    renderReasoning(root,data);
  }).catch(()=>{pending.textContent='Could not load reasoning.'});
}

function note(text){const el=document.createElement('p');el.className='intel-note';el.textContent=text;return el}

function intelSection(title,rows){
  const box=document.createElement('section');box.className='intel-section';
  const h=document.createElement('h4');h.textContent=title;box.append(h);
  const dl=document.createElement('dl');dl.className='intel-grid';
  rows.forEach(([k,v])=>{const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v;row.append(dt,dd);dl.append(row)});
  box.append(dl);return box;
}

function pill(text,tone){const s=document.createElement('span');s.className=`pill pill-${tone}`;s.textContent=text;return s}

function renderReasoning(root,data){
  const brief=data.brief||{},verdict=data.guardian||{};

  // Guardian verdict first. A reviewer should see the gate before the copy.
  const g=document.createElement('section');g.className='intel-section';
  const gh=document.createElement('h4');gh.textContent='Guardian';
  gh.append(pill(verdict.approved?'PASS':'BLOCKED',verdict.approved?'ok':'bad'));g.append(gh);
  const checks=['style_checks','evidence_checks','claim_checks','commercial_checks'];
  const summary=document.createElement('div');summary.className='check-row';
  checks.forEach(key=>{const list=verdict[key]||[];const failed=list.filter(c=>c.passed===false).length;
    summary.append(pill(`${key.replace('_checks','')} ${failed?`${failed} failed`:'ok'}`,failed?'bad':'ok'))});
  g.append(summary);
  (verdict.issues||[]).forEach(i=>{const el=document.createElement('p');el.className='intel-issue';el.textContent=`${i.rule}: ${i.detail}`;g.append(el)});
  (verdict.warnings||[]).slice(0,4).forEach(w=>{const el=document.createElement('p');el.className='intel-warn';el.textContent=`${w.rule}: ${w.detail}`;g.append(el)});
  root.append(g);

  // What Winston observed, with the evidence behind each claim.
  const problems=brief.observed_problems||[];
  const p=document.createElement('section');p.className='intel-section';
  const ph=document.createElement('h4');ph.textContent=`What Winston found (${problems.length})`;p.append(ph);
  if(!problems.length)p.append(note('No observations met the confidence floor.'));
  problems.forEach(item=>{
    const card=document.createElement('div');card.className='evidence-card';
    const label=document.createElement('b');label.textContent=item.label;
    const conf=pill(`confidence ${Math.round((item.confidence||0)*100)}%`,item.confidence>=0.7?'ok':'warn');
    const ev=document.createElement('small');ev.textContent=item.evidence||'';
    card.append(label,conf,ev);p.append(card);
  });
  if((brief.withheld_low_confidence||[]).length)p.append(note(`Withheld as too uncertain to state: ${brief.withheld_low_confidence.join(', ')}`));
  root.append(p);

  // The offer and why it was chosen.
  const offer=brief.recommended_service||brief.recommended_product;
  const rows=[['Recommended',offer?offer.name:'None'],['Kind',offer?offer.kind:'\u2014'],['Intent',brief.intent||'\u2014']];
  if(offer&&offer.price_min_usd!=null)rows.push(['Catalogue range',`$${offer.price_min_usd} to $${offer.price_max_usd}`]);
  else rows.push(['Pricing','Not yet available (pricing engine is Phase B)']);
  (brief.scores?Object.entries(brief.scores):[]).forEach(([k,v])=>rows.push([k.replaceAll('_',' ').toLowerCase(),v==null?'unknown':v]));
  root.append(intelSection('Recommendation',rows));

  // Proof, with why it was selected and its relevance score.
  const proof=brief.proof||[];
  const pr=document.createElement('section');pr.className='intel-section';
  const prh=document.createElement('h4');prh.textContent='Proof cited';pr.append(prh);
  if(!proof.length)pr.append(note('No proof linked to this offer.'));
  proof.forEach(item=>{
    const card=document.createElement('div');card.className='evidence-card';
    const b=document.createElement('b');b.textContent=item.name;
    card.append(b,pill(`relevance ${item.relevance}`,'ok'));
    const why=document.createElement('small');why.textContent=item.why||'';card.append(why);
    const url=safeURL(item.url);
    if(url){const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener noreferrer';a.textContent=item.url;card.append(a)}
    pr.append(card);
  });
  root.append(pr);

  (brief.blockers||[]).forEach(b=>root.append(note(b)));
  root.append(intelSection('Generation',[['Provider',data.provider||'\u2014'],['Model',data.model||'\u2014'],['Tokens',`${data.input_tokens||0} in / ${data.output_tokens||0} out`],['AI cost',`$${(data.estimated_cost_usd||0).toFixed(4)}`]]));
}

function renderEvents(events){const root=$('activity-list');root.replaceChildren();if(!events.length){root.textContent='No activity yet.';return}events.forEach(item=>{const el=document.createElement('div');el.className='event';const label=document.createElement('span');label.textContent=item.event_type.replaceAll('.',' · ');const time=document.createElement('time');time.textContent=new Date(item.created_at).toLocaleString();el.append(label,time);root.append(el)})}
function move(delta){if(!state.filtered.length)return;const next=(state.selectedIndex+delta+state.filtered.length)%state.filtered.length;selectLead(state.filtered[next])}

async function approve(){if(!state.selected)return;try{const data=await api('/approve',requestJSON('POST',{index:state.selected._index}));state.selected.draft_id=data.draft_id;state.selected.workflow_stage='approved';toast(`${data.name} approved. Nothing sent.`);selectLead(state.selected);loadDashboard()}catch(error){toast(error.message,true)}}
async function queue(){if(!state.selected?.draft_id)return;try{const data=await api(`/drafts/${encodeURIComponent(state.selected.draft_id)}/queue`,{method:'POST'});state.jobId=data.job_id;state.selected.job_id=data.job_id;state.selected.workflow_stage='queued';toast('Draft queued. Final confirmation is still required.');selectLead(state.selected);loadDashboard()}catch(error){toast(error.message,true)}}
async function confirmSend(){if(!state.jobId)return;const dialog=$('confirm-dialog');dialog.showModal();const result=await new Promise(resolve=>dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true}));if(result!=='confirm')return;try{await api(`/send-jobs/${encodeURIComponent(state.jobId)}/confirm`,{method:'POST'});toast('Confirmed email sent.');await loadLeads(false);await loadDashboard()}catch(error){toast(error.message,true)}}
async function removeLead(kind){if(!state.selected)return;try{await api(`/${kind}`,requestJSON('POST',{index:state.selected._index}));toast(kind==='reject'?'Lead rejected.':'Lead skipped.');await loadLeads(false);await loadDashboard()}catch(error){toast(error.message,true)}}
async function startScan(){try{const data=await api('/scan',{method:'POST'});toast(data.status==='already scanning'?'Scan already running.':'Lead scan started.');loadDashboard()}catch(error){toast(error.message,true)}}
async function draftExisting(){try{await api('/draft-existing',requestJSON('POST',{limit:10}));toast('Creating up to 10 drafts from existing contacts with local Ollama.');loadDashboard()}catch(error){toast(error.message,true)}}
async function stopScan(){try{await api('/stop',{method:'POST'});toast('Scan stopped.');loadDashboard()}catch(error){toast(error.message,true)}}

function bind(){
  $('queue-search').addEventListener('input',applyFilters);$('global-search').addEventListener('input',applyFilters);$('status-filter').addEventListener('change',applyFilters);$('contact-filter').addEventListener('change',applyFilters);
  $('prev-lead').onclick=()=>move(-1);$('next-lead').onclick=()=>move(1);$('approve-button').onclick=approve;$('queue-button').onclick=queue;$('confirm-button').onclick=confirmSend;$('reject-button').onclick=()=>removeLead('reject');$('skip-button').onclick=()=>removeLead('skip');
  $('scan-button').onclick=startScan;$('draft-existing-button').onclick=draftExisting;$('empty-draft-existing').onclick=draftExisting;$('stop-button').onclick=stopScan;$('export-button').onclick=()=>location.href='/export_csv';$('shortcuts-button').onclick=()=>$('shortcuts-dialog').showModal();$('mobile-menu').onclick=()=>$('sidebar').classList.toggle('open');
  $('filter-toggle').onclick=()=>$('filters').classList.toggle('hidden');
  document.querySelectorAll('.nav-item').forEach(button=>button.onclick=()=>{
    const view=button.dataset.view,filter=button.dataset.filter;
    if(button.id==='nav-live'){toast(state.dashboard?.scan_status==='drafting_existing'?'Local drafting is running.':state.dashboard?.scan_status==='scanning'?'Google discovery scan is running.':'No operation is currently running.')}
    else if(filter==='no-website'){$('contact-filter').value='all';$('global-search').value='';$('queue-search').value='';state.filtered=state.leads.filter(lead=>!lead.website);renderQueue();selectLead(state.filtered[0]||null);toast(`${state.filtered.length} queued leads have no website.`)}
    else if(view==='editorial'){$('queue-search').focus()}
    else if(view==='overview'){window.scrollTo({top:0,behavior:'smooth'});toast('Live command-center metrics are shown above.')}
    else if(view==='leads'){$('global-search').value='';$('queue-search').value='';$('status-filter').value='all';$('contact-filter').value='all';applyFilters();$('queue-search').focus()}
    else if(view==='sent'){window.open('/sent?limit=100','_blank','noopener')}
    else if(view==='social'){window.open('/social_leads?limit=100','_blank','noopener')}
    else if(view==='activity'){$('activity-list').scrollIntoView({behavior:'smooth',block:'center'})}
    document.querySelectorAll('.nav-item').forEach(item=>item.classList.remove('active'));button.classList.add('active');
  });
  document.querySelectorAll('.tabs button').forEach(button=>button.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));button.classList.add('active');['overview','outreach','activity'].forEach(name=>$(`tab-${name}`).classList.toggle('hidden',button.dataset.tab!==name))});
  document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('global-search').focus();return}if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;const key=event.key.toLowerCase();if(key==='j')move(1);if(key==='k')move(-1);if(key==='a')approve();if(key==='r')removeLead('reject')});
}

async function boot(){bind();try{await Promise.all([loadDashboard(),loadLeads(false)]);setInterval(()=>{loadDashboard().catch(()=>{});loadLeads(true).catch(()=>{})},10000)}catch(error){toast(error.message,true);$('online-label').textContent='DEGRADED'}}
boot();
