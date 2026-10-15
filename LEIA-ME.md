# Hiscores do servidor — instalação

Isto roda de graça no GitHub, sem o PC ligado. A cada 15 min o GitHub Actions
puxa o `ranking.json` do servidor pela SFTP da Shockbyte, publica a página e,
se houver webhook, atualiza a mensagem fixa no Discord.

## 0. Antes de tudo: um usuário SFTP só para isso
Não use a senha da sua conta Shockbyte. No painel, **Controle de Acesso** →
crie um subusuário (outro e-mail) só com permissão de **arquivos / SFTP**.
As credenciais dele é que vão para o GitHub.

## 1. Repositório
1. Crie um repositório **público** no GitHub (ex.: `hiscores-dragonwilds`).
   Público porque o GitHub Pages grátis exige; os segredos continuam escondidos.
2. Suba o conteúdo desta pasta (inclusive a pasta oculta `.github`).

## 2. Segredos
Settings → Secrets and variables → Actions → **New repository secret**:

| nome | valor |
|---|---|
| `SFTP_HOST` | o host do painel (botão "Conectar SFTP"), ex.: `sftp.shockbyte.sao1....` |
| `SFTP_PORT` | `2222` |
| `SFTP_USER` | usuário do subusuário |
| `SFTP_PASS` | senha do subusuário |
| `RANKING_PATH` | caminho do arquivo a partir da raiz da SFTP, terminando em `Win64/ue4ss/Mods/Guardiao/ranking.json` |
| `DISCORD_WEBHOOK` | (opcional) URL do webhook do canal `#hiscores` |
| `SITE_URL` | (opcional) endereço da página, para o título do Discord virar link |

Para descobrir o `RANKING_PATH`: abra a SFTP no FileZilla (ou navegue no
gerenciador de arquivos do painel) até o `ranking.json` e copie o caminho
mostrado, sem a barra do começo.

## 3. Página
Settings → Pages → Source: **Deploy from a branch** → branch `main`, pasta `/docs`.
O endereço fica `https://<seu-usuario>.github.io/hiscores-dragonwilds/`.

## 4. Primeira rodada
Actions → **Atualizar ranking** → **Run workflow**. Se der erro, o log da
rodada diz qual segredo falta ou se a SFTP recusou.

## Discord
Crie o webhook em: canal `#hiscores` → Editar canal → Integrações → Webhooks.
Na 1ª rodada o bot posta a mensagem; nas seguintes, **edita a mesma** (o id
fica em `estado.json`). Fixe essa mensagem no canal. Se alguém apagá-la, a
próxima rodada posta outra.

## Observações
- O GitHub pode atrasar o agendamento alguns minutos em horários de pico.
- Só há commit quando o ranking muda, então o repositório não enche.
