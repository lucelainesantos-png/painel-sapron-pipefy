"""
Passo 3 (RPC): publica cards + activities no Supabase via funções RPC
protegidas por segredo. Sem service_role key.

Cards são a união de:
  1. Cards do Pipefy Fase 3 (com/sem chamado)
  2. Activities do Sapron em 'andamento' cujo imóvel NÃO está na Fase 3
     (criam cards "avulsos" só do Sapron)

Marca `chamados_andamento`: quantas activities em andamento cada imóvel tem.
Front usa esse número pra destacar duplicados (> 1).
"""
import json, re, sys, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BASE, SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_REFRESH_SECRET

CARDS_JSON = BASE / 'cards.json'
SAPRON_JSON = BASE / 'sapron_result.json'


def quem_respondeu(email):
    if not email: return None
    return 'time' if email.lower().endswith('@seazone.com.br') else 'franquia'


def parse_dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception: return None


def fmt_dt(dt):
    return dt.strftime('%d/%m/%Y %H:%M') if dt else ''


def ts_ms(dt):
    return int(dt.timestamp() * 1000) if dt else 0


CODE_RE = re.compile(r'^\s*([A-Z]{2,6}\d{3,5})', re.IGNORECASE)


def extract_codigo_from_title(title):
    """Do title 'VIL0023 - Implantação novo imóvel' extrai 'VIL0023'."""
    if not title: return ''
    m = CODE_RE.match(title)
    if m: return m.group(1).upper()
    # fallback: primeiro token
    first = title.split(' ', 1)[0].split('-', 1)[0].strip().upper()
    return first if first else ''


