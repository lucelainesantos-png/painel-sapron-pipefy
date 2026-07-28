"""
Passo 3 (RPC): publica cards + activities no Supabase via funções RPC
protegidas por segredo. Não precisa de service_role key.
"""
import json, sys, urllib.request, urllib.error
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


def build_rows():
    cards = json.loads(CARDS_JSON.read_text(encoding='utf-8'))
    sap = json.loads(SAPRON_JSON.read_text(encoding='utf-8'))
    by_prop = defaultdict(list)
    for r in sap:
        by_prop[str(r['property_id'])].append(r)

    rows, acts = [], []
    for c in cards:
        pid = (c.get('sapron_property_id') or '').strip()
        codigo = c.get('codigo_imovel') or c.get('codigo_imovel_field') or ''
        card_id = c.get('card_id') or ''
        responsavel = c.get('responsavel_pipefy') or ''
        data_ag = c.get('data_agendada') or ''
        fase = c.get('phase_name') or ''
        card_acts = by_prop.get(pid, [])

        if not card_acts:
            rows.append({
                'card_id': card_id, 'codigo': codigo, 'property_id': pid,
                'responsavel': responsavel, 'data_agendada': data_ag, 'fase': fase,
                'categoria': 'sem_activity', 'acao': 'Sem chamado no Sapron',
                'quem': '', 'quando': '', 'ultima_msg_ts': 0,
                'abertos': 0, 'total': 0,
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

    ordem = {'sem_activity':0,'nossa_vez':1,'sem_msg':2,'esperando':3}
    rows.sort(key=lambda r: (ordem.get(r['categoria'],9), (r.get('quando') or 'ZZ'), r['codigo']))
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
    print(f'OK: {len(rows)} cards | {len(acts)} chamados | {dict(c)}')


if __name__ == '__main__':
    main()
