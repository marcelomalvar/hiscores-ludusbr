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

excluir.txt (opcional, na raiz do repositorio): contas que NAO aparecem no ranking.
  Um GUID por linha; o que vem depois de # e comentario. Editar pelo site do GitHub
  ja vale na proxima execucao. O registro no servidor continua intacto.

docs/semana.json: ganhos da semana (segunda 00:00 de Brasilia ate a segunda seguinte).
  A cada execucao compara o XP de cada skill com o da execucao anterior. O tempo de jogo
  sai do campo "data" do registro (o servidor regrava a cada ~5 min enquanto o jogador
  esta online): o intervalo entre duas gravacoes vistas conta como tempo jogado e vale
  para cada skill que ganhou XP nele. Se o jogador estava offline na execucao anterior,
  conta so a partir dela (nao a pausa); e nunca mais de 45 min por intervalo. Na virada da semana o resultado vai para docs/semanas/<data>.json.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import paramiko

AQUI = os.path.dirname(os.path.abspath(__file__))
PUBLICO = os.path.join(AQUI, "docs", "ranking.json")
ESTADO = os.path.join(AQUI, "estado.json")
EXCLUIR = os.path.join(AQUI, "excluir.txt")
SEMANA = os.path.join(AQUI, "docs", "semana.json")
SEMANAS = os.path.join(AQUI, "docs", "semanas")
BRASILIA = timezone(timedelta(hours=-3))
PAUSA_MAX = 45 * 60      # segundos: intervalo maior que isso entre gravacoes nao conta inteiro
MIN_XPH = 30 * 60        # segundos treinando a skill para aparecer no XP/h


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


def ler_excluidos():
    """GUIDs de excluir.txt (maiusculas). Linhas vazias e comentarios (#) sao ignorados."""
    try:
        with open(EXCLUIR, encoding="utf-8") as f:
            linhas = f.read().splitlines()
    except OSError:
        return set()
    return {l.split("#", 1)[0].strip().upper() for l in linhas} - {""}


def agora_utc():
    return datetime.now(timezone.utc)


