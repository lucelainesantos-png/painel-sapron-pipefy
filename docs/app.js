/* Painel Sapron × Pipefy — versão colaborativa
 * - Login Google via Supabase Auth
 * - Restrição por domínio @seazone.com.br (RLS no servidor + guard no cliente)
 * - Realtime em pipefy_checks e pipefy_notes
 */

const CFG = window.PAINEL_CONFIG;
const sb = supabase.createClient(CFG.supabaseUrl, CFG.supabaseAnonKey, {
  auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
});

const $ = id => document.getElementById(id);
const escapeHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

// ============= estado local =============
let ME = null;          // { email, name }
let CARDS = [];         // rows do banco
let ACTS  = [];         // activities
let CHECKS = new Map(); // card_id -> {user_email, user_name, checked_at}
let NOTES  = new Map(); // card_id -> {content, user_email, user_name, updated_at}
let META = null;
let FILTRO = 'todos';
let BUSCA = '';

// ============= toast =============
function toast(msg, ms=3500){
  const t = document.createElement('div');
  t.className='toast'; t.textContent=msg;
  $('toast-container').appendChild(t);
  setTimeout(()=> t.remove(), ms);
}

// ============= auth =============
async function initAuth(){
  const { data:{ session } } = await sb.auth.getSession();
  if(!session) return showLogin();

  const email = (session.user.email || '').toLowerCase();
  if(!email.endsWith(CFG.allowedDomain)){
    await sb.auth.signOut();
    return showLogin(`Acesso permitido só para contas ${CFG.allowedDomain}. Você entrou como ${email}.`);
  }
  ME = {
    email,
    name: session.user.user_metadata?.full_name || session.user.user_metadata?.name || email.split('@')[0],
  };
  showApp();
}

function showLogin(errorMsg){
  $('login-screen').classList.remove('hidden');
  $('app').classList.add('hidden');
  const err = $('login-error');
  if(errorMsg){ err.textContent = errorMsg; err.classList.remove('hidden'); }
  else err.classList.add('hidden');
}

async function showApp(){
  $('login-screen').classList.add('hidden');
  $('app').classList.remove('hidden');
  const initials = (ME.name||ME.email).split(' ').map(x=>x[0]).slice(0,2).join('').toUpperCase();
  $('me-avatar').textContent = initials;
  $('me-name').textContent = ME.name;
  await loadAll();
  subscribeRealtime();
}

$('btn-login').addEventListener('click', async () => {
  const { error } = await sb.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: window.location.origin + window.location.pathname,
      queryParams: { hd: 'seazone.com.br', prompt: 'select_account' },
    }
  });
  if(error) alert('Erro no login: ' + error.message);
});
$('btn-logout').addEventListener('click', async () => {
  await sb.auth.signOut();
  location.reload();
});

// ============= carga inicial =============
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
  if(m.error) console.warn('meta', m.error);

  CARDS = c.data || [];
  ACTS  = a.data || [];
  CHECKS.clear(); (ch.data||[]).forEach(r => CHECKS.set(r.card_id, r));
  NOTES.clear();  (n.data ||[]).forEach(r => NOTES.set(r.card_id, r));
  META = m.data;

  // auto-desmarcar quem tem OK anterior à última mensagem do card
  const stale = [];
  for(const card of CARDS){
    const chk = CHECKS.get(card.card_id);
    if(!chk) continue;
    const ckMs = new Date(chk.checked_at).getTime();
    if((card.ultima_msg_ts||0) > ckMs){
      stale.push(card.card_id);
    }
  }
  if(stale.length){
    // remove local + servidor (só do próprio usuário — RLS permite deletar; se for de outro, pula)
    const mine = stale.filter(id => (CHECKS.get(id)||{}).user_email === ME.email);
    if(mine.length){
      const { error } = await sb.from('pipefy_checks').delete().in('card_id', mine);
      if(!error) mine.forEach(id => CHECKS.delete(id));
    }
    // dos outros usuários: NÃO deleto (só o dono pode). Deixo o check dele lá — a linha vai continuar amarela até ele reavaliar.
    if(stale.length !== mine.length){
      console.info(`${stale.length-mine.length} check(s) de outros usuários com mensagem nova — deixando pro dono desmarcar`);
    }
  }

  render();
  renderMeta();
}

function fatal(what, err){
  console.error(what, err);
  alert(`Erro ao carregar ${what}: ${err.message}`);
}