def build_rows():
    cards = json.loads(CARDS_JSON.read_text(encoding='utf-8'))
    sap = json.loads(SAPRON_JSON.read_text(encoding='utf-8'))

    # index activities por property_id (para os que TÊM property_id vinculado)
    by_prop = defaultdict(list)
    for r in sap:
        pid = r.get('property_id')
        if pid: by_prop[str(pid)].append(r)

    # index por código do título — fallback quando property_id é null ou não bate
    by_code = defaultdict(list)
    for r in sap:
        code = extract_codigo_from_title(r.get('activity_title'))
        if code: by_code[code].append(r)

    # contagem de activities em 'andamento' por property_id E por código
    andamento_by_prop = defaultdict(int)
    andamento_by_code = defaultdict(int)
    for r in sap:
        if (r.get('activity_status') or '').lower() == 'andamento':
            pid = r.get('property_id')
            if pid: andamento_by_prop[str(pid)] += 1
            code = extract_codigo_from_title(r.get('activity_title'))
            if code: andamento_by_code[code] += 1

    # códigos já cobertos pelo Pipefy (pra não duplicar)
    pipefy_property_ids = set()
    pipefy_codigos = set()
    for c in cards:
        pid = (c.get('sapron_property_id') or '').strip()
        cod = (c.get('codigo_imovel') or c.get('codigo_imovel_field') or '').strip().upper()
        if pid: pipefy_property_ids.add(pid)
        if cod: pipefy_codigos.add(cod)

    rows, acts = [], []

    # =========== 1) cards do Pipefy Fase 3 ===========
    for c in cards:
        pid = (c.get('sapron_property_id') or '').strip()
        codigo = (c.get('codigo_imovel') or c.get('codigo_imovel_field') or '').strip().upper()
        card_id = c.get('card_id') or ''
        responsavel = c.get('responsavel_pipefy') or ''
        data_ag = c.get('data_agendada') or ''
        fase = c.get('phase_name') or ''

        # 1º tenta cruzamento por property_id; 2º fallback por código no título
        card_acts = list(by_prop.get(pid, [])) if pid else []
        if not card_acts and codigo:
            # activities cujo title começa com o código — dedup por activity_id
            seen_ids = set()
            for a in by_code.get(codigo, []):
                aid = a.get('activity_id')
                if aid in seen_ids: continue
                seen_ids.add(aid)
                card_acts.append(a)

        # contagem de "andamento" pra este card (pela via que deu match)
        cham_and = andamento_by_prop.get(pid, 0) if pid and by_prop.get(pid) else andamento_by_code.get(codigo, 0)

        if not card_acts:
            rows.append({
                'card_id': card_id, 'codigo': codigo, 'property_id': pid,
                'responsavel': responsavel, 'data_agendada': data_ag, 'fase': fase,
                'categoria': 'sem_activity', 'acao': 'Sem chamado no Sapron',
                'quem': '', 'quando': '', 'ultima_msg_ts': 0,
                'abertos': 0, 'total': 0,
                'origem': 'pipefy', 'chamados_andamento': cham_and,
            })
            continue

        def key(a):
            dt = parse_dt(a.get('ultima_msg_at'))
            return (dt is None, -(dt.timestamp() if dt else 0), -int(a.get('activity_id') or 0))
        card_acts_sorted = sorted(card_acts, key=key)
        a0 = card_acts_sorted[0]
        dt0 = parse_dt(a0.get('ultima_msg_at'))
        respondeu = quem_respondeu(a0.get('ultimo_autor_email'))

        if respondeu == 'franquia':
            cat, acao = 'nossa_vez', 'Nossa vez de responder'
        elif respondeu == 'time':
            cat, acao = 'esperando', 'Esperando franquia responder'
        else:
            cat, acao = 'sem_msg', 'Chamado aberto, sem mensagens'

        rows.append({
            'card_id': card_id, 'codigo': codigo, 'property_id': pid,
            'responsavel': responsavel, 'data_agendada': data_ag, 'fase': fase,
            'categoria': cat, 'acao': acao,
            'quem': (a0.get('ultimo_autor_nome') or '').strip(),
            'quando': fmt_dt(dt0),
            'ultima_msg_ts': ts_ms(dt0),
            'abertos': sum(1 for a in card_acts if (a.get('activity_status') or '').lower()
                           in ('aberto','andamento','aguardando')),
            'total': len(card_acts),
            'origem': 'pipefy',
            'chamados_andamento': cham_and,
        })

        for a in card_acts_sorted:
            dta = parse_dt(a.get('ultima_msg_at'))
            respondeu_a = quem_respondeu(a.get('ultimo_autor_email'))
            cat_a = ('nossa_vez' if respondeu_a == 'franquia'
                     else 'esperando' if respondeu_a == 'time'
                     else 'sem_msg')
            acts.append({
                'activity_id': str(a.get('activity_id','')),
                'card_id': card_id, 'codigo': codigo, 'property_id': pid,
                'titulo': a.get('activity_title','') or '',
                'status': a.get('activity_status','') or '',
                'ultima': fmt_dt(dta),
                'ultima_ts': ts_ms(dta),
                'autor': (a.get('ultimo_autor_nome') or '').strip(),
                'email': (a.get('ultimo_autor_email') or '').strip(),
                'cat': cat_a,
            })

    # =========== 2) cards "só Sapron" (property_ids em andamento fora da Fase 3) ===========
    # Agrupa activities avulsas por property_id
    avulsos = defaultdict(list)
    for a in sap:
        pid = str(a['property_id'])
        if pid in pipefy_property_ids: continue
        # pega só as em andamento (que interessam pro user)
        if (a.get('activity_status') or '').lower() != 'andamento': continue
        avulsos[pid].append(a)

    for pid, act_list in avulsos.items():
        # define código a partir do title da activity com mensagem mais recente
        def key(a):
            dt = parse_dt(a.get('ultima_msg_at'))
            return (dt is None, -(dt.timestamp() if dt else 0), -int(a.get('activity_id') or 0))
        act_list_sorted = sorted(act_list, key=key)
        a0 = act_list_sorted[0]
        codigo = extract_codigo_from_title(a0.get('activity_title'))
        if not codigo or codigo in pipefy_codigos:
            # já coberto pelo Pipefy via código (property_id divergente) — pula
            continue

        dt0 = parse_dt(a0.get('ultima_msg_at'))
        respondeu = quem_respondeu(a0.get('ultimo_autor_email'))
        if respondeu == 'franquia':
            cat, acao = 'nossa_vez', 'Nossa vez de responder'
        elif respondeu == 'time':
            cat, acao = 'esperando', 'Esperando franquia responder'
        else:
            cat, acao = 'sem_msg', 'Chamado aberto, sem mensagens'

        rows.append({
            'card_id': f'sapron_{pid}',
            'codigo': codigo, 'property_id': pid,
            'responsavel': '', 'data_agendada': '',
            'fase': '(fora da Fase 3)',
            'categoria': cat, 'acao': acao,
            'quem': (a0.get('ultimo_autor_nome') or '').strip(),
            'quando': fmt_dt(dt0),
            'ultima_msg_ts': ts_ms(dt0),
            'abertos': sum(1 for a in act_list if (a.get('activity_status') or '').lower()
                           in ('aberto','andamento','aguardando')),
            'total': len(act_list),
            'origem': 'sapron_only',
            'chamados_andamento': andamento_by_prop.get(pid, 0),
        })

        for a in act_list_sorted:
            dta = parse_dt(a.get('ultima_msg_at'))
            respondeu_a = quem_respondeu(a.get('ultimo_autor_email'))
            cat_a = ('nossa_vez' if respondeu_a == 'franquia'
                     else 'esperando' if respondeu_a == 'time'
                     else 'sem_msg')
            acts.append({
                'activity_id': str(a.get('activity_id','')),
                'card_id': f'sapron_{pid}', 'codigo': codigo, 'property_id': pid,
                'titulo': a.get('activity_title','') or '',
                'status': a.get('activity_status','') or '',
                'ultima': fmt_dt(dta),
                'ultima_ts': ts_ms(dta),
                'autor': (a.get('ultimo_autor_nome') or '').strip(),
                'email': (a.get('ultimo_autor_email') or '').strip(),
                'cat': cat_a,
            })

    # ordena: sem_activity (0), duplicado (1), nossa_vez (2), sem_msg (3), esperando (4)
    def sort_key(r):
        if r['categoria'] == 'sem_activity': cat_ord = 0
        elif r.get('chamados_andamento', 0) > 1: cat_ord = 1  # duplicados subindo
        elif r['categoria'] == 'nossa_vez':      cat_ord = 2
        elif r['categoria'] == 'sem_msg':        cat_ord = 3
        else:                                    cat_ord = 4
        return (cat_ord, r.get('quando') or 'ZZ', r['codigo'])
    rows.sort(key=sort_key)
    for i, r in enumerate(rows):
        r['order_idx'] = i
    return rows, acts


