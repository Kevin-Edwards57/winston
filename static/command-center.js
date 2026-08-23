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
  renderOpportunity(lead);
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
  const pricing=brief.pricing||{};
  if(brief.pricing_status==='quoted'){
    rows.push(['Recommended',`$${Math.round(pricing.target_usd).toLocaleString()}`]);
    rows.push(['Range',`$${Math.round(pricing.floor_usd).toLocaleString()} to $${Math.round(pricing.premium_usd).toLocaleString()}`]);
    rows.push(['Estimated effort',`${pricing.effort_hours}h at $${pricing.hourly_rate}/h`]);
    rows.push(['Margin at target',`${Math.round((pricing.margin_at_target||0)*100)}%`]);
    rows.push(['Price confidence',pricing.confidence]);
  } else if(brief.pricing_status==='no_pricing_basis'){
    rows.push(['Pricing',pricing.reason||'No pricing basis']);
  } else rows.push(['Pricing','Not calculated']);
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

  // Pricing arithmetic, so a reviewer can disagree with a step rather than a number.
  if(brief.pricing_status==='quoted'){
    const pz=document.createElement('section');pz.className='intel-section';
    const pzh=document.createElement('h4');pzh.textContent='How the price was built';pz.append(pzh);
    (brief.pricing.rationale||[]).forEach(line=>{const el=document.createElement('p');el.className='intel-note';el.textContent=line;pz.append(el)});
    (brief.pricing.adjustments||[]).forEach(adj=>{const card=document.createElement('div');card.className='evidence-card';
      const b=document.createElement('b');b.textContent=`${adj.factor} x${adj.multiplier}`;
      const s=document.createElement('small');s.textContent=`${adj.reason}${adj.evidence?` (${adj.evidence})`:''}`;
      card.append(b,s);pz.append(card)});
    (brief.pricing.scope_assumptions||[]).forEach(a=>{const el=document.createElement('p');el.className='intel-note';el.textContent=`Assumption: ${a}`;pz.append(el)});
    root.append(pz);
  }

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
  const research=$('research-button');if(research)research.onclick=researchProspect;
  document.querySelectorAll('[data-batch]').forEach(button=>
    button.onclick=()=>startResearchBatch(Number(button.dataset.batch)));
  const stop=$('research-stop');
  if(stop)stop.onclick=async()=>{await api('/research/batch',{method:'DELETE'});toast('Stopping after the current prospect.');};
  buildPalette();
  document.querySelectorAll('.nav-item').forEach(button=>button.onclick=()=>{
    const view=button.dataset.view,filter=button.dataset.filter;
    if(button.id==='nav-live'){
      const status=state.dashboard?.scan_status;
      toast(status==='drafting_existing'?'Local drafting is running.':status==='scanning'?'Google discovery scan is running.':'No operation is currently running.');
      return;
    }
    if(filter==='no-website'){
      showView('editorial');
      $('contact-filter').value='all';$('global-search').value='';$('queue-search').value='';
      state.filtered=state.leads.filter(lead=>!lead.website);renderQueue();
      selectLead(state.filtered[0]||null);
      toast(`${state.filtered.length} queued leads have no website.`);
      document.querySelectorAll('.nav-item').forEach(item=>item.classList.remove('active'));
      button.classList.add('active');
      return;
    }
    if(view==='leads'){
      showView('editorial');
      $('global-search').value='';$('queue-search').value='';
      $('status-filter').value='all';$('contact-filter').value='all';applyFilters();
      return;
    }
    if(view==='activity'){showView('editorial');$('activity-list').scrollIntoView({behavior:'smooth',block:'center'});return}
    if(view==='overview'){showView('editorial');window.scrollTo({top:0,behavior:'smooth'});return}
    showView(view);
  });
  document.querySelectorAll('.tabs button').forEach(button=>button.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));button.classList.add('active');['overview','outreach','activity'].forEach(name=>$(`tab-${name}`).classList.toggle('hidden',button.dataset.tab!==name))});
  document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();if(window.openPalette)window.openPalette();else $('global-search').focus();return}if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;const key=event.key.toLowerCase();if(key==='j')move(1);if(key==='k')move(-1);if(key==='a')approve();if(key==='r')removeLead('reject')});
}

window.addEventListener('hashchange',()=>showView(location.hash.slice(1)));

async function boot(){bind();showView(location.hash.slice(1)||'editorial');try{await Promise.all([loadDashboard(),loadLeads(false)]);setInterval(()=>{loadDashboard().catch(()=>{});loadLeads(true).catch(()=>{})},10000)}catch(error){toast(error.message,true);$('online-label').textContent='DEGRADED'}}


