# UNIÃO DOS AFUNDADOS — Dashboard

Painel de desempenho dos 18 membros da UDA, alimentado pela API oficial da Riot.
O resultado é um único arquivo `dashboard.html`, autossuficiente: os ícones vão
embutidos, então ele abre offline e funciona quando você manda pro grupo.

**No ar:** <https://editnexxt-code.github.io/uda-dashboard/> — o GitHub Actions
recoleta e republica de duas em duas horas.

O painel existe para zoar. Isso não é piada de rodapé: é o critério de projeto.
Toda métrica precisa render uma frase que alguém vá mandar no grupo, e toda
acusação vem com a prova do lado — dá pra clicar e ver a partida.

---

## 1. Instalação

```bash
pip install -r requirements.txt
```

## 2. Chave da Riot

```bash
python configurar.py
```

Pede a chave, não mostra o que você digita, grava no `.env` e faz uma chamada
real pra provar que funciona antes de você gastar a coleta inteira.

> **Development Key morre em 24h.** Para a atualização automática, peça uma
> **Personal API Key** em <https://developer.riotgames.com> (Register Product →
> Personal API Key): mesma cota, não expira. No GitHub, a chave vive no secret
> `RIOT_API_KEY` do repositório.

## 3. Rodar

```bash
python verificar.py      # audita as contas ANTES de gastar a coleta
python run.py --open     # coleta e abre o painel
```

O `verificar.py` responde, por conta, em ~1 minuto: o Riot ID existe? tem
invocador de LoL no BR? de que servidor são as partidas de verdade (lê o prefixo
`BR1_` do ID)? qual o elo? Use `--sugerir` para ele tentar variações de tag nos
que falharem.

| Comando | O que faz |
|---|---|
| `python run.py` | busca o que há de novo e regera o HTML |
| `python run.py --build` | só recalcula, sem tocar na API (não precisa de chave) |
| `python run.py --build --days 0` | usa o histórico inteiro do banco |
| `python run.py --days 30` | analisa só os últimos 30 dias |
| `python demo.py` | painel de demonstração com dados **falsos** |

A primeira coleta leva 20–30 min (limite de 100 requisições a cada 2 minutos).
As seguintes levam menos de 1 minuto: partida baixada fica no SQLite pra sempre,
e quando vários membros jogam juntos a partida conta como um download só.

## 4. As abas

**Visão geral** — panorama, pódio, os afundados, halls da fama e da vergonha,
dispersão de "carrega ou alimenta", rotas, filas e as últimas partidas.

**Classificação** — todos os jogadores em 19 colunas ordenáveis.

**Evolução** — corrida do UDA Score por mês, forma recente, saldo acumulado,
mapa de quando vocês jogam e sequências. Tem seletor de jogador: clique num chip
para isolar alguém.

**Em equipe** — só as partidas com 2+ membros no mesmo time. Junto ou sozinho,
matriz de sinergia, formações e confrontos internos.

**Personalizadas** — as partidas que vocês jogam entre si. Tabela de todos contra
todos, algoz e freguês, a dupla e o divórcio, a balança, o espelho.

**Jogadores** — um cartão por pessoa com radar comparativo, rotas, melhor e pior
partida. **Clicar no cartão abre o champion pool por rota**: cada campeão com
vitórias, derrotas, aproveitamento e KDA, separado por posição. Clicar num
campeão dentro do cartão continua abrindo o dossiê, e numa partida, o placar.

**Campeões** — o que o grupo joga, o arsenal e **os algozes da rota**: contra qual
campeão cada um apanha na fase de rota.

**Arsenal** — itens, runas principais e duplas de feitiço mais usados.

**Elevador** — quem subiu, quem desceu e quem passou o ano apertando o botão.
Traz **três origens diferentes de dado, cada uma dizendo qual é**:

- os três pódios, pelo saldo de vitórias e derrotas em ranqueada (medido);
- *A queda, medida* — a curva do saldo acumulado ao longo do tempo, com o pico
  marcado e o quanto caiu de lá até hoje (medido, mas só alcança o que o
  Match-V5 ainda serve);
- *O tombo que ninguém esquece* — temporada antiga, digitada à mão em
  `elos.json` e marcada como **informado** na tela.

**Mural do mês** — o 👑 UDA e o 💀 Afundado de cada mês, com pódio, e o quadro de
títulos acumulados.