def rpc(fn, payload):
    url = f'{SUPABASE_URL}/rest/v1/rpc/{fn}'
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST',
        headers={
            'apikey': SUPABASE_ANON_KEY,
            'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
            'Content-Type': 'application/json',
        })
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'RPC {fn} HTTP {e.code}: {e.read().decode("utf-8", errors="replace")}')


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]


def main():
    rows, acts = build_rows()
    print(f'Preparado: {len(rows)} cards, {len(acts)} activities')

    total = 0
    for batch in chunked(rows, 200):
        total += rpc('pipefy_upsert_cards', {'secret': SUPABASE_REFRESH_SECRET, 'rows': batch})
    print(f'  cards upsertados: {total}')

    total_a = 0
    for batch in chunked(acts, 300):
        total_a += rpc('pipefy_upsert_activities', {'secret': SUPABASE_REFRESH_SECRET, 'rows': batch})
    print(f'  activities upsertadas: {total_a}')

    keep_cards = [r['card_id'] for r in rows]
    keep_acts = [a['activity_id'] for a in acts if a['activity_id']]
    prune = rpc('pipefy_prune', {
        'secret': SUPABASE_REFRESH_SECRET,
        'keep_cards': keep_cards,
        'keep_activities': keep_acts,
    })
    print(f'  prune: {prune}')

    from collections import Counter
    c = Counter(r['categoria'] for r in rows)
    o = Counter(r['origem'] for r in rows)
    d = sum(1 for r in rows if r.get('chamados_andamento', 0) > 1)
    print(f'OK: {len(rows)} cards | {len(acts)} chamados | categorias={dict(c)} | origem={dict(o)} | duplicados={d}')


if __name__ == '__main__':
    main()
