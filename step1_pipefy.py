"""
Passo 1 do refresh: baixa os cards da Fase 3 - Vistoria Inicial do
PIPE 1 (Implantação/Mãe id 303781436). Gera cards.json + sapron_ids.txt.
"""
import json, os, sys, urllib.request, urllib.error, csv, time
from pathlib import Path

BASE = Path(__file__).parent
TOKEN = (BASE / '.pipefy_token').read_text(encoding='utf-8').strip()
PIPE_ID = 303781436
PHASE_ID = 323044784  # Fase 3 - Vistoria Inicial
PHASE_NAME = 'Fase 3 - Vistoria Inicial'


def gql(query, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                'https://api.pipefy.com/graphql',
                data=json.dumps({'query': query}).encode('utf-8'),
                headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read().decode('utf-8')
            data = json.loads(body)
            if 'errors' in data:
                raise RuntimeError(f'Pipefy errors: {data["errors"]}')
            return data
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def list_phases(pipe_id):
    q = '{ pipe(id: %d) { phases { id name } } }' % pipe_id
    return gql(q)['data']['pipe']['phases']


def fetch_phase_cards(phase_id):
    cursor, nodes = None, []
    for page in range(1, 30):
        after = f', after: "{cursor}"' if cursor else ''
        q = ('{ phase(id: %s) { cards(first: 50%s) { pageInfo { hasNextPage endCursor } '
             'edges { node { id title assignees { name email } '
             'fields { name value } } } } } }' % (phase_id, after))
        conn = gql(q)['data']['phase']['cards']
        nodes.extend(e['node'] for e in conn['edges'])
        if not conn['pageInfo']['hasNextPage']:
            break
        cursor = conn['pageInfo']['endCursor']
    return nodes


def extract(node, phase_id, phase_name):
    f = {x['name']: x['value'] for x in node.get('fields', [])}
    codigo = (f.get('Código do imóvel') or f.get('Codigo do imovel')
              or f.get('Código') or f.get('Codigo') or node.get('title', '')).strip()
    codigo_field = f.get('Código do imóvel') or ''
    prop_id = (f.get('ID imóvel no SAPRON') or f.get('ID imovel no SAPRON')
               or f.get('property_id') or '').strip()
    assignees = node.get('assignees') or []
    resp = assignees[0]['name'] if assignees else ''
    data_ag = (f.get('Data agendada da vistoria') or f.get('Data agendada')
               or f.get('Data e hora agendada da vistoria') or '')
    return {
        'card_id': node['id'],
        'codigo_imovel': codigo,
        'codigo_imovel_field': codigo_field,
        'sapron_property_id': prop_id,
        'responsavel_pipefy': resp,
        'data_agendada': data_ag,
        'phase_id': str(phase_id),
        'phase_name': phase_name,
    }


def main():
    nodes = fetch_phase_cards(PHASE_ID)
    print(f'Fase {PHASE_NAME!r}: {len(nodes)} cards')
    rows, ids = [], set()
    for n in nodes:
        r = extract(n, PHASE_ID, PHASE_NAME)
        rows.append(r)
        if r['sapron_property_id'].isdigit():
            ids.add(int(r['sapron_property_id']))
    ids = sorted(ids)

    with (BASE / 'cards_extracted.csv').open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['card_id','codigo_imovel','codigo_imovel_field',
                                          'sapron_property_id','responsavel_pipefy',
                                          'data_agendada','phase_id','phase_name'])
        w.writeheader(); w.writerows(rows)
    (BASE / 'sapron_ids.txt').write_text('\n'.join(str(i) for i in ids) + '\n', encoding='utf-8')
    (BASE / 'cards.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'OK: {len(rows)} cards no total, {len(ids)} property_ids únicos')


if __name__ == '__main__':
    main()
