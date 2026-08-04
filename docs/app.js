/* Painel Sapron × Pipefy — versão colaborativa
 * Login simples: usuário digita email @seazone.com.br → guarda no localStorage.
 * Todo check/OBS carrega esse email, sincroniza via Supabase Realtime.
 */

const CFG = window.PAINEL_CONFIG;
const sb = supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey);

const $ = id => document.getElementById(id);
const escapeHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

const IDENTITY_KEY = 'painel_identity_v1';
function getMe(){ try{ return JSON.parse(localStorage.getItem(IDENTITY_KEY) || 'null'); } catch(e){ return null; } }
function saveMe(me){ localStorage.setItem(IDENTITY_KEY, JSON.stringify(me)); }
function clearMe(){ localStorage.removeItem(IDENTITY_KEY); }
let ME = getMe();

let CARDS = [], ACTS = [];
let CHECKS = new Map(), NOTES = new Map();
let META = null;
let FILTRO = 'todos', BUSCA = '';

function toast(msg, ms=3500){
  const t = document.createElement('div');
  t.className='toast'; t.textContent=msg;
  $('toast-container').appendChild(t);
  setTimeout(()=> t.remove(), ms);
}

function showLogin(errorMsg){
  $('login-screen').classList.remove('hidden');
  $('app').classList.add('hidden');
  const err = $('login-error');
  if(errorMsg){ err.textContent = errorMsg; err.classList.remove('hidden'); }
  else err.classList.add('hidden');
}
function showApp(){
  $('login-screen').classList.add('hidden');
  $('app').classList.remove('hidden');
  const initials = (ME.name||ME.email).split(' ').map(x=>x[0]).slice(0,2).join('').toUpperCase();
  $('me-avatar').textContent = initials;
  $('me-name').textContent = ME.name;
}

$('btn-login').addEventListener('click', () => {
  const email = ($('input-email').value || '').trim().toLowerCase();
  const name  = ($('input-name').value  || '').trim();
  if(!email.endsWith(CFG.allowedDomain)){
    return showLogin(`E-mail precisa terminar em ${CFG.allowedDomain}`);
  }
  if(!name){ return showLogin('Digite seu nome pra os outros saberem quem marcou'); }
  ME = { email, name };
  saveMe(ME);
  init();
});
$('btn-logout').addEventListener('click', () => { clearMe(); location.reload(); });

async function init(){
  if(!ME || !ME.email?.endsWith(CFG.allowedDomain)){ return showLogin(); }
  showApp();
  await loadAll();
  subscribeRealtime();
}

async function loadAll(){
  const [c, a, ch, n, m] = await Promise.all([
    sb.from('pipefy_cards').select('*').order('order_idx'),
    sb.from('pipefy_activities').select('*').order('ultima_ts', { ascending:false }),
    sb.from('pipefy_checks').select('*'),
    sb.from('pipefy_notes').select('*'),
    sb.from('pipefy_meta').select('*').eq('id', 1).single(),
  ]);
  if(c.error) return fatal('cards', c.error);
  if(a.error) return fatal('activities', a.error);
  if(ch.error) return fatal('checks', ch.error);
  if(n.error) return fatal('notes', n.error);

  CARDS = c.data || [];
  ACTS  = a.data || [];
  CHECKS.clear(); (ch.data||[]).forEach(r => CHECKS.set(r.card_id, r));
  NOTES.clear();  (n.data ||[]).forEach(r => NOTES.set(r.card_id, r));
  META = m.data;

  const mine_stale = [];
  for(const card of CARDS){
    const chk = CHECKS.get(card.card_id);
    if(!chk || chk.user_email !== ME.email) continue;
    if((card.ultima_msg_ts||0) > new Date(chk.checked_at).getTime()){
      mine_stale.push(card.card_id);
    }
  }
  if(mine_stale.length){
    await sb.from('pipefy_checks').delete().in('card_id', mine_stale);
    mine_stale.forEach(id => CHECKS.delete(id));
    toast(`${mine_stale.length} desmarcado(s) por nova resposta`);
  }

  render();
  renderMeta();
}

function fatal(what, err){ console.error(what, err); toast('Erro ao carregar '+what+': '+err.message, 8000); }

const SAPRON_ACTIVITY_URL = id => `https://sapron.com.br/central-atividades/a/${id}`;

