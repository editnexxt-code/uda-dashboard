# Relatório — UDA Dashboard Piores

**Nota importante antes de tudo:** o código mudou enquanto a auditoria rodava. Reli todos os arquivos agora, e as linhas citadas abaixo são as do estado **atual** do disco (`uda/kpi.py` com 746 linhas, `uda/template.html` com 861, `players.json` com 14 contas, `.env` já existindo com `RIOT_API_KEY` de 42 caracteres começando em `RGAPI`). Três defeitos que apareciam na auditoria **já foram corrigidos** e estão listados na seção 4.

---

## 1. VALORANT — veredito

**Não. É tecnicamente impossível o dashboard ter puxado qualquer dado de Valorant ou TFT**, porque as únicas 5 chamadas à Riot API no projeto são `/riot/account/v1/accounts/by-riot-id/` (agnóstico de jogo, devolve só puuid/nome/tag — `uda/riot.py:110`), `/lol/summoner/v4/summoners/by-puuid/` (`riot.py:118`), `/lol/league/v4/entries/by-puuid/` (`riot.py:125`), `/lol/match/v5/matches/by-puuid/{puuid}/ids` (`riot.py:137`) e `/lol/match/v5/matches/{match_id}` (`riot.py:146`), nos hosts `br1.api.riotgames.com` e `americas.api.riotgames.com` — não existe uma única string `/val/` ou `/tft/` no projeto inteiro (a palavra "Valorant" aparece uma vez só, em `verificar.py:55`, como texto de aviso impresso na tela).

As duas únicas outras URLs de rede são o Data Dragon (`riot.py:160` e `riot.py:176`), CDN público e exclusivo de League of Legends.

---

## 2. CONTAS E RIOT IDs

### O que dá para afirmar sem chave de API

- **Nenhuma coleta real jamais aconteceu.** O banco de produção `data/uda.sqlite3` está com **0 linhas** em `players`, `ranks`, `matches`, `participants` e `meta`. O `dashboard.html` (47 KB, 16/08 00:38) foi gerado desse banco vazio.
- **O dashboard que você viu (`dashboard-demo.html`) é 100% inventado.** Ele vem de `data/demo.sqlite3` (14 jogadores, 1071 partidas, 10.710 participações), gerado por `demo.py:32` com `random.Random(20260816)` — números pseudoaleatórios de semente fixa. Nada ali passou pela Riot.
- **O formato dos 14 Riot IDs está correto.** Todos os `gameName` têm 3–16 caracteres e todas as `tagLine` têm 3–5 caracteres alfanuméricos. O nome cirílico `различие#666` **não** é problema: `uda/riot.py:111` usa `quote(..., safe='')`, que codifica UTF-8 corretamente na URL.
- **A configuração declara BR**: `players.json:2-5` fixa `platform: "br1"` e `routing: "americas"`, e todas as chamadas `/lol/` vão de fato para `br1.api.riotgames.com`.

### O que só a API pode confirmar