**Paredão** — as piores partidas de cada um, com o porquê medido. Tem filtro de
período (tudo / 90 / 30 / 7 dias) e botão de copiar no pódio.

> O veredito de cada vexame sai de uma escala calibrada pela distribuição real
> das notas (29 a 132, mediana 82), com variantes sorteadas pelo ID da partida.
> O sorteio usa  e não : o Python embaralha o hash de string a
> cada processo, e a mesma partida mudaria de frase a cada geração.

**Troféus** — 64 métricas de carreira, em Glória, Vergonha e Curiosidade. Clicar
num degrau do pódio abre as partidas que produziram aquele número, e o botão
*copiar* no canto do cartão põe a zoeira montada na área de transferência,
pronta para colar no grupo.

**Autópsia** — não quanto você morre, mas **como**: de que tipo de dano cada um
apanha (físico / mágico / verdadeiro), onde se sente seguro para matar (na saia
da própria torre ou na cara da inimiga), qual dos três botões o dedo castiga, e
o pódio das mortes sem nenhum culpado por perto.

**Contra o servidor** — a única aba cuja régua não é a própria UDA. A API de
Desafios da Riot dá, por desafio, a faixa do jogador (Ferro a Desafiante) contra
a base inteira do BR, mais os pontos totais e a maestria por campeão.

**O Mapa** — mapa de calor das mortes, com a coordenada exata que a Riot
registra em cada abate. Um mapa do grupo inteiro e um por pessoa. Só Summoner's
Rift: o Abismo Uivante usa outra escala de coordenada, e sob o filtro ARAM a aba
fica vazia de propósito em vez de mostrar o mapa errado.

**Regras** — como o UDA Score é calculado.

Tudo é recalculado **por fila**: Todas, Solo, Flex, Normais, ARAM,
Personalizadas e Outros modos.

### O placar

Qualquer partida citada na tela é clicável e abre a tela de fim de jogo com os
dez jogadores, nomes reais, itens, runas, objetivos e selos MVP/ACE. Funciona
também nas personalizadas, que não vêm da API: ali o placar é montado da tabela
`participants` com os nomes do cadastro local.

### O UDA Score

Compara **vocês com vocês**, não com o servidor — 50 é a média exata da UDA.
Cada métrica vira z-score dentro do grupo e entra com um peso: vitórias 28%,
KDA 18%, dano/min 14%, participação 12%, CS/min 10%, sobrevivência 10%,
visão/min 8%. Mínimo de 5 partidas na fila para entrar no ranking. Em ARAM e
Outros modos os pesos mudam: CS e visão saem, dano sobe.

A Riot não publica estatísticas agregadas do BR, então não existe "média do
Esmeralda" para comparar em KDA, dano ou CS. Por isso a régua do UDA Score é o
próprio grupo.

A **única** exceção está na aba *Contra o servidor*: a API de Desafios entrega
a faixa de cada jogador em cada desafio, medida contra toda a base. Vale ler com
cuidado, porque a granularidade engana:

- A **faixa** (Ferro a Desafiante) é individual de verdade.
- O **percentil** descreve a *faixa*, não a pessoa: quem está em Ouro num desafio
  recebe o mesmo número de todo mundo em Ouro nele, tendo o dobro do valor ou
  não. Medido: 5,5 percentis distintos por desafio entre 18 pessoas.
- Por isso os destaques são ordenados por faixa. Ordenando por percentil, os
  mesmos três desafios apareciam no cartão do elenco inteiro — e no bloco de
  ponto fraco saíam desafios em que a pessoa estava 500% *acima* da mediana da
  UDA. Com a faixa são 45 desafios distintos ali, contra 3.
- **Sem faixa não é ser ruim**: desafio nunca pontuado volta com percentil 100%
  por definição, e fica de fora.

### O que fica de fora

Partidas contra **bots**, **tutorial**, o **modo Prática** e a **Arena** são
descartadas de todos os cálculos. Bots inflariam vitórias e KDA; a Arena é
2v2v2v2 e a API agrupa os jogadores de um jeito em que "abates do time" somaria
9 pessoas, destruindo participação e % de dano.

A classificação é por `gameMode`, que vem dentro da partida — não por ID de fila.
A Riot cria IDs novos sem avisar e a lista pública dela fica desatualizada.

Remakes e partidas de menos de 5 minutos também saem.

### Histórico de elo: a Riot não guarda, o painel passou a guardar