/* ── Real views ───────────────────────────────────────────────────────────
   The sidebar used to fake navigation: two items opened raw JSON in a new
   tab, the rest fired toasts or scrolled. These are actual views over data
   the backend already exposes. */

const VIEWS = ['editorial','sent','social','blocked','pricing','catalog','providers','research','projects','agents','ops'];

function showView(name){
  if(!VIEWS.includes(name)) name='editorial';
  VIEWS.forEach(view=>{const el=$(`view-${view}`);if(el)el.classList.toggle('hidden',view!==name)});
  document.querySelectorAll('.nav-item').forEach(item=>item.classList.toggle('active',item.dataset.view===name));
  if(location.hash.slice(1)!==name) history.replaceState(null,'',`#${name}`);
  const loader={sent:loadSent,social:loadSocial,blocked:loadBlocked,pricing:loadPricing,
                catalog:loadCatalog,providers:loadProviders,research:loadResearch,
                projects:loadProjects,agents:loadAgents,ops:loadOps}[name];
  if(loader) loader().catch(error=>toast(error.message,true));
}

function el(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node}
function badge(text,tone){return el('span',`badge badge-${tone}`,text)}
function money(value){return value==null?'—':`$${Number(value).toLocaleString(undefined,{maximumFractionDigits:0})}`}
function dash(value){return value==null||value===''?'—':value}

function table(root,columns,rows,renderRow,emptyMessage){
  root.replaceChildren();
  if(!rows.length){root.append(el('div','intel-empty',emptyMessage));return}
  const t=el('table','data-table'),thead=el('thead'),tr=el('tr');
  columns.forEach(c=>tr.append(el('th',null,c)));
  thead.append(tr);t.append(thead);
  const tbody=el('tbody');
  rows.forEach(row=>tbody.append(renderRow(row)));
  t.append(tbody);root.append(t);
}

/* Sent history. Previously window.open('/sent?limit=100'). */
async function loadSent(){
  const data=await api('/sent?limit=200');
  const rows=(data.sent||[]).slice().reverse();
  table($('sent-body'),['Business','Industry','Subject','Sent','Follow-up'],rows,item=>{
    const tr=el('tr');
    tr.append(el('td',null,dash(item.name)));
    tr.append(el('td','muted',dash(item.type)));
    tr.append(el('td','muted',dash(item.subject)));
    tr.append(el('td','muted',item.sent_date?new Date(item.sent_date).toLocaleDateString():'—'));
    const status=el('td');status.append(item.followup_sent?badge('sent','ok'):badge('none','muted'));
    tr.append(status);return tr;
  },'No sends recorded yet.');
}

/* Social leads. Previously window.open('/social_leads?limit=100'). */
async function loadSocial(){
  const data=await api('/social_leads?limit=200');
  const rows=data.leads||[];
  table($('social-body'),['Business','Industry','Instagram','Facebook','Website'],rows,item=>{
    const social=item.social||{};
    const tr=el('tr');
    tr.append(el('td',null,dash(item.name)));
    tr.append(el('td','muted',dash(item.type)));
    tr.append(el('td','muted',dash(social.instagram)));
    tr.append(el('td','muted',dash(social.facebook)));
    const site=el('td');
    const url=safeURL(item.website);
    if(url){const a=el('a',null,item.website);a.href=url;a.target='_blank';a.rel='noopener noreferrer';site.append(a)}
    else site.textContent='—';
    tr.append(site);return tr;
  },'No social-only leads yet.');
}

/* Guardian blocks. There is deliberately no bypass control here. */
async function loadBlocked(){
  const data=await api('/drafts/blocked');
  const rows=data.blocked||[];
  $('nav-blocked-count').textContent=rows.length;
  table($('blocked-body'),['Business','Status','Rules triggered','When'],rows,item=>{
    const tr=el('tr');
    tr.append(el('td',null,dash(item.business)));
    const status=el('td');status.append(badge(item.status,'bad'));tr.append(status);
    const rules=el('td');
    (item.issues||[]).forEach(rule=>rules.append(badge(rule.replaceAll('_',' '),'bad')));
    if(!(item.issues||[]).length)rules.textContent='—';
    tr.append(rules);
    tr.append(el('td','muted',item.created_at?new Date(item.created_at).toLocaleString():'—'));
    return tr;
  },'Nothing blocked. Guardian has not refused any draft.');
}

