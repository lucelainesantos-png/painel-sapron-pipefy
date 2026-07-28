"""
Passo 3 (nova versão): publica cards + activities no Supabase.

Substitui o antigo step3_build.py, que gerava HTML local.
O painel Web agora lê tudo do Supabase; checks/OBS ficam lá também
e sincronizam em tempo real entre os usuários @seazone.com.br.
"""
import json, sys, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import BASE, SUPABASE_URL, SUPABASE_SERVICE_KEY

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
                'quem': '', 'quando': '', 'quando_iso': '', 'ultima_msg_ts': 0,
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
            'quando_iso': dt0.isoformat() if dt0 else '',
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
                'card_id': card_id,
                'codigo': codigo,
                'property_id': pid,
                'titulo': a.get('activity_title','') or '',
                'status': a.get('activity_status','') or '',
                'ultima': fmt_dt(dta),
                'ultima_ts': ts_ms(dta),
                'autor': (a.get('ultimo_autor_nome') or '').strip(),
                'email': (a.get('ultimo_autor_email') or '').strip(),
                'cat': cat_a,
            })

    # ordena e stampa order_idx
    ordem = {'sem_activity':0,'nossa_vez':1,'sem_msg':2,'esperando':3}
    rows.sort(key=lambda r: (ordem.get(r['categoria'],9), r['quando_iso'] or 'ZZ', r['codigo']))
    for i, r in enumerate(rows):
        r['order_idx'] = i
        # quando_iso não vai pro banco (não temos coluna)
        r.pop('quando_iso', None)

    return rows, acts


def http_json(method, path, body=None, extra_headers=None):
    url = f'{SUPABASE_URL}/rest/v1/{path}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {
        'apikey': SUPABASE_SERVICE_KEY,
        'Authorization': f'Bearer {SUPABASE_SERVICE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'HTTP {e.code} em {method} {path}: {e.read().decode("utf-8", errors="replace")}') from e


def upsert_batch(table, rows, on_conflict):
    if not rows: return
    # PostgREST upsert: POST /table com Prefer resolution=merge-duplicates + on_conflict via query
    path = f'{table}?on_conflict={on_conflict}'
    http_json('POST', path, rows, extra_headers={'Prefer': 'resolution=merge-duplicates,return=minimal'})


def delete_stale(table, keep_ids, key_col):
    # Deleta linhas cujo key não está em keep_ids
    # PostgREST: DELETE /table?key=not.in.(a,b,c)
    if not keep_ids:
        # esvazia tudo
        http_json('DELETE', f'{table}?{key_col}=neq.__NEVER__')
        return
    # PostgREST tem limite de URL; se muitos ids, faz em blocos
    keep_list = list(keep_ids)
    CHUNK = 300
    for i in range(0, len(keep_list), CHUNK):
        chunk = keep_list[i:i+CHUNK]
        # Estratégia: em vez de not.in de todos, deleta em batches por chunk NÃO listado é inviável.
        # Melhor: uma passada só. Se muito grande, fallback: buscar todos, comparar, deletar por id.
        if i == 0 and len(keep_list) <= CHUNK:
            expr = 'not.in.(' + ','.join(f'"{k}"' for k in chunk) + ')'
            http_json('DELETE', f'{table}?{key_col}={expr}')
            return
    # muitos ids: usa a via alternativa
    _, body = http_json('GET', f'{table}?select={key_col}&limit=100000',
                        extra_headers={'Prefer': ''})
    existing = {row[key_col] for row in json.loads(body)}
    to_delete = list(existing - set(keep_ids))
    for i in range(0, len(to_delete), CHUNK):
        chunk = to_delete[i:i+CHUNK]
        expr = 'in.(' + ','.join(f'"{k}"' for k in chunk) + ')'
        http_json('DELETE', f'{table}?{key_col}={expr}')


def stamp_meta():
    http_json('PATCH', 'pipefy_meta?id=eq.1', {'last_refresh': datetime.now().isoformat()})


def main():
    rows, acts = build_rows()
    print(f'Preparado: {len(rows)} cards, {len(acts)} activities')

    # 1) upsert cards
    CHUNK = 200
    for i in range(0, len(rows), CHUNK):
        upsert_batch('pipefy_cards', rows[i:i+CHUNK], on_conflict='card_id')
    print(f'✓ pipefy_cards upsert')

    # 2) delete cards sumidos
    keep_cards = {r['card_id'] for r in rows}
    delete_stale('pipefy_cards', keep_cards, 'card_id')
    print(f'✓ pipefy_cards clean-up')

    # 3) upsert activities
    if acts:
        for i in range(0, len(acts), CHUNK):
            upsert_batch('pipefy_activities', acts[i:i+CHUNK], on_conflict='activity_id')
    print(f'✓ pipefy_activities upsert')

    keep_acts = {a['activity_id'] for a in acts if a['activity_id']}
    delete_stale('pipefy_activities', keep_acts, 'activity_id')
    print(f'✓ pipefy_activities clean-up')

    stamp_meta()
    from collections import Counter
    c = Counter(r['categoria'] for r in rows)
    print(f'OK: {len(rows)} cards | {len(acts)} chamados | {dict(c)}')


if __name__ == '__main__':
    main()
