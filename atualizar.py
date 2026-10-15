"""Puxa o ranking.json do servidor (SFTP da Shockbyte), publica em docs/ e,
se houver webhook, atualiza a mensagem fixa do Discord.

Variaveis (segredos do GitHub):
  SFTP_HOST        ex.: sftp.shockbyte.sao1.xxxx (com ou sem "sftp://")
  SFTP_PORT        ex.: 2222
  SFTP_USER        usuario SFTP (de preferencia um SUBUSUARIO so com acesso a arquivos)
  SFTP_PASS        senha desse usuario
  RANKING_PATH     caminho do ranking.json a partir da raiz do SFTP
  DISCORD_WEBHOOK  (opcional) URL do webhook do canal #hiscores
  SITE_URL         (opcional) endereco da pagina, para o link no Discord
"""
import json
import os
import sys
import urllib.request

import paramiko

AQUI = os.path.dirname(os.path.abspath(__file__))
PUBLICO = os.path.join(AQUI, "docs", "ranking.json")
ESTADO = os.path.join(AQUI, "estado.json")


def env(nome, padrao=None):
    v = os.environ.get(nome, "").strip()
    if not v and padrao is None:
        sys.exit(f"Falta o segredo {nome}")
    return v or padrao


def listar(sftp, pasta):
    """listdir que ignora erro no CLOSE (a raiz da Shockbyte e uma 'lista de servidores'
    que aceita ler mas nao aceita fechar)."""
    from paramiko.sftp import CMD_CLOSE, CMD_HANDLE, CMD_NAME, CMD_OPENDIR, CMD_READDIR
    t, msg = sftp._request(CMD_OPENDIR, pasta)
    if t != CMD_HANDLE:
        raise IOError("sem handle")
    handle = msg.get_binary()
    nomes = []
    while True:
        try:
            t, msg = sftp._request(CMD_READDIR, handle)
        except EOFError:
            break
        if t != CMD_NAME:
            break
        for _ in range(msg.get_int()):
            nome = msg.get_text()
            longo = msg.get_text()
            paramiko.SFTPAttributes._from_msg(msg, nome, longo)
            if nome not in (".", ".."):
                nomes.append(nome)
    try:
        sftp._request(CMD_CLOSE, handle)
    except Exception:
        pass
    return sorted(nomes)


def baixar_ranking():
    host = env("SFTP_HOST").replace("sftp://", "").strip("/")
    porta = int(env("SFTP_PORT", "2222"))
    if ":" in host:
        host, p = host.rsplit(":", 1)
        porta = int(p)
    t = paramiko.Transport((host, porta))
    try:
        t.connect(username=env("SFTP_USER"), password=env("SFTP_PASS"))
        sftp = paramiko.SFTPClient.from_transport(t)
        caminho = env("RANKING_PATH")
        try:
            with sftp.open(caminho, "rb") as f:
                return f.read().decode("utf-8")
        except IOError as e:
            # mostra o que existe na raiz e no caminho ate onde der, para achar o nome certo
            print(f"Nao achei '{caminho}': {e}")
            for pasta in ("/", "."):
                try:
                    print(f"Conteudo de '{pasta}': {listar(sftp, pasta)}")
                except Exception as e2:
                    print(f"Nao consegui listar '{pasta}': {e2}")
            partes = caminho.strip("/").split("/")
            for i in range(1, len(partes)):
                sub = "/".join(partes[:i])
                try:
                    print(f"Conteudo de '{sub}': {listar(sftp, sub)}")
                except Exception:
                    print(f"'{sub}' nao existe")
                    break
            sys.exit("Ajuste o segredo RANKING_PATH usando a lista acima")
    finally:
        t.close()


def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dado):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dado, f, ensure_ascii=False, indent=1)
        f.write("\n")


def nivel_xp(s):
    return (s.get("nivel") or 0, s.get("xp") or 0)


def mensagem_discord(r):
    js = r.get("jogadores", [])
    linhas = ["```", f"{'#':>2}  {'Jogador':<16} {'Nível':>5} {'XP':>11}"]
    for i, j in enumerate(js[:10], 1):
        linhas.append(f"{i:>2}  {j['nome'][:16]:<16} {j['total_nivel']:>5} {j['total_xp']:>11,}".replace(",", "."))
    linhas.append("```")
    lideres = {}
    for j in js:
        for s in j.get("skills", []):
            atual = lideres.get(s["nome"])
            if (s.get("xp") or 0) > 0 and (atual is None or nivel_xp(s) > nivel_xp(atual[1])):
                lideres[s["nome"]] = (j["nome"], s)
    lid = [f"**{k}** — {v[0]} ({v[1].get('nivel')})" for k, v in sorted(lideres.items())]
    campos = [{"name": "Líder de cada skill", "value": "\n".join(lid)[:1024]}] if lid else []
    site = env("SITE_URL", "-")
    embed = {
        "title": "Hiscores do servidor",
        "description": "\n".join(linhas),
        "fields": campos,
        "footer": {"text": "Atualizado"},
        "timestamp": r.get("atualizado"),
        "color": 0xC8A24A,
    }
    if site != "-":
        embed["url"] = site
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


def discord(url, corpo, msg_id):
    def chamar(metodo, alvo):
        req = urllib.request.Request(alvo, data=json.dumps(corpo).encode(), method=metodo,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "hiscores-dragonwilds"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    base = url.split("?")[0]
    if msg_id:
        try:
            chamar("PATCH", f"{base}/messages/{msg_id}")
            return msg_id
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            print("Mensagem antiga do Discord sumiu; criando outra")
    return chamar("POST", base + "?wait=true")["id"]


def main():
    bruto = baixar_ranking()
    novo = json.loads(bruto)
    if not isinstance(novo.get("jogadores"), list):
        sys.exit("ranking.json sem a lista 'jogadores'; nada publicado")
    antigo = ler_json(PUBLICO, {})
    if antigo == novo:
        print("Ranking sem mudança")
        return
    gravar_json(PUBLICO, novo)
    print(f"Ranking publicado: {len(novo['jogadores'])} jogador(es), {novo.get('atualizado')}")

    url = env("DISCORD_WEBHOOK", "-")
    if url != "-":
        estado = ler_json(ESTADO, {})
        estado["discord_msg_id"] = discord(url, mensagem_discord(novo), estado.get("discord_msg_id"))
        gravar_json(ESTADO, estado)
        print("Discord atualizado")


if __name__ == "__main__":
    main()