/* Pricing. Every number states whether it is an assumption. */
async function loadPricing(){
  const [card,readiness]=await Promise.all([api('/ratecard'),api('/pricing')]);
  const warn=$('pricing-warning');warn.replaceChildren();
  if(card.status&&card.status.warning) warn.append(el('div','warning-banner',card.status.warning));
  if(!readiness.can_quote) warn.append(el('div','warning-banner',`Cannot quote: ${readiness.missing.join('; ')}`));
  const rate=readiness.rate_card&&readiness.rate_card.hourly_rate_usd;
  warn.append(el('div','intel-note',rate?`Internal rate $${rate}/h. This is delivery cost, not the client price.`
                                        :'No internal hourly rate configured.'));
  table($('pricing-body'),['Service','Enabled','Floor','Target','Premium','Effort','Basis'],card.entries||[],item=>{
    const tr=el('tr');
    tr.append(el('td',null,item.slug));
    const on=el('td');on.append(item.enabled?badge('enabled','ok'):badge('disabled','muted'));tr.append(on);
    tr.append(el('td','mono',money(item.price_floor_usd)));
    tr.append(el('td','mono',money(item.price_target_usd)));
    tr.append(el('td','mono',money(item.price_premium_usd)));
    tr.append(el('td','muted',item.effort_hours_min?`${item.effort_hours_min}-${item.effort_hours_max}h`:'—'));
    const basis=el('td');
    basis.append(item.evidence_backed?badge('evidence','ok'):badge('assumption','warn'));
    tr.append(basis);return tr;
  },'No rate card entries.');
}

/* Catalogue. Sellable, proof, and internal must stay visibly distinct. */
async function loadCatalog(){
  const data=await api('/catalog');
  const readiness=data.readiness||{};
  const root=$('catalog-body');root.replaceChildren();
  if(!readiness.can_recommend) root.append(el('div','warning-banner','No verified business-facing entry. Winston will not recommend anything.'));
  const wrap=el('div');
  table(wrap,['Entry','Kind','Status','Audience','Verified','Role'],data.entries||[],item=>{
    const tr=el('tr');
    tr.append(el('td',null,item.name));
    tr.append(el('td','muted',item.kind));
    tr.append(el('td','muted',item.status));
    tr.append(el('td','muted',item.audience));
    const ver=el('td');ver.append(item.verified?badge('verified','ok'):badge('unverified','warn'));tr.append(ver);
    const role=el('td');
    if(item.offerable_to_business)role.append(badge('sellable','ok'));
    else if(item.citable_as_proof)role.append(badge('proof only','info'));
    else role.append(badge('not offerable','muted'));
    tr.append(role);return tr;
  },'Catalogue is empty.');
  root.append(wrap);
}

/* AI and cost. The zero-cost architecture should be visible, not assumed. */
async function loadProviders(){
  const [summary,costs]=await Promise.all([api('/providers'),api('/costs')]);
  const root=$('providers-body');root.replaceChildren();

  const cost=el('div','cost-strip');
  [['Today',costs.ai_cost.today_usd],['This month',costs.ai_cost.month_to_date_usd],
   ['Projected',costs.ai_cost.projected_month_usd]].forEach(([label,value])=>{
    const box=el('div','cost-box');box.append(el('small',null,label),el('strong',null,`$${Number(value).toFixed(2)}`));cost.append(box)});
  root.append(cost);
  if(!costs.spend_capable_providers.length)
    root.append(el('div','ok-banner','No provider is currently able to spend money.'));

  const avail=el('div');
  table(avail,['Provider','Cost class','Usable','Suited to','Note'],summary.availability||[],item=>{
    const tr=el('tr');
    tr.append(el('td',null,item.key));
    tr.append(el('td','muted',item.cost_class));
    const use=el('td');use.append(item.usable?badge('usable','ok'):badge('blocked','muted'));tr.append(use);
    tr.append(el('td','muted',(item.suitable_for||[]).join(', ')));
    tr.append(el('td','muted',item.blocked_reason||item.notes||''));
    return tr;
  },'No providers.');
  root.append(el('h3','section-heading','Providers'),avail);

  const routing=el('div');
  const policy=summary.policy||{};
  table(routing,['Task class','Preference order'],Object.keys(policy),name=>{
    const tr=el('tr');tr.append(el('td',null,name));
    tr.append(el('td','muted',(policy[name]||[]).join('  →  ')));return tr;
  },'No routing policy.');
  root.append(el('h3','section-heading','Routing policy'),routing);
}