A API da Riot não tem endpoint de histórico de elo: a League-V4 responde só onde
a pessoa está **agora**. Sites como Blitz e OP.GG mostram a curva de PDL porque
**eles mesmos vêm gravando snapshots há anos** — não é um dado que a Riot
entregue, é um dado que se acumula. Este painel também só guardava o agora, e
cada execução apagava a anterior.

Agora o `replace_ranks` grava em `rank_history` **a cada mudança**, não a cada
leitura: o Actions roda de duas em duas horas e a maioria das leituras é idêntica
à anterior, então guardar todas encheria a tabela de pontos repetidos. Assim cada
linha é uma mudança real de PDL, que é a resolução que o gráfico quer. Como
`wins`/`losses` vêm da própria League-V4, a diferença entre dois pontos diz
exatamente quantas ranqueadas aconteceram no trecho.

Enquanto o histórico não tem dois dias, a aba diz que está anotando. Os pódios
usam o **saldo ranqueado** (vitórias menos derrotas nas filas 420 e 440), que é
medido de partida real e é exatamente o que move o PDL. Estimar trajetória
chutando "20 PDL por vitória" daria cara de medição a um palpite.

> "Quem mais se manteve" exige mínimo de 20 ranqueadas. Sem o piso, o prêmio de
> mais estável iria para quem não jogou: zero partida dá saldo zero.