def inicio_semana(t):
    """Segunda-feira 00:00 de Brasilia da semana de t, em UTC."""
    local = t.astimezone(BRASILIA)
    seg = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return seg.astimezone(timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def ler_data(texto):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(texto, f).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def xp_por_skill(j):
    return {s["nome"]: int(s.get("xp") or 0) for s in j.get("skills", []) if s.get("nome")}


def resumo_semana(sem):
    """Para cada skill: quem mais ganhou XP e quem teve o melhor XP/h. Mais o top de XP total."""
    total, lideres = [], {}
    for g, p in sem.get("jogadores", {}).items():
        ganho_total = 0
        for sk, xp in p.get("xp", {}).items():
            ganho = xp - p.get("base", {}).get(sk, xp)
            if ganho <= 0:
                continue
            ganho_total += ganho
            seg = p.get("seg_skill", {}).get(sk, 0)
            l = lideres.setdefault(sk, {"ganho": None, "xph": None})
            if l["ganho"] is None or ganho > l["ganho"][1]:
                l["ganho"] = (p["nome"], ganho)
            if seg >= MIN_XPH:
                xph = ganho * 3600 / seg
                if l["xph"] is None or xph > l["xph"][1]:
                    l["xph"] = (p["nome"], xph, ganho)
        if ganho_total > 0:
            total.append((p["nome"], ganho_total, p.get("seg", 0)))
    total.sort(key=lambda x: -x[1])
    return total, lideres


def atualizar_semana(jogadores, agora, atualizado=None):
    """Atualiza docs/semana.json com o ranking (ja filtrado). Devolve True se mudou."""
    ini = inicio_semana(agora)
    antes = ler_json(SEMANA, None)
    sem = json.loads(json.dumps(antes)) if antes else None
    virou = sem is not None and sem.get("inicio") != iso(ini)
    if virou:
        os.makedirs(SEMANAS, exist_ok=True)
        sem["final"] = True
        nome = datetime.strptime(sem["inicio"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        gravar_json(os.path.join(SEMANAS, nome.astimezone(BRASILIA).strftime("%Y-%m-%d") + ".json"), sem)
        total, _ = resumo_semana(sem)
        print(f"Semana {sem['inicio']} encerrada; campeao: {total[0][0] if total else '-'}")
    if sem is None or virou:
        sem = {"inicio": iso(ini), "fim": iso(ini + timedelta(days=7)), "jogadores": {}}
    js = sem["jogadores"]
    ref = ler_data(sem.get("ref"))   # momento da ultima gravacao vista na execucao anterior
    for j in jogadores:
        g = str(j.get("guid", "")).upper()
        xp = xp_por_skill(j)
        p = js.get(g)
        if p is None:
            # primeira vez nesta semana: o XP de agora e o ponto de partida
            js[g] = {"nome": j["nome"], "base": xp, "xp": xp, "data": j.get("data"), "seg": 0, "seg_skill": {}}
            continue
        p["nome"] = j["nome"]
        if j.get("data") == p.get("data"):
            continue
        t1, t0 = ler_data(j.get("data")), ler_data(p.get("data"))
        dt = 0
        if t1 and t0:
            if ref and t0 < ref - timedelta(minutes=6):
                t0 = max(t0, ref)    # estava offline na execucao anterior: a pausa nao conta
            dt = int(min(max((t1 - t0).total_seconds(), 0), PAUSA_MAX))
        ganhou = [sk for sk, v in xp.items() if v > p["xp"].get(sk, v)]
        if ganhou:
            p["seg"] += dt
            for sk in ganhou:
                p["seg_skill"][sk] = p["seg_skill"].get(sk, 0) + dt
        for sk, v in xp.items():
            p["base"].setdefault(sk, v)
        p["xp"], p["data"] = xp, j.get("data")
    if atualizado:
        sem["ref"] = atualizado
    if sem == antes:
        return False
    sem["atualizado"] = iso(agora)
    gravar_json(SEMANA, sem)
    return True


def nivel_xp(s):
    return (s.get("nivel") or 0, s.get("xp") or 0)


def num(n):
    return f"{int(round(n)):,}".replace(",", ".")


def mensagem_discord(r, sem=None):
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
    if sem:
        total, lideres = resumo_semana(sem)
        if total:
            campos.append({"name": "Mais XP na semana",
                           "value": "\n".join(f"{i}. {n} — +{num(x)} XP" for i, (n, x, _) in enumerate(total[:3], 1))})
        xph = [f"**{sk}** — {l['xph'][0]} · {num(l['xph'][1])} XP/h"
               for sk, l in sorted(lideres.items()) if l["xph"]]
        if xph:
            campos.append({"name": "Melhor XP/h da semana", "value": "\n".join(xph)[:1024]})
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
    fora = ler_excluidos()
    if fora:
        antes = len(novo["jogadores"])
        novo["jogadores"] = [j for j in novo["jogadores"] if str(j.get("guid", "")).upper() not in fora]
        print(f"excluir.txt: {antes - len(novo['jogadores'])} conta(s) fora do ranking")
    mudou_semana = atualizar_semana(novo["jogadores"], agora_utc(), novo.get("atualizado"))
    antigo = ler_json(PUBLICO, {})
    if antigo == novo and not mudou_semana:
        print("Ranking sem mudança")
        return
    if antigo != novo:
        gravar_json(PUBLICO, novo)
        print(f"Ranking publicado: {len(novo['jogadores'])} jogador(es), {novo.get('atualizado')}")
    if mudou_semana:
        print("Semana atualizada")

    url = env("DISCORD_WEBHOOK", "-")
    if url != "-":
        estado = ler_json(ESTADO, {})
        corpo = mensagem_discord(novo, ler_json(SEMANA, None))
        estado["discord_msg_id"] = discord(url, corpo, estado.get("discord_msg_id"))
        gravar_json(ESTADO, estado)
        print("Discord atualizado")


if __name__ == "__main__":
    main()