- **Se cada Riot ID existe e é da pessoa certa.** `Gui#Gui`, por exemplo, é genérico o bastante para ser conta de um estranho — só a Account-V1 responde isso.
- **Se cada conta tem invocador de League of Legends no BR.** Tag não codifica região: `metal mummy#br1` não prova BR mais do que `Maou#TopBr` prova. A única prova é o Summoner-V4 no host `br1` responder 200 — e hoje o código **descarta essa resposta** (ver defeito #2 abaixo).
- **Se as partidas são de BR1.** O roteamento `americas` cobre BR1, NA1, LA1 e LA2 juntos. Uma conta de NA traria o histórico dela normalmente, misturado com os brasileiros, e o cabeçalho continuaria escrito "BR".

**Como confirmar:** o projeto já tem a ferramenta pronta — `verificar.py` audita todas as contas, marca "SEM invocador de LoL em BR" (`verificar.py:49-56`) e confere o servidor real pelo prefixo do matchId (`verificar.py:74-79`), gastando ~50 requisições e **sem escrever nada no banco**. Rode `python verificar.py` **antes** do primeiro `python run.py`. Hoje ela não é chamada por `run.py` nem citada no `README.md` — só em `configurar.py:121` e `configurar.py:158`.

---

## 3. DEFEITOS REAIS

### ALTO

**[1] O dashboard de demonstração é visualmente idêntico ao real e afirma que os dados vieram da Riot**
- **O que quebra:** `demo.py:157` marca o payload com `"UNIAO DOS AFUNDADOS (DEMO)"`, mas o template **nunca lê `DATA.title`** (zero ocorrências em `template.html`). O `<h1>` em `template.html:285` é texto fixo, o `<title>` da aba em `template.html:6` é fixo, e o rodapé em `template.html:298` afirma literalmente **"Dados: Riot Games API · Data Dragon"**. O chip em `template.html:846` diz "BR ·" também hardcoded.
- **Onde aparece na tela:** em **todos** os números — as 1071 "partidas analisadas", o UDA Score, os elos Prata/Ouro/Diamante, KDA, pódio e o Hall da Vergonha. Confirmei no arquivo gerado: `dashboard-demo.html:285` renderiza o nome limpo e `:298` afirma procedência da Riot, enquanto o payload embutido carrega o título "(DEMO)". **Esta é a causa direta da sua suspeita (1).** Os dados não estão incorretos — eles são fictícios, e nada na tela avisa isso.
- **Correção:** trocar `template.html:285` por `${h(DATA.title)}`; adicionar um campo booleano `demo` ao payload (setado em `demo.py`) que, quando verdadeiro, pinta uma faixa vermelha no topo ("DADOS FICTÍCIOS — nenhuma chamada à Riot API") e troca o texto do rodapé; trocar o "BR" de `template.html:846` por `${h(DATA.platform)}`.

---

### MÉDIO

**[2] Conta sem League of Legends no BR passa como "ok"**
- **O que quebra:** `uda/fetch.py:40` — `summoner = client.summoner_by_puuid(puuid) or {}`. Essa é a **única** chamada que provaria que a conta tem invocador de LoL no servidor BR, e o 404 dela (`riot.py:115-120`, `allow_404=True`) vira dicionário vazio. O jogador segue sendo gravado (`fetch.py:41-44`), entra em `tracked` (`fetch.py:143`) e é impresso como `ok ... nivel ?` (`fetch.py:51,56`).
- **Onde aparece na tela:** a pessoa fica com ícone padrão 29 e "nível 0" (`kpi.py:99-100`, `or 29` / `or 0`), indistinguível de conta BR real. Se ela não tiver LoL em nenhum servidor das Américas, some do painel por `kpi.py` (`if not prows: continue`) e o chip passa a mostrar 13 jogadores em vez de 14, sem mensagem nenhuma.
- **Correção:** em `fetch.py:40`, tratar `None` como falha: imprimir `x {riot_id} SEM invocador de LoL em BR` e `continue`, sem gravar o jogador.

**[3] Ninguém limpa a tabela `players`: conta errada resolvida uma vez fica no painel para sempre**
- **O que quebra:** `uda/kpi.py:94` faz `SELECT * FROM players` sem WHERE e sem confrontar com o `players.json` atual, e `build_payload` (`kpi.py:576`) nem recebe o roster. O único DELETE do projeto inteiro é `DELETE FROM ranks` em `uda/store.py:124` — não existe DELETE em `players` nem em `participants`.
- **Onde aparece na tela:** o intruso vira um card a mais, entra no cálculo de média e desvio-padrão do UDA Score (`kpi.py:245-246`, mudando o score de **todo mundo**), pode ocupar pódio ou Hall da Vergonha (`kpi.py:697-698`) e ganhar prêmios. Corrigir o `players.json` **não desfaz** nada disso. O gatilho mais provável nem é ID errado: é simplesmente **remover alguém do `players.json`** — o card fantasma aparece em 100% dos casos.
- **Atenuante:** a janela de 90 dias faz o fantasma sumir sozinho quando as partidas antigas dele saírem do período.
- **Correção:** ao fim de `resolve_players`, apagar de `players`/`ranks` os puuids que não estão mais no roster, ou passar `resolved` para `kpi.build_payload` e filtrar.

**[4] A flag `tracked` é escrita uma única vez e nunca corrigida**
- **O que quebra:** `uda/store.py:234` — `"tracked": 1 if puuid in tracked else 0` — é calculada no instante do download, e `uda/fetch.py:98` (`novos = [m for m in ids if m not in known ...]`) garante que partida já salva **nunca** é regravada. Não existe nenhum `UPDATE participants SET tracked` no projeto.
- **Onde aparece na tela:** um membro adicionado depois da primeira coleta aparece com **muito menos jogos do que realmente jogou** — as partidas antigas que ele dividiu com o grupo ficam `tracked=0` e são filtradas por `kpi.py:79` (`WHERE p.tracked = 1`). Se ficar abaixo de MIN_GAMES=5, ele sai do ranking, do UDA Score e dos prêmios, enquanto os companheiros contam **as mesmas partidas** normalmente.
- **Correção:** depois de `resolve_players`, rodar `UPDATE participants SET tracked=1 WHERE puuid IN (roster)` e `SET tracked=0 WHERE puuid NOT IN (roster)`.

**[5] Um 404 numa conta envenena aquela execução inteira, permanentemente**
- **O que quebra:** `uda/fetch.py:31-34` — Riot ID digitado errado ou conta renomeada devolve `None`, o jogador é pulado com um `continue` e fica fora de `tracked = set(resolved)` (`fetch.py:143`). Todas as partidas baixadas naquela run gravam `tracked=0` para ele, e pelo defeito [4] isso nunca é corrigido. Pior: `store.py:150` (`SELECT MAX(game_creation) ... WHERE puuid=?`, **sem filtro de tracked**) passa a enxergar essas linhas envenenadas, então na run seguinte `fetch.py:81-82` trata a primeira carga dele como incremental e **nunca pede o histórico anterior**.
- **Onde aparece na tela:** o jogador aparece com menos partidas do que jogou, ou some do ranking. O aviso é **uma linha** no log da etapa [1/3].
- **Correção:** abortar (ou pedir confirmação) quando algum Riot ID não resolver, e adicionar `AND tracked=1` em `store.py:150`.

**[6] Interromper a coleta perde partidas de forma irrecuperável**
- **O que quebra:** as partidas são baixadas da mais nova para a mais antiga (`fetch.py:120`), com commit a cada 20 (`fetch.py:128-129`). Como a marca d'água é `MAX(game_creation)` (`store.py:148-152`) e vira `startTime` na próxima run (`fetch.py:81`), um Ctrl+C no meio salva justamente as **mais novas** e empurra a marca ao máximo — as antigas do mesmo lote nunca mais são pedidas. Agravante: `save_match` grava os 10 participantes, então salvar partidas do jogador A também avança a marca do jogador B que estava junto.
- **Onde aparece na tela:** a próxima execução imprime "incremental -> 0 novos" e o painel fica permanentemente com um recorte enviesado para jogos recentes. E `run.py:74` afirma o contrário: "Interrompido. O que já foi baixado está salvo no banco." — falso, porque `run.py:65` faz `conn.close()` sem commit, descartando até 19 partidas já baixadas.
- **Correção:** baixar em ordem cronológica crescente (`match_ids[::-1]` em `fetch.py:120`), commitar por partida, e trocar o `conn.close()` de `run.py:65` por commit-antes-de-fechar.

**[7] Coleta incremental limitada a 100 ids**
- **O que quebra:** `uda/fetch.py:82` — `target = settings.match_count if last is None else 100`. Se alguém jogar 250 partidas entre duas execuções, as 100 mais novas entram e as 150 do meio somem para sempre, porque a marca d'água avança para a mais nova.
- **Onde aparece na tela:** `jogos`, `winrate` e "Partidas analisadas" ficam subestimados, e o gráfico de tendência semanal mostra semanas vazias que na verdade foram jogadas.
- **Atenuante:** coerente com o teto de 100 declarado no README; só dispara com runs muito espaçadas e alguém jogando muito.
- **Correção:** no incremental, paginar até o lote voltar vazio em vez de parar em 100.

---

### BAIXO

**[8] A coluna "Elo" ordena do pior para o melhor** — `template.html:468` tem `s:(p)=>-(p.ranks.solo?p.ranks.solo.weight:-1)`, e o comparador de `template.html:509` já é decrescente. O primeiro clique em "Elo" coloca Prata no topo e Diamante embaixo, com a seta ▼ sugerindo o contrário; "Sem elo" sobe acima de todo mundo (porque `-(-1) = 1` é maior que o `-0` de um Ferro IV). Nenhum número está errado, só a ordem, e o segundo clique corrige. **Correção:** remover o sinal de menos na linha 468.

**[9] Clicar no cabeçalho da tabela de campeões lança erro e não faz nada** — `template.html:812` registra o handler de ordenação em **todos** os `thead th` do documento. Os cabeçalhos da tabela "Campeões do grupo" (`template.html:603`), da tabela de duplas (`:657-660`) e da matriz (`:672`, `:684`) não têm `data-k`, então `sortKey` vira `undefined`, `COLS.find` devolve `undefined` e `template.html:507` estoura um TypeError. Como o CSS deixa esses cabeçalhos com `cursor:pointer` e hover dourado, eles parecem clicáveis. **Correção:** escopar para `"#tbl thead th[data-k]"` na linha 812 e adicionar `if(!k) return;`.

**[10] Dois winrates diferentes e contraditórios na mesma linha, sem rótulo** — a célula de Elo mostra o winrate da escada ranqueada (temporada inteira, `template.html:332` monta `${r.lp} PDL · ${r.winrate}%` a partir de League-V4), e a coluna "Vitórias" ao lado mostra o winrate da janela de 90 dias na fila selecionada (`template.html:473`, `p.stats.winrate`). No demo, o mesmo jogador aparece com **58,8% e 37,5%** lado a lado. Pior: nas abas Flex, Normais e ARAM o número da esquerda continua sendo o de **solo** (`template.html:468-470` lê `p.ranks.solo` fixo). Os dois números estão certos — medem coisas diferentes sem dizer. É leitura direta de "os dados estão incorretos". **Correção:** rotular (`${r.winrate}% na ranqueada`) e usar `p.ranks[grupo]` quando existir.

**[11] "OS AFUNDADOS" some da tela sem explicação** — `kpi.py:697-698` calcula pódio como `ranked[:3]` e vergonha como `ranked[-3:]`, que se sobrepõem quando há 5 elegíveis ou menos; o template remove a sobreposição (`template.html:736-737`) mas `template.html:773` não tem fallback para lista vazia. Resultado: o título "OS AFUNDADOS" fica sozinho, sem cards e sem mensagem. Reproduzido com `--days 30` na aba Normais. Mesmo problema nos prêmios (`template.html:517`, `if(!list.length) return ""`, enquanto o cabeçalho já foi impresso) — note que as tabelas de campeões e duplas **têm** mensagem de vazio (`:601`, `:695`), então a inconsistência é só nesses dois blocos. **Correção:** calcular vergonha só com o excedente (`ranked[3:][-3:]`) e adicionar mensagem de estado vazio.

**[12] A semana da virada de ano vira dois pontos no gráfico** — `kpi.py:499` usa `time.strftime("%Y-W%W")`, que joga 01/01 a 04/01 num balde artificial `2026-W00`, separado do `2025-W52` que começou na mesma segunda-feira. Uma semana real vira dois pontos menores e cria uma queda fictícia de winrate que nunca aconteceu. Afeta só o gráfico de tendência semanal, e só em execuções feitas entre janeiro e março. **Correção:** trocar por semana ISO — `time.strftime("%G-W%V")`.

**[13] A escala do UDA Score depende do tamanho do grupo** — `kpi.py:246` usa desvio populacional sobre o próprio grupo, então o |z| máximo é `sqrt(n-1)`. Com apenas 2 jogadores elegíveis numa aba, os scores são sempre 34,5 e 65,5, **independentemente** de estarem empatados ou a um abismo de distância. Um Score 70 na aba Normais não significa o mesmo que 70 na aba Todas. Além disso, os limites `max(1, min(99, ...))` de `kpi.py:259` são código morto: o Score só pode variar entre 9,7 e 90,3, nunca 1–99 como anuncia. O **ranking em si está correto** — só a magnitude do número é que é enganosa. **Correção:** usar baselines absolutos ou desvio amostral com piso, e não publicar Score com menos de 4 elegíveis.

**[14] Nenhuma validação de região no `run.py`** — `fetch.py:31-34` aceita qualquer PUUID que a Account-V1 devolva; nada confere o prefixo do matchId contra `settings.platform`, embora `verificar.py:74-79` já faça exatamente isso (`Counter(m.split("_")[0] for m in ids)`). Uma conta de NA/LAN/LAS traria as partidas dela normalmente pelo cluster `americas`, misturadas com as brasileiras, e o chip continuaria dizendo "BR". **Correção:** rodar `verificar.py` antes da coleta e replicar o filtro de prefixo em `fetch.py:98`.

---

### INFORMATIVO

**[15]** `template.html:442` — com exatamente um jogador elegível, `best` e `worst` são a mesma pessoa e o card "Melhor / Pior" mostra o nome dele e escreve **"sem ranking"** logo abaixo. Só ocorre com `--days` curto.

**[16]** O payload carrega ~43 KB de campos calculados que a tela nunca mostra: `bestGame`, `worstGame`, `roles`, `hotStreak`, `surrender_rate`, `dmg_taken_min`, `cs_game`, `vision_game`, `control_wards_game`, `total_kills` — todos com **zero** ocorrências em `template.html`. Não corrompe nada; é oportunidade desperdiçada (melhor/pior partida e posições mais jogadas seriam ótimas no card do jogador) ou peso de arquivo a cortar.

---

## 4. O QUE **NÃO** É DEFEITO (verificado e correto)

**Já corrigido no código atual** (a auditoria pegou uma versão anterior):
- **Partidas contra bots, tutorial e Arena não entram mais em nada.** `kpi.py:16-19` define `BOT_QUEUES`, `TUTORIAL_QUEUES` e `ARENA_QUEUES`, e `kpi.py:85` aplica `AND p.queue_id NOT IN (...)` direto no SQL. Isso elimina a inflação de winrate/KDA por bot e o problema de "% do dano" no Arena (onde o time real tem 2 pessoas, não 5).
- **Riot IDs agora são limpos antes de usar.** `config.py:44-56` remove caracteres invisíveis (zero-width space, BOM, isolates bidirecionais — o lixo que vem junto ao copiar do Discord), normaliza em NFC, faz `strip()` e avisa no console quando altera algo.

**Verificado e correto:**
- **Rate limiter** (`riot.py:33-46`): janela deslizante correta, 18 req/s e 95 req/2min — conservador frente ao limite real da chave de desenvolvimento. Retries em 429 e 5xx também são contabilizados.
- **Conversão de `startTime`** (`fetch.py:81`): `gameCreation` vem em milissegundos e o parâmetro da Riot é em segundos; a divisão por 1000 e o recuo de 1h estão certos.
- **Escape do JSON no HTML** (`render.py:19`): testei com nome contendo `</script>` — o payload é escapado corretamente e nada é injetado na página.
- **Heurística de duração em ms** (`store.py:164-167`): tecnicamente frágil, mas só afeta partidas anteriores a outubro/2021, que a janela de 90 dias descarta antes. Nenhuma linha do banco jamais passou por esse ramo.
- **Ordenação das duplas** (`kpi.py`, piso `max(3, min_games // 2)`): é escolha de design parametrizada, e o número de jogos aparece ao lado do percentual ("5V 0D"), então a amostra não fica escondida.
- **Prêmios do Hall da Fama/Vergonha**: os vencedores da amostra pequena batem com os da amostra grande no demo — não há "vencedor de ruído".
- **Ícones de campeão**: o fallback usa o `championName` cru da Riot, que já é o ID do Data Dragon, então as URLs resolvem mesmo se o índice de campeões falhar.
- **`team.dmg_share` e `team.kp`**: são agregados por participação por decisão consciente do autor (que criou `uniqueMatches` separado justamente para isso) — e nem sequer são lidos pelo template.