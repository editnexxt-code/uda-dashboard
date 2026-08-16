# UNIÃO DOS AFUNDADOS — Dashboard

Painel local de desempenho dos 12 membros da UDA, alimentado pela API oficial da Riot.
Roda 100% na sua máquina, sem servidor e sem nuvem. O resultado é um único arquivo
`dashboard.html` que você abre no navegador.

---

## 1. Instalação (uma vez só)

```bash
pip install -r requirements.txt
```

## 2. Chave da Riot

1. Entre em <https://developer.riotgames.com> com sua conta Riot.
2. Copie a **Development Key** (`RGAPI-...`).
3. Renomeie `.env.example` para `.env` e cole a chave:

```
RIOT_API_KEY=RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> **A Development Key expira a cada 24 horas.** Para a atualização diária
> funcionar sozinha, peça uma **Personal API Key** no mesmo portal
> (Register Product → Personal API Key). Mesma cota, mas não expira.
> Enquanto isso não sai, é só regerar a dev key e colar no `.env` de novo.

## 3. Rodar

```bash
python run.py --open
```

A primeira execução baixa até 100 partidas por jogador e leva **20–30 minutos**
por causa do limite de 100 requisições a cada 2 minutos. As execuções seguintes
levam menos de 1 minuto: partida já baixada nunca é baixada de novo, e quando
vários membros da UDA jogam juntos a partida conta como um download só.

| Comando | O que faz |
|---|---|
| `python run.py` | busca o que há de novo e regera o HTML |
| `python run.py --open` | idem, e abre o dashboard no navegador |
| `python run.py --build` | só recalcula o HTML, sem tocar na API (não precisa de chave) |
| `python run.py --build --days 0` | usa todo o histórico do banco, sem janela de tempo |
| `python run.py --days 30` | analisa só os últimos 30 dias |
| `python demo.py` | dashboard de demonstração com dados **falsos** |

---

## 4. Atualização diária automática

`atualizar.bat` roda a coleta e grava um log em `data\log.txt`.
Para agendar todo dia às 09:00, abra o **PowerShell como administrador** e rode:

```powershell
schtasks /create /tn "UDA Dashboard" /tr "'C:\Users\Willi\OneDrive\Documentos\UDA Dashboard Piores\atualizar.bat' /silent" /sc daily /st 09:00 /f
```

Para conferir, remover ou rodar na hora:

```powershell
schtasks /query /tn "UDA Dashboard"
schtasks /run   /tn "UDA Dashboard"
schtasks /delete /tn "UDA Dashboard" /f
```

Se a tarefa falhar em silêncio, quase sempre é a chave de 24h que expirou —
o motivo fica escrito em `data\log.txt`.

---

## 5. O que o dashboard mostra

**Abas:** Todas · Ranked Solo · Ranked Flex · Normais · ARAM · **Em equipe**.
Tudo abaixo é recalculado por aba.

### Aba "Em equipe"

Considera só as partidas em que **2 ou mais membros da UDA estavam no mesmo time**.
Além de tudo que as outras abas mostram, ela traz quatro blocos exclusivos:

- **Junto ou sozinho?** — o mesmo jogador nos dois cenários, lado a lado. A coluna Δ
  mostra quanto ele muda com a UDA no time: vitórias, KDA, mortes, participação e dano.
  Só aparece quem tem amostra suficiente **dos dois lados** (senão a comparação mente).
- **Matriz de sinergia** — grade de todos contra todos com o aproveitamento de cada
  dupla no mesmo time. Verde = vencem juntos, vermelho = afundam juntos. O número
  pequeno em cada célula é quantas partidas aquela dupla tem.
- **Formações** — duplas, trios, quartetos e time completo, com aproveitamento de cada
  composição exata.
- **Confrontos internos** — placar de quem leva a melhor quando dois membros caem em
  times opostos.

### Filas que ficam de fora de tudo

Partidas contra **bots** (Co-op vs AI), **tutorial** e **Arena** são descartadas de todos
os cálculos. Bots inflariam KDA e vitórias artificialmente; Arena é 2v2v2v2, então
"abates do time" e "% do dano" não significam a mesma coisa que no 5v5 e misturar os
dois distorce as métricas de todo mundo.

- **Visão geral** — partidas, aproveitamento do grupo, KDA médio, perfil da partida
  média, total de mortes, melhor e pior do momento.
- **O pódio & os afundados** — top 3 e bottom 3 pelo UDA Score.
- **Classificação geral** — tabela ordenável por qualquer coluna: elo, jogos,
  vitórias, KDA, participação em abates, CS/min, ouro/min, dano/min, % do dano do
  time, visão/min, mortes por 10 min e UDA Score. Clicar numa linha pula para o
  card daquele jogador.
- **Hall da fama / Hall da vergonha** — 12 prêmios: O Carregador, Máquina de Abate,
  O Imortal, O Farmador, A Sentinela, Onipresente, Doador de Ouro, O Cego,
  O Fantasma, O Turista, O Azarado, O Figurante.
- **Carrega ou alimenta?** — dispersão de dano/min contra mortes/10min, com as
  médias do grupo marcadas.
- **Aproveitamento por semana** e **Recordes** (maior massacre, maior dano numa
  partida, a vergonha máxima).
- **Campeões do grupo** e **Duplas** (quem joga junto e com que aproveitamento).
- **Jogador por jogador** — card com radar comparativo, partida média, 12 métricas,
  as últimas 12 partidas e os campeões mais jogados.

### O UDA Score

Nota de 1 a 99 que compara cada jogador **contra o próprio grupo**, não contra o
servidor. Cada métrica vira um z-score dentro da UDA e entra com um peso:

| Métrica | Peso |
|---|---|
| Vitórias | 28% |
| KDA | 18% |
| Dano por minuto | 14% |
| Participação em abates | 12% |
| CS por minuto | 10% |
| Sobrevivência (minutos por morte) | 10% |
| Visão por minuto | 8% |

No ARAM os pesos mudam: CS e visão saem, dano e sobrevivência sobem.
Score 50 é exatamente a média da UDA. Só entra no ranking quem tem pelo menos
`MIN_GAMES` partidas na fila (padrão 5) — evita alguém liderar com 2 jogos.

---

## 6. Ajustes

Tudo no `.env`:

| Variável | Padrão | Efeito |
|---|---|---|
| `MATCH_COUNT` | 100 | partidas por jogador na primeira carga (máx. 100) |
| `WINDOW_DAYS` | 90 | janela de análise; `0` usa o histórico inteiro |
| `MIN_GAMES` | 5 | mínimo de partidas para entrar no ranking |

Para trocar quem aparece no painel, edite `players.json` (Riot ID completo,
`Nome#TAG`) e rode `python run.py`.

