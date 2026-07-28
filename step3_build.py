"""
Passo 3: monta o painel HTML usando cards.json + sapron_result.json.

Regras de "quem tem que responder":
  última msg foi da franquia  → NOSSA VEZ de responder      (categoria='nossa_vez')
  última msg foi do time      → ESPERANDO franquia responder (categoria='esperando')
  chamado sem mensagens       → sem_msg
  card sem chamado no Sapron  → sem_activity

Cada row leva `ultima_msg_ts` (epoch em ms) → o JS do painel usa isso pra
desmarcar OK automaticamente quando chega resposta nova.

O progresso (OK/OBS) fica no localStorage do navegador, refresh não apaga.
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
CARDS = json.loads((BASE / 'cards.json').read_text(encoding='utf-8'))
SAPRON = json.loads((BASE / 'sapron_result.json').read_text(encoding='utf-8'))
TEMPLATE = (BASE / 'template.html').read_text(encoding='utf-8')

by_prop = defaultdict(list)
for r in SAPRON:
    by_prop[str(r['property_id'])].append(r)


def quem_respondeu(email):
    """Retorna 'franquia'|'time'|None conforme o e-mail do último autor."""
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


rows_out, detalhes = [], []

for c in CARDS:
    pid = (c.get('sapron_property_id') or '').strip()
    codigo = c.get('codigo_imovel') or c.get('codigo_imovel_field') or ''
    card_id = c.get('card_id') or ''
    responsavel = c.get('responsavel_pipefy') or ''
    data_ag = c.get('data_agendada') or ''
    fase = c.get('phase_name') or ''
    acts = by_prop.get(pid, [])

    if not acts:
        rows_out.append({
            'codigo': codigo, 'card_id': card_id, 'property_id': pid,
            'responsavel': responsavel, 'data_ag': data_ag, 'fase': fase,
            'categoria': 'sem_activity', 'acao': 'Sem chamado no Sapron',
            'quem': '', 'quando': '', 'quando_iso': '', 'ultima_msg_ts': 0,
            'abertos': 0, 'total': 0,
        })
        detalhes.append({
            'codigo': codigo, 'card_id': card_id, 'pid': pid, 'fase': fase,
            'activity_id': '', 'titulo': '', 'status': 'SEM ACTIVITY',
            'ultima': '', 'autor': '', 'email': '', 'cat': 'sem_activity',
        })
        continue

    def k(a):
        dt = parse_dt(a.get('ultima_msg_at'))
        return (dt is None, -(dt.timestamp() if dt else 0), -int(a.get('activity_id') or 0))
    acts = sorted(acts, key=k)
    a0 = acts[0]
    dt0 = parse_dt(a0.get('ultima_msg_at'))
    respondeu = quem_respondeu(a0.get('ultimo_autor_email'))

    if respondeu == 'franquia':
        cat, acao = 'nossa_vez', 'Nossa vez de responder'
    elif respondeu == 'time':
        cat, acao = 'esperando', 'Esperando franquia responder'
    else:
        cat, acao = 'sem_msg', 'Chamado aberto, sem mensagens'

    rows_out.append({
        'codigo': codigo, 'card_id': card_id, 'property_id': pid,
        'responsavel': responsavel, 'data_ag': data_ag, 'fase': fase,
        'categoria': cat, 'acao': acao,
        'quem': (a0.get('ultimo_autor_nome') or '').strip(),
        'quando': fmt_dt(dt0),
        'quando_iso': dt0.isoformat() if dt0 else '',
        'ultima_msg_ts': ts_ms(dt0),
        'abertos': sum(1 for a in acts if (a.get('activity_status') or '').lower()
                       in ('aberto', 'andamento', 'aguardando')),
        'total': len(acts),
    })

    for a in acts:
        dta = parse_dt(a.get('ultima_msg_at'))
        respondeu_a = quem_respondeu(a.get('ultimo_autor_email'))
        cat_a = ('nossa_vez' if respondeu_a == 'franquia'
                 else 'esperando' if respondeu_a == 'time'
                 else 'sem_msg')
        detalhes.append({
            'codigo': codigo, 'card_id': card_id, 'pid': pid, 'fase': fase,
            'activity_id': a.get('activity_id', ''),
            'titulo': a.get('activity_title', ''),
            'status': a.get('activity_status', ''),
            'ultima': fmt_dt(dta),
            'autor': (a.get('ultimo_autor_nome') or '').strip(),
            'email': (a.get('ultimo_autor_email') or '').strip(),
            'cat': cat_a,
        })


# Ordem de prioridade: quem exige nossa ação primeiro
ordem = {'sem_activity': 0, 'nossa_vez': 1, 'sem_msg': 2, 'esperando': 3}
rows_out.sort(key=lambda r: (ordem.get(r['categoria'], 9), r['quando_iso'] or 'ZZ', r['codigo']))

dados = {'rows': rows_out, 'detalhes': detalhes, 'gerado': datetime.now().isoformat()}
(BASE / 'dados.json').write_text(json.dumps(dados, ensure_ascii=False), encoding='utf-8')

html = TEMPLATE.replace('__DADOS__', json.dumps(dados, ensure_ascii=False))
(BASE / 'painel.html').write_text(html, encoding='utf-8')

from collections import Counter
c = Counter(r['categoria'] for r in rows_out)
print(f'OK: {len(rows_out)} imóveis | {len(detalhes)} chamados | {dict(c)}')