// Ativity mais recente por card (pra usar como link primário)
function primaryActivity(card_id){
  let best = null, bestTs = -1;
  for(const a of ACTS){
    if(a.card_id !== card_id) continue;
    const ts = a.ultima_ts || 0;
    if(ts > bestTs || best === null){ best = a; bestTs = ts; }
  }
  return best;
}

function render(){
  const tbody = $('tbody'); tbody.innerHTML = '';
  let visible=0, ok=0, pend=0, nossa=0, esp=0;
  // Quando o filtro é "Fase 3", ordena por conversa mais antiga primeiro (nulos vão na frente).
  const iter = (FILTRO === 'fase3')
    ? [...CARDS].sort((a,b) => (a.ultima_msg_ts||0) - (b.ultima_msg_ts||0))
    : CARDS;
  for(const r of iter){
    const chk = CHECKS.get(r.card_id);
    const note = NOTES.get(r.card_id);
    const isOK = !!chk;
    if(isOK) ok++; else pend++;
    if(!isOK && r.categoria==='nossa_vez') nossa++;
    if(!isOK && r.categoria==='esperando') esp++;

    let show = true;
    if(FILTRO==='pendentes' && isOK) show=false;
    if(['sem_activity','nossa_vez','sem_msg','esperando'].includes(FILTRO) && r.categoria!==FILTRO) show=false;
    if(FILTRO==='duplicados' && (r.chamados_andamento||0) <= 1) show=false;
    if(FILTRO==='sapron_only' && r.origem !== 'sapron_only') show=false;
    if(FILTRO==='fase3' && r.origem !== 'pipefy') show=false;
    if(BUSCA){
      const hay = (r.codigo+' '+r.acao+' '+r.quem+' '+r.responsavel+' '+(note?.content||'')).toLowerCase();
      if(!hay.includes(BUSCA)) show=false;
    }
    if(!show) continue;
    visible++;

    const isSapronOnly = r.origem === 'sapron_only';

    const tr = document.createElement('tr');
    tr.className = [
      isOK ? 'done' : r.categoria,
      isSapronOnly ? 'sapron_only' : '',
    ].filter(Boolean).join(' ');

    const checkedBy = isOK
      ? `<span class="checked-by">✔ ${escapeHtml(chk.user_name || chk.user_email.split('@')[0])}</span>`
      : '';
    const foraBadge = isSapronOnly
      ? `<span class="badge-fora" title="Fora da Fase 3 do Pipefy">◆ fora</span>`
      : '';
    const fase3Badge = r.origem === 'pipefy'
      ? `<span class="badge-fase3" title="Card da Fase 3 - Vistoria Inicial do PIPE 1">Fase 3</span>`
      : '';
    const primaryAct = primaryActivity(r.card_id);
    const codigoHtml = primaryAct && primaryAct.activity_id
      ? `<a class="codigo codigo-link" href="${SAPRON_ACTIVITY_URL(primaryAct.activity_id)}" target="_blank" rel="noopener" title="Abrir chamado no Sapron">${escapeHtml(r.codigo)}</a>`
      : `<span class="codigo">${escapeHtml(r.codigo)}</span>`;
    tr.innerHTML =
      `<td class="c">${visible}</td>`+
      `<td class="c"><input type="checkbox" class="chk" data-k="${escapeHtml(r.card_id)}" ${isOK?'checked':''}></td>`+
      `<td>${codigoHtml}${fase3Badge}${foraBadge}${checkedBy}</td>`+
      `<td>${escapeHtml(r.acao)}</td>`+
      `<td>${escapeHtml(r.quem)}</td>`+
      `<td>${escapeHtml(r.quando)}</td>`+
      `<td class="c"><span class="badge-abertos ${r.abertos>0?'hot':''}">${r.abertos}</span></td>`+
      `<td>${escapeHtml(r.responsavel)}</td>`+
      `<td>${escapeHtml(r.data_agendada)}</td>`+
      `<td><textarea class="obs" data-k="${escapeHtml(r.card_id)}" rows="1" placeholder="anotação compartilhada...">${escapeHtml(note?.content||'')}</textarea>`+
        (note ? `<div style="font-size:10px;color:var(--mute);margin-top:2px">por ${escapeHtml(note.user_name || note.user_email.split('@')[0])}</div>` : '')+
      `</td>`;
    tbody.appendChild(tr);
  }
  const N = CARDS.length;
  $('k-total').textContent = N;
  $('k-ok').textContent    = ok;
  $('k-pend').textContent  = pend;
  $('k-nossa').textContent = nossa;
  $('k-esp').textContent   = esp;
  const pct = N ? (ok/N*100) : 0;
  $('prog-label').textContent = `Progresso: ${pct.toFixed(1)}% concluído · ${pend} pendente(s) · ${visible} visível(is) no filtro`;
  $('prog-bar').style.width = pct+'%';
}