---

## 7. Como está montado

```
run.py              entrada: coleta + cálculo + render
demo.py             dashboard de demonstração com dados falsos
players.json        os 12 Riot IDs
uda/config.py       lê .env e players.json
uda/riot.py         cliente da API com rate limit e retry
uda/store.py        SQLite (partidas ficam salvas para sempre)
uda/kpi.py          todos os KPIs e o UDA Score
uda/render.py       injeta o JSON no template
uda/template.html   o visual (CSS + JS puro, sem framework)
data/uda.sqlite3    banco local
dashboard.html      o resultado
```

Os gráficos são SVG gerado na hora, sem biblioteca. Ícones de campeão, invocador
e emblema de elo vêm do Data Dragon e do Community Dragon — precisam de internet
para aparecer, mas não precisam de chave.

### Notas de implementação

- **Dois hosts, sempre.** `americas.api.riotgames.com` para Account-V1 e Match-V5;
  `br1.api.riotgames.com` para Summoner-V4 e League-V4. Trocar isso devolve 404.
- Partidas com menos de 5 minutos ou encerradas por *remake* são descartadas de
  todos os cálculos.
- Os 10 participantes de cada partida são gravados, não só os da UDA — é o que
  permite calcular participação em abates e % do dano do time.

---

## 8. Limites conhecidos

- A Riot **não** expõe estatísticas agregadas do servidor. Não dá para comparar a
  UDA contra "a média do Esmeralda BR" — o LeagueOfGraphs consegue porque baixa
  milhões de partidas por conta própria. Por isso o UDA Score compara vocês entre
  vocês.
- Match-V5 guarda o histórico recente. Partidas muito antigas não voltam mais.
- Sem GPU envolvida: a carga é de rede e o cálculo é aritmética simples sobre
  algumas milhares de linhas. Roda em segundos na CPU.

Este projeto não é endossado pela Riot Games.