/* System. Winston's own readiness, stated plainly. */
async function loadOps(){
  const health=await api('/health');
  const root=$('ops-body');root.replaceChildren();

  const warnings=[];
  if(!health.catalogue?.can_recommend) warnings.push('No verified business-facing service. Winston cannot recommend anything.');
  if(health.rate_card?.all_prices_are_assumptions) warnings.push('Every price is an operator assumption. No completed engagement supports any of them.');
  if(!health.pricing?.can_quote) warnings.push(`Pricing cannot quote: ${(health.pricing?.missing||[]).join('; ')}`);
  if(!health.funnel?.reply_tracking_enabled) warnings.push('Reply tracking has never run, so reply rates are unknown rather than zero.');
  (health.misconfigured_providers||[]).forEach(item=>warnings.push(`${item.provider}: ${item.problem}`));
  warnings.forEach(text=>root.append(el('div','warning-banner',text)));

  if(health.dry_run) root.append(el('div','ok-banner','Dry run is ON. No message can reach a real inbox.'));
  root.append(el('div','ok-banner',`Legacy follow-up sender: ${health.legacy_followup_sender}.`));

  const counts=el('div');
  table(counts,['Table','Rows'],Object.entries(health.counts||{}),([name,value])=>{
    const tr=el('tr');tr.append(el('td',null,name.replaceAll('_',' ')));
    tr.append(el('td','mono',String(value)));return tr;
  },'No counts.');
  root.append(el('h3','section-heading','Database'),counts);

  const funnel=el('div');
  const f=health.funnel||{};
  table(funnel,['Stage','Value'],[
    ['Sent',f.sent],['Delivered',f.delivery_tracked?f.delivered:'not tracked'],
    ['Replies',f.reply_tracking_enabled?f.replies:'not tracked'],
    ['Meetings',f.meetings],['Proposals',f.proposals],
    ['Won',f.deals_won],['Revenue',money(f.revenue_usd)],
    ['Reply rate',f.reply_rate==null?'unknown':`${(f.reply_rate*100).toFixed(1)}%`],
  ],([label,value])=>{
    const tr=el('tr');tr.append(el('td',null,label));
    tr.append(el('td','mono',String(value)));return tr;
  },'No funnel data.');
  root.append(el('h3','section-heading','Commercial funnel'),funnel);
}


/* Opportunity replaces Contact Completeness. Having an email address is not an
   opportunity, and a green 100 beside an unresearched business reads as an
   endorsement Winston has not earned. */
async function renderOpportunity(lead){
  const label=$('completeness-score'),ring=$('score-ring');
  if(!lead.contact_id){label.textContent='—';ring.style.background='#17232d';return}
  label.textContent='…';
  try{
    const fit=await api(`/prospects/${encodeURIComponent(lead.contact_id)}/fit`);
    const score=Math.round((fit.scores?.COMMERCIAL_OPPORTUNITY||0)*100);
    const researched=(fit.observed_problems||[]).length>0;
    label.textContent=researched?score:'—';
    ring.style.background=researched?`conic-gradient(var(--green) ${score*3.6}deg,#17232d 0)`:'#17232d';
    const note=$('score-note');
    if(note) note.textContent=researched
      ? `${fit.observed_problems.length} observed problem(s), confidence ${fit.scores.CONFIDENCE}`
      : 'Not researched yet. Opportunity is unknown, not zero.';
  }catch(error){label.textContent='—'}
}

/* Research from the interface rather than curl. */
async function researchProspect(){
  const lead=state.selected;
  if(!lead?.contact_id){toast('Select a prospect first.',true);return}
  const button=$('research-button');
  if(button){button.disabled=true;button.textContent='Researching…'}
  try{
    const result=await api(`/research/${encodeURIComponent(lead.contact_id)}`,requestJSON('POST',{}));
    toast(result.status==='ok'?`Found ${result.signals} signal(s) across ${result.pages} page(s).`
                              :`Could not reach that site (${result.status}).`,result.status!=='ok');
    renderOpportunity(lead);renderIntel(lead);
  }catch(error){toast(error.message,true)}
  finally{if(button){button.disabled=false;button.textContent='⟳ Research'}}
}

/* Start only once every declaration above has been evaluated. Invoking boot()
   mid-file put it in the temporal dead zone of the view module's constants. */

/* ── Research batches ───────────────────────────────────────────────────
   Bounded deliberately. Researching 1,390 prospects means 1,390 real requests
   at small-business websites, so the operator picks a size and watches it. */