function renderMeta(){
  const when = META ? new Date(META.last_refresh) : null;
  $('sub').textContent =
    (when ? 'Atualizado em '+when.toLocaleString('pt-BR')+' · ' : '') +
    `${CARDS.length} cards · ${ACTS.length} chamados · sincronização ao vivo entre usuários @seazone.com.br`;
}

function renderDetalhes(){
  const q = ($('qd').value || '').toLowerCase();
  const tb = $('tbody-det'); tb.innerHTML = '';
  for(const d of ACTS){
    const hay = (d.codigo+' '+d.titulo+' '+d.autor+' '+d.email+' '+d.status).toLowerCase();
    if(q && !hay.includes(q)) continue;
    const tr = document.createElement('tr');
    tr.className = d.cat;
    const actLink = d.activity_id
      ? `<a href="${SAPRON_ACTIVITY_URL(d.activity_id)}" target="_blank" rel="noopener" title="Abrir chamado no Sapron">${escapeHtml(d.activity_id)}</a>`
      : '';
    tr.innerHTML =
      `<td><span class="codigo">${escapeHtml(d.codigo)}</span></td>`+
      `<td>${actLink}</td>`+
      `<td>${escapeHtml(d.titulo)}</td>`+
      `<td>${escapeHtml(d.status)}</td>`+
      `<td>${escapeHtml(d.ultima)}</td>`+
      `<td>${escapeHtml(d.autor)}</td>`+
      `<td>${escapeHtml(d.email)}</td>`+
      `<td>${escapeHtml(d.cat)}</td>`;
    tb.appendChild(tr);
  }
}

document.addEventListener('change', async e => {
  if(!e.target.classList.contains('chk')) return;
  const card_id = e.target.dataset.k;
  if(e.target.checked){
    const row = { card_id, user_email: ME.email, user_name: ME.name, checked_at: new Date().toISOString() };
    const { error } = await sb.from('pipefy_checks').upsert(row);
    if(error) return toast('Erro ao marcar: '+error.message);
    CHECKS.set(card_id, row);
  } else {
    const { error } = await sb.from('pipefy_checks').delete().eq('card_id', card_id);
    if(error) return toast('Erro ao desmarcar: '+error.message);
    CHECKS.delete(card_id);
  }
  render();
});

const noteTimers = new Map();
document.addEventListener('input', e => {
  if(!e.target.classList.contains('obs')) return;
  const card_id = e.target.dataset.k;
  const val = e.target.value;
  clearTimeout(noteTimers.get(card_id));
  noteTimers.set(card_id, setTimeout(async () => {
    const row = { card_id, content: val, user_email: ME.email, user_name: ME.name, updated_at: new Date().toISOString() };
    const { error } = await sb.from('pipefy_notes').upsert(row);
    if(error) return toast('Erro ao salvar OBS: '+error.message);
    NOTES.set(card_id, row);
  }, 700));
});

$('q').addEventListener('input', e => { BUSCA = e.target.value.toLowerCase(); render(); });
$('qd').addEventListener('input', renderDetalhes);
document.querySelectorAll('.chip').forEach(el => el.addEventListener('click', () => {
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  FILTRO = el.dataset.filter;
  render();
}));
document.querySelectorAll('.tab-btns button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tab-btns button').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  const tab = b.dataset.tab;
  $('tab-painel').classList.toggle('hidden', tab!=='painel');
  $('tab-detalhes').classList.toggle('hidden', tab!=='detalhes');
  if(tab==='detalhes') renderDetalhes();
}));
$('btn-refresh').addEventListener('click', () => loadAll().then(()=> toast('Dados recarregados')));