// ============= render =============
function render(){
  const tbody = $('tbody');
  tbody.innerHTML = '';
  let visible=0, ok=0, pend=0, nossa=0, esp=0;

  for(const r of CARDS){
    const chk = CHECKS.get(r.card_id);
    const note = NOTES.get(r.card_id);
    const isOK = !!chk;
    if(isOK) ok++; else pend++;
    if(!isOK && r.categoria==='nossa_vez') nossa++;
    if(!isOK && r.categoria==='esperando') esp++;

    let show = true;
    if(FILTRO==='pendentes' && isOK) show=false;
    if(['sem_activity','nossa_vez','sem_msg','esperando'].includes(FILTRO) && r.categoria!==FILTRO) show=false;
    if(BUSCA){
      const hay = (r.codigo+' '+r.acao+' '+r.quem+' '+r.responsavel+' '+(note?.content||'')).toLowerCase();
      if(!hay.includes(BUSCA)) show=false;
    }
    if(!show) continue;
    visible++;

    const tr = document.createElement('tr');
    tr.className = isOK ? 'done' : r.categoria;

    const checkedBy = isOK
      ? `<span class="checked-by">✔ ${escapeHtml(chk.user_name || chk.user_email.split('@')[0])}</span>`
      : '';

    tr.innerHTML =
      `<td class="c">${visible}</td>`+
      `<td class="c"><input type="checkbox" class="chk" data-k="${escapeHtml(r.card_id)}" ${isOK?'checked':''}></td>`+
      `<td><span class="codigo">${escapeHtml(r.codigo)}</span>${checkedBy}</td>`+
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
    `${CARDS.length} cards · ${ACTS.length} chamados · sync ao vivo entre usuários @seazone.com.br`;
}

function renderDetalhes(){
  const q = ($('qd').value || '').toLowerCase();
  const tb = $('tbody-det');
  tb.innerHTML = '';
  for(const d of ACTS){
    const hay = (d.codigo+' '+d.titulo+' '+d.autor+' '+d.email+' '+d.status).toLowerCase();
    if(q && !hay.includes(q)) continue;
    const tr = document.createElement('tr');
    tr.className = d.cat;
    tr.innerHTML =
      `<td><span class="codigo">${escapeHtml(d.codigo)}</span></td>`+
      `<td>${escapeHtml(d.activity_id)}</td>`+
      `<td>${escapeHtml(d.titulo)}</td>`+
      `<td>${escapeHtml(d.status)}</td>`+
      `<td>${escapeHtml(d.ultima)}</td>`+
      `<td>${escapeHtml(d.autor)}</td>`+
      `<td>${escapeHtml(d.email)}</td>`+
      `<td>${escapeHtml(d.cat)}</td>`;
    tb.appendChild(tr);
  }
}

// ============= interações =============
document.addEventListener('change', async e => {
  if(!e.target.classList.contains('chk')) return;
  const card_id = e.target.dataset.k;
  if(e.target.checked){
    const { error } = await sb.from('pipefy_checks').upsert({
      card_id, user_email: ME.email, user_name: ME.name, checked_at: new Date().toISOString()
    });
    if(error) return toast('Erro ao marcar: '+error.message);
    CHECKS.set(card_id, { card_id, user_email: ME.email, user_name: ME.name, checked_at: new Date().toISOString() });
  } else {
    const { error } = await sb.from('pipefy_checks').delete().eq('card_id', card_id);
    if(error) return toast('Erro ao desmarcar: '+error.message);
    CHECKS.delete(card_id);
  }
  render();
});

// OBS: debounce por card
const noteTimers = new Map();
document.addEventListener('input', e => {
  if(!e.target.classList.contains('obs')) return;
  const card_id = e.target.dataset.k;
  const val = e.target.value;
  clearTimeout(noteTimers.get(card_id));
  noteTimers.set(card_id, setTimeout(async () => {
    const { error } = await sb.from('pipefy_notes').upsert({
      card_id, content: val, user_email: ME.email, user_name: ME.name, updated_at: new Date().toISOString()
    });
    if(error) return toast('Erro ao salvar OBS: '+error.message);
    NOTES.set(card_id, { card_id, content: val, user_email: ME.email, user_name: ME.name, updated_at: new Date().toISOString() });
  }, 600));
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

// ============= realtime =============
function subscribeRealtime(){
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
    })
    .subscribe();

  sb.channel('pipefy-notes')
    .on('postgres_changes', { event:'*', schema:'public', table:'pipefy_notes' }, payload => {
      const row = payload.new || payload.old;
      const card_id = row?.card_id; if(!card_id) return;
      if(payload.eventType === 'DELETE'){ NOTES.delete(card_id); }
      else { NOTES.set(card_id, payload.new); }
      if(payload.new?.user_email !== ME.email && payload.eventType !== 'DELETE'){
        toast(`${nameOf(payload.new)} anotou em ${labelOf(card_id)}`);
      }
      // Re-render sem tocar em textareas em edição
      updateNotesInDom(card_id);
    })
    .subscribe();

  // Presença online — mostra quem mais tá vendo o painel
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
  if(!ta) return render();  // linha não visível → re-render total
  if(document.activeElement === ta) return;  // não sobreescrevo se o usuário está digitando
  ta.value = NOTES.get(card_id)?.content || '';
}

function renderOnline(state){
  const others = Object.values(state).flat().filter(u => u.email !== ME.email);
  const box = $('online-avatars');
  box.innerHTML = '';
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

// ============= start =============
initAuth();