async function loadResearch(){
  const data=await api('/research/progress');
  const status=$('research-status');status.replaceChildren();
  const c=data.coverage||{};
  status.append(el('div','warning-banner',
    `${c.unresearched} of ${c.contacts} prospects have never been researched. Winston will not write about a business it has not looked at.`));

  const root=$('research-progress');root.replaceChildren();
  if(!data.requested){root.append(el('div','intel-empty','No batch has run yet.'));return}
  const strip=el('div','cost-strip');
  [['Requested',data.requested],['Researched',data.completed],
   ['Unreachable',data.unreachable],['Failed',data.failed],
   ['Signals found',data.signals]].forEach(([label,value])=>{
    const box=el('div','cost-box');box.append(el('small',null,label),el('strong',null,String(value)));strip.append(box)});
  root.append(strip);
  root.append(el('div',data.running?'ok-banner':'intel-note',
    data.running?'RUNNING. Progress updates every few seconds.':'Batch finished.'));
}

async function startResearchBatch(limit){
  if(!confirm(`Research ${limit} prospects? This fetches ${limit} real websites, one at a time.`)) return;
  try{
    await api('/research/batch',requestJSON('POST',{limit}));
    toast(`Research batch of ${limit} started.`);
    pollResearch();
  }catch(error){toast(error.message,true)}
}

let researchTimer=null;
function pollResearch(){
  clearInterval(researchTimer);
  researchTimer=setInterval(async()=>{
    try{
      const data=await api('/research/progress');
      await loadResearch();
      if(!data.running){clearInterval(researchTimer);loadDashboard().catch(()=>{})}
    }catch(error){clearInterval(researchTimer)}
  },4000);
}

/* ── Agents ─────────────────────────────────────────────────────────────
   Only what is genuinely implemented is listed as active. A function is not
   an agent because it has a good name, and a dashboard implying otherwise
   would be exactly the fake-agent theatre worth avoiding. */

const AGENTS=[
  {name:'Scout',status:'active',fn:'google_places_search',role:'Business discovery',
   note:'Queries Google Places across categories and boroughs. Costs money, so it is opt-in.'},
  {name:'Researcher',status:'active',fn:'research_contact',role:'Website and contact research',
   note:'Fetches a site, follows contact pages, extracts email, phone and social.'},
  {name:'Auditor',status:'active',fn:'extract_signals / derive_problems',role:'Digital-presence analysis',
   note:'Deterministic. Withholds a negative when the page is client-rendered rather than guessing.'},
  {name:'Strategist',status:'active',fn:'FitEngine.assess',role:'Offer matching',
   note:'Scores product fit, service fit and proof relevance separately.'},
  {name:'Pricer',status:'active',fn:'PricingEngine.quote',role:'Commercial pricing',
   note:'Refuses without a rate card. Protected characteristics cannot reach it.'},
  {name:'Writer',status:'active',fn:'Writer.write',role:'Outreach generation',
   note:'Only states facts present in the brief. Declines when no verified offer fits.'},
  {name:'Guardian',status:'active',fn:'Guardian.review',role:'Deterministic safety gate',
   note:'Veto power. No bypass exists anywhere in the codebase.'},
  {name:'Provider Router',status:'active',fn:'ProviderRegistry.route',role:'Model selection',
   note:'Cheapest capable model first. Paid escalation needs two independent gates.'},
  {name:'Inbox',status:'built, never run',fn:'InboxScanner.scan',role:'Reply classification',
   note:'Implemented and unit tested, but has never run against a real mailbox.'},
  {name:'Negotiator',status:'planned',fn:'—',role:'Reply handling and next action',
   note:'Not implemented.'},
  {name:'Learner',status:'blocked',fn:'—',role:'Outcome learning',
   note:'Waiting on labelled outcomes. Zero replies, meetings or closed deals recorded.'},
  {name:'ML Engine',status:'blocked',fn:'—',role:'Prediction',
   note:'Insufficient data. Training on zero positive examples is not possible.'},
];

async function loadAgents(){
  const root=$('agents-body');root.replaceChildren();
  const active=AGENTS.filter(a=>a.status==='active').length;
  root.append(el('div','ok-banner',
    `${active} of ${AGENTS.length} roles are implemented and running. The rest are listed as planned or blocked rather than shown as active.`));
  const wrap=el('div');
  table(wrap,['Role','Status','Implementation','Responsibility','Notes'],AGENTS,item=>{
    const tr=el('tr');
    tr.append(el('td',null,item.name));
    const st=el('td');
    st.append(badge(item.status,item.status==='active'?'ok':item.status==='blocked'?'muted':'warn'));
    tr.append(st);
    tr.append(el('td','mono',item.fn));
    tr.append(el('td','muted',item.role));
    tr.append(el('td','muted',item.note));
    return tr;
  },'No agents.');
  root.append(wrap);
}