// debounce pra evitar refetch em rajada quando o Python faz upsert de várias linhas
let cardsRefetchTimer = null;
function scheduleCardsRefetch(){
  clearTimeout(cardsRefetchTimer);
  cardsRefetchTimer = setTimeout(async () => {
    await loadAll();
  }, 1500);
}

function subscribeRealtime(){
  // Cards e activities atualizados pelo refresh Python → recarrega tudo
  sb.channel('pipefy-cards')
    .on('postgres_changes', { event:'*', schema:'public', table:'pipefy_cards' }, () => scheduleCardsRefetch())
    .subscribe();
  sb.channel('pipefy-activities')
    .on('postgres_changes', { event:'*', schema:'public', table:'pipefy_activities' }, () => scheduleCardsRefetch())
    .subscribe();
  // Meta (timestamp do último refresh) — dispara mesmo quando nada mudou nos dados
  sb.channel('pipefy-meta')
    .on('postgres_changes', { event:'UPDATE', schema:'public', table:'pipefy_meta' }, () => scheduleCardsRefetch())
    .subscribe();

  // Safety net — a cada 90s pega meta pra detectar se realtime caiu
  setInterval(async () => {
    const { data } = await sb.from('pipefy_meta').select('*').eq('id', 1).single();
    if(!data || !META) return;
    if(new Date(data.last_refresh).getTime() > new Date(META.last_refresh).getTime()){
      scheduleCardsRefetch();
    }
  }, 90 * 1000);

  sb.channel('pipefy-checks')
    .on('postgres_changes', { event:'*', schema:'public', table:'pipefy_checks' }, payload => {
      const row = payload.new || payload.old;
      const card_id = row?.card_id; if(!card_id) return;
      if(payload.eventType === 'DELETE'){
        CHECKS.delete(card_id);
        if(row.user_email !== ME.email) toast(`${nameOf(row)} desmarcou ${labelOf(card_id)}`);
      } else {
        CHECKS.set(card_id, payload.new);
        if(payload.new.user_email !== ME.email) toast(`${nameOf(payload.new)} ✓ marcou ${labelOf(card_id)}`);
      }
      render();
    }).subscribe();

  sb.channel('pipefy-notes')
    .on('postgres_changes', { event:'*', schema:'public', table:'pipefy_notes' }, payload => {
      const row = payload.new || payload.old;
      const card_id = row?.card_id; if(!card_id) return;
      if(payload.eventType === 'DELETE'){ NOTES.delete(card_id); }
      else { NOTES.set(card_id, payload.new); }
      if(payload.new?.user_email !== ME.email && payload.eventType !== 'DELETE'){
        toast(`${nameOf(payload.new)} anotou em ${labelOf(card_id)}`);
      }
      updateNotesInDom(card_id);
    }).subscribe();

  const pres = sb.channel('pipefy-presence', { config:{ presence:{ key: ME.email } }});
  pres.on('presence', { event:'sync' }, () => renderOnline(pres.presenceState()))
      .subscribe(async status => {
        if(status === 'SUBSCRIBED') await pres.track({ name: ME.name, email: ME.email });
      });
}

function nameOf(row){ return row?.user_name || (row?.user_email||'').split('@')[0]; }
function labelOf(card_id){
  const c = CARDS.find(x => x.card_id === card_id);
  return c ? c.codigo : card_id;
}
function updateNotesInDom(card_id){
  const ta = document.querySelector(`textarea.obs[data-k="${CSS.escape(card_id)}"]`);
  if(!ta) return render();
  if(document.activeElement === ta) return;
  ta.value = NOTES.get(card_id)?.content || '';
}
function renderOnline(state){
  const others = Object.values(state).flat().filter(u => u.email !== ME.email);
  const box = $('online-avatars'); box.innerHTML = '';
  for(const u of others.slice(0, 5)){
    const div = document.createElement('div');
    div.className = 'avatar'; div.title = `${u.name} (${u.email})`;
    div.textContent = (u.name || u.email).split(' ').map(x=>x[0]).slice(0,2).join('').toUpperCase();
    box.appendChild(div);
  }
  if(others.length > 5){
    const div = document.createElement('div');
    div.className = 'avatar'; div.textContent = '+'+(others.length-5);
    div.style.background = 'var(--mute)';
    box.appendChild(div);
  }
}

if(ME && ME.email?.endsWith(CFG.allowedDomain)){ init(); } else { showLogin(); }