**Temporada antiga não está em API nenhuma.** O Match-V5 só serve partida
recente — o banco tem 155 ranqueadas do Gui, enquanto a própria Riot diz que ele
tem 832 na temporada. Então a queda que o grupo lembra ("foi de Mestre a quase
Platina") não é recuperável: ou alguém digita, ou o painel finge que não
aconteceu. `elos.json` é onde se digita, e a tela marca como *informado* para
ninguém confundir com o que foi medido.

> Testei usar os desafios de temporada como elo — "Temporada 2024: Etapa 2 =
> MASTER" parecia promissor. Não serve: CliqueLetal está **Prata** hoje e marca
> MASTER nesse desafio. A descrição confirma que são pontos de desafio, não
> ranque.

### A timeline, e por que ela é a exceção

`/matches/{id}/timeline` traz um quadro por minuto com ouro, XP, CS e posição
dos dez, mais cada abate com a coordenada no mapa, cada item na ordem de compra
e cada ponto de habilidade. É o dado mais caro do projeto: **963 KB crus por
partida, 82 KB comprimidos**. As 1681 partidas dariam 139 MB atravessando o
cache do Actions a cada duas horas.

Por isso, e só aqui, o bruto **não** é guardado: `uda/timeline.py` extrai o que
vira tela (~700 bytes por participante, ~2 MB no total) e descarta o resto. A
contrapartida é real e assumida: campo novo daqui exige rebuscar na API.

A coleta tem teto por execução (`TIMELINE_LIMIT`, padrão 250) e vai da partida
mais recente para a mais antiga, com a marca em `matches.tl_done`. Cair no meio
não perde o que já veio, e o histórico converge em algumas rodadas.

### Campos destravados sem gastar API

O JSON completo de cada partida fica comprimido em `matches.raw`, então dá para
extrair campo novo do histórico inteiro sem uma única chamada. A migração v3
destravou 34 colunas — morte sem culpado, camp do próprio jungler, execução pela
torre, dano levado por tipo, uso por habilidade — e repopulou 1664 partidas.

> O backfill é versionado por linha (`matches.schema_v`). Antes ele perguntava
> "esta partida tem `patch` nulo?", o que só funciona **uma vez**: depois da v2
> nenhuma linha tinha patch nulo, e a v3 acharia zero pendentes, gravaria a nova
> versão e deixaria as colunas novas zeradas para sempre — sem erro nenhum.

### Métricas que dependem de rota

"Farm dos Dez Minutos" e "Cego de Rota" contam **só** topo, meio e atirador, e
exigem 8 partidas de rota. Sem esse recorte, o jungler apareceria como "quem
menos farma" — o que não é vexame, é a função dele. O inverso também vale:
"Farm da Selva aos 10" e "Rei do Caranguejo" só contam quem estava na selva, e
"Rato de Camp" (comer o camp do próprio jungler) exclui o jungler, para quem
aquilo é simplesmente o farm da função.

## 5. Atualização automática

O `.github/workflows/atualizar.yml` roda de 2 em 2 horas: restaura o banco do
cache, chama `python run.py` com o secret `RIOT_API_KEY`, e publica em GitHub
Pages. Dá pra disparar na mão com `gh workflow run atualizar.yml --ref main`.

Localmente, `atualizar.bat` faz o mesmo e grava log em `data\log.txt`.

## 6. Personalizadas

Elas **não existem na API da Riot** — só no cliente do jogo. O coletor
`personalizadas.py` grava os JSON em `personalizadas/bruto/`, que **vai para o
git**; o `uda/inhouse.py` importa essa pasta para o banco a cada execução, tanto
no seu PC quanto no GitHub Actions.

## 7. Ajustes

Tudo no `.env`:

| Variável | Padrão | Efeito |
|---|---|---|
| `MATCH_COUNT` | 100 | partidas por jogador na primeira carga (máx. 100) |
| `WINDOW_DAYS` | 90 | janela de análise; `0` usa o histórico inteiro |
| `MIN_GAMES` | 5 | mínimo de partidas para entrar no ranking |
| `TIMELINE_LIMIT` | 250 | timelines buscadas por execução; `0` desliga |

Para mudar quem aparece, edite `players.json` com o Riot ID completo (`Nome#TAG`)
e rode `python run.py`.

> Quem entra no elenco depois recebe carga completa automaticamente. A marca
> `fullload:<puuid>` na tabela `meta` controla isso — deduzir de "já tem partida
> no banco" não funcionava, porque quem entra depois já tem participações
> gravadas vindas das partidas dos outros.

## 8. Como está montado

```
run.py                entrada: coleta + cálculo + render
verificar.py          audita as contas contra a API
configurar.py         grava a chave no .env sem exibir
demo.py               painel de demonstração com dados falsos
personalizadas.py     coletor das partidas personalizadas (lê o cliente)
players.json          os Riot IDs
uda/config.py         lê .env e players.json, limpa caractere invisível
uda/riot.py           cliente da API: rate limit por host, calibrado pelos headers
uda/store.py          SQLite, migração de schema e backfill
uda/fetch.py          coleta incremental, retomável
uda/kpi.py            KPIs, UDA Score, grupos por fila
uda/evolucao.py       séries temporais
uda/zoeira.py         os 64 troféus de carreira
uda/vexames.py        as piores partidas, com motivo
uda/mural.py          UDA e Afundado do mês
uda/arsenal.py        itens, runas e feitiços
uda/rota.py           o algoz de rota
uda/autopsia.py       como cada um morre, e de que
uda/timeline.py       coleta e extrato da timeline (minuto a minuto)
uda/mapa.py           mapa de calor das mortes
uda/desafios.py       coleta dos desafios e da maestria
uda/elo.py            o elevador: sobe, desce e fica parado
uda/servidor.py       a comparacao com o servidor
uda/partidas.py       placar completo das partidas citadas
uda/inhouse.py        importa e analisa as personalizadas
uda/assets.py         embute ícones e a ficha dos campeões
uda/render.py         injeta o JSON no template
uda/template.html     o painel inteiro (CSS + JS puro, sem framework)
```

### Notas de implementação

- **Dois hosts, sempre.** `americas` para Account-V1 e Match-V5; `br1` para
  Summoner-V4 e League-V4. Trocar devolve 404.
- **Rate limit calibrado pelos headers.** O cliente lê `X-App-Rate-Limit` e
  `X-App-Rate-Limit-Count` e mantém um balde por host — a Riot conta separado
  por roteamento. Sem isso, um processo que começa logo depois de outro dispara
  em cima de uma janela já cheia e toma 429.
- **Os 10 participantes** de cada partida são gravados, não só os da UDA. É o
  que permite calcular participação em abates, % do dano do time e o placar.
- **Coleta retomável.** Todo ID descoberto entra numa fila em tabela e só sai
  quando é baixado. Sem isso, uma queda no meio deixaria buracos que a marca
  d'água esconderia para sempre.
- **Densidade.** Blocos repetidos dobram (`dobrar()`) e seções recolhem
  (`secoesDobraveis()`), com limites diferentes para celular e desktop.

## 9. Limites conhecidos

- A Riot **não** expõe estatísticas agregadas do servidor, então não dá para
  comparar a UDA com "a média do Esmeralda BR".
- Match-V5 guarda o histórico recente; partidas muito antigas não voltam mais.
- O HTML passa de 6 MB por causa dos ícones e dos placares embutidos. É o preço
  de funcionar offline e em qualquer visualizador.

Este projeto não é endossado pela Riot Games.