/* ── Command palette ────────────────────────────────────────────────────
   Cmd/Ctrl+K used to focus a search box. It now opens a command list, so the
   whole application is reachable without the mouse. */

const COMMANDS=[
  {label:'Go to Review queue', run:()=>showView('editorial')},
  {label:'Go to Research', run:()=>showView('research')},
  {label:'Go to Guardian blocks', run:()=>showView('blocked')},
  {label:'Go to Pricing', run:()=>showView('pricing')},
  {label:'Go to Catalogue', run:()=>showView('catalog')},
  {label:'Go to AI and cost', run:()=>showView('providers')},
  {label:'Go to Client projects', run:()=>showView('projects')},
  {label:'Go to Agents', run:()=>showView('agents')},
  {label:'Go to Sent history', run:()=>showView('sent')},
  {label:'Go to Social leads', run:()=>showView('social')},
  {label:'Go to System', run:()=>showView('ops')},
  {label:'Research this prospect', run:researchProspect},
  {label:'Research batch of 10', run:()=>startResearchBatch(10)},
  {label:'Research batch of 25', run:()=>startResearchBatch(25)},
  {label:'Draft 10 existing contacts', run:draftExisting},
  {label:'Export contacts as CSV', run:()=>location.href='/export_csv'},
  {label:'Approve current draft', run:approve},
];

function buildPalette(){
  if($('palette')) return;
  const dialog=document.createElement('dialog');dialog.id='palette';dialog.className='palette';
  const input=document.createElement('input');input.placeholder='Type a command…';input.id='palette-input';
  const list=document.createElement('div');list.className='palette-list';list.id='palette-list';
  dialog.append(input,list);document.body.append(dialog);

  function render(filter=''){
    const needle=filter.trim().toLowerCase();
    const matches=COMMANDS.filter(c=>c.label.toLowerCase().includes(needle));
    list.replaceChildren();
    if(!matches.length){list.append(el('div','intel-empty','No matching command.'));return}
    matches.forEach((command,index)=>{
      const row=el('button','palette-item'+(index===0?' active':''),command.label);
      row.onclick=()=>{dialog.close();command.run()};
      list.append(row);
    });
  }
  input.oninput=()=>render(input.value);
  input.onkeydown=event=>{
    if(event.key==='Enter'){
      const first=list.querySelector('.palette-item');
      if(first){dialog.close();first.click()}
      event.preventDefault();
    }
  };
  dialog.addEventListener('close',()=>{input.value=''});
  window.openPalette=()=>{render();dialog.showModal();input.focus()};
}


/* ── Client projects ────────────────────────────────────────────────────
   The Website Builder exposes no HTTP API, so status here is what a human
   reported. The view says so rather than implying it was observed. */

async function loadProjects(){
  const data=await api('/projects');
  const note=$('projects-note');note.replaceChildren();
  note.append(el('div','warning-banner',data.status.integration_note));

  table($('projects-body'),['Client','Service','Status','Reported','Builder ref','Published','Price'],
    data.projects||[],item=>{
      const tr=el('tr');
      tr.append(el('td',null,item.business));
      tr.append(el('td','muted',item.service_slug));
      const st=el('td');
      st.append(badge(item.status.replaceAll('_',' '),
        item.status==='published'?'ok':item.status==='cancelled'?'muted':'info'));
      tr.append(st);
      tr.append(el('td','muted',item.status_reported_at?new Date(item.status_reported_at).toLocaleDateString():'—'));
      tr.append(el('td','mono',item.builder_reference||'—'));
      const pub=el('td');
      const url=safeURL(item.published_url);
      if(url){const a=el('a',null,item.published_url);a.href=url;a.target='_blank';a.rel='noopener noreferrer';pub.append(a)}
      else pub.textContent='—';
      tr.append(pub);
      tr.append(el('td','mono',item.agreed_price_usd?`$${Math.round(item.agreed_price_usd).toLocaleString()}`:'—'));
      return tr;
    },'No client projects yet. A prospect becomes a project once they buy.');
}

/* Start only once every declaration above has been evaluated. */
boot();
