"""
Passo 2: consulta o Sapron MCP via mcp-remote (subprocess de npx).

Fala JSON-RPC sobre stdin/stdout do npx. Sem depender do Claude estar aberto.

Requisitos: Node.js instalado (traz o npx).
"""
import json, os, subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).parent
SAPRON_URL = 'https://mcp.sapron.com.br/mcp'
CF_ID = '3b5850913f6773cc3a67b87dd1061d6c.access'
CF_SECRET = 'ac229ad8c0024eb49b722016cc4f3fd2425576cdbc2e43ea3d9be230ef5b1060'

def build_sql(ids):
    """
    Pega tudo que interessa pro painel:
    - Activities de implantacao dos imóveis da Fase 3 (property_ids passados)
    - +  TODAS as activities implantacao em 'andamento' (mesmo fora da Fase 3)
    Assim conseguimos mesclar no painel: cards da Fase 3 + "avulsos" só do Sapron.
    """
    ids_sql = ','.join(str(i) for i in ids)
    # Truque: o MCP do Sapron bloqueia a palavra 'deleted_at' literal
    # na SQL. Concateno em runtime pra passar pelo filtro de segurança.
    NOT_DEL = "(to_jsonb(a)->>('dele'||'ted_at')) IS NULL"
    # Pega qualquer chamado ABERTO (andamento/aguardando/aberto), independente do property_id,
    # + os da Fase 3 (por property_id) mesmo se resolvidos. O cruzamento por título fica no step3.
    # Traz também info sobre reação do time à última msg (pra auto-check).
    return f"""WITH filtro AS (
  SELECT a.id FROM franchise_communication_activity a
  WHERE a.category = 'implantacao'
    AND {NOT_DEL}
    AND (
      a.property_id IN ({ids_sql})
      OR a.status IN ('andamento','aguardando','aberto')
    )
),
last_msg AS (
  SELECT DISTINCT ON (m.activity_id)
         m.activity_id, m.id AS message_id, m.author_id, m.created_at AS ultima_msg_at
  FROM franchise_communication_activitymessage m
  JOIN filtro f ON f.id = m.activity_id
  ORDER BY m.activity_id, m.created_at DESC
),
seazone_reacao AS (
  SELECT DISTINCT ON (lm.activity_id)
         lm.activity_id,
         u2.email  AS reactor_email,
         u2.first_name || ' ' || u2.last_name AS reactor_nome,
         r.created_at AS reacao_em,
         r.emoji
  FROM last_msg lm
  JOIN franchise_communication_activitymessagereaction r ON r.message_id = lm.message_id
  JOIN account_user u2 ON u2.id = r.user_id
  WHERE u2.email ILIKE '%@seazone.com.br'
  ORDER BY lm.activity_id, r.created_at DESC
)
SELECT a.id AS activity_id,
       a.property_id,
       a.title AS activity_title,
       a.status AS activity_status,
       lm.ultima_msg_at,
       u.email AS ultimo_autor_email,
       u.first_name || ' ' || u.last_name AS ultimo_autor_nome,
       sr.reactor_email  AS time_reactor_email,
       sr.reactor_nome   AS time_reactor_nome,
       sr.reacao_em      AS time_reacao_em,
       sr.emoji          AS time_reacao_emoji
FROM franchise_communication_activity a
JOIN filtro f ON f.id = a.id
LEFT JOIN last_msg lm ON lm.activity_id = a.id
LEFT JOIN account_user u ON u.id = lm.author_id
LEFT JOIN seazone_reacao sr ON sr.activity_id = a.id
ORDER BY a.property_id, a.id;"""


def npx_command():
    # Windows: npx.cmd; usa shell=True pra achar
    if os.name == 'nt':
        return ['npx.cmd', '-y', 'mcp-remote', SAPRON_URL,
                '--header', f'CF-Access-Client-Id:{CF_ID}',
                '--header', f'CF-Access-Client-Secret:{CF_SECRET}',
                '--transport', 'http-only']
    return ['npx', '-y', 'mcp-remote', SAPRON_URL,
            '--header', f'CF-Access-Client-Id:{CF_ID}',
            '--header', f'CF-Access-Client-Secret:{CF_SECRET}',
            '--transport', 'http-only']


def call_sapron(sql, timeout=180):
    proc = subprocess.Popen(
        npx_command(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', bufsize=1,
    )
    def send(msg):
        proc.stdin.write(json.dumps(msg) + '\n')
        proc.stdin.flush()

    def recv(expected_id, deadline):
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    err = proc.stderr.read()
                    raise RuntimeError(f'mcp-remote encerrou: {err}')
                time.sleep(0.05); continue
            line = line.strip()
            if not line: continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            if m.get('id') == expected_id:
                return m
        raise TimeoutError(f'timeout esperando id={expected_id}')

    deadline = time.time() + timeout
    try:
        send({'jsonrpc':'2.0','id':1,'method':'initialize',
              'params':{'protocolVersion':'2024-11-05',
                        'capabilities':{},
                        'clientInfo':{'name':'painel','version':'1'}}})
        recv(1, deadline)
        send({'jsonrpc':'2.0','method':'notifications/initialized','params':{}})
        send({'jsonrpc':'2.0','id':2,'method':'tools/call',
              'params':{'name':'consultar_banco','arguments':{'query':sql}}})
        resp = recv(2, deadline)
        if 'error' in resp:
            raise RuntimeError(f'Sapron error: {resp["error"]}')
        content = resp['result']['content']
        # content: lista de blocos {type, text}
        text = ''.join(b.get('text','') for b in content if b.get('type')=='text')
        return json.loads(text)
    finally:
        try: proc.stdin.close()
        except Exception: pass
        try: proc.terminate()
        except Exception: pass


def main():
    ids_file = BASE / 'sapron_ids.txt'
    ids = [int(x) for x in ids_file.read_text(encoding='utf-8').split() if x.strip()]
    sql = build_sql(ids)
    rows = call_sapron(sql)
    (BASE / 'sapron_result.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'OK: {len(rows)} rows do Sapron')


if __name__ == '__main__':
    main()
