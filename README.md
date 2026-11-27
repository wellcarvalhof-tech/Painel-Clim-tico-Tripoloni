# Painel-Clim-tico-Tripoloni

Painel de clima das cidades das obras (GitHub Pages).

- `index.html`: painel principal (previsão multimodelo, ano atual, normais).
- `clima-obra.html`: Clima × Obra: dias perdidos, cenários de El Niño, chuva realizada (INMET e pluviômetro) e horário da chuva.

## Dados do INMET (realizado e horário da chuva)

O site é estático. Quem busca os dados do INMET é a automação do GitHub (aba **Actions**), que grava os arquivos em `dados/inmet/`. O token do INMET fica guardado como segredo do repositório e **nunca aparece no site**.

| Automação | Quando roda | Precisa de token? | O que faz |
|---|---|---|---|
| INMET — histórico anual | dia 6 de cada mês, ou manual | Não | Baixa os arquivos anuais do INMET, escolhe a estação automática mais próxima de cada obra e monta a série horária |
| INMET — atualização diária | todo dia às 06:23 | Sim (sem ele tenta a rota pública) | Busca os últimos 30 dias pela API, para o realizado ficar em dia |

### Primeira configuração

1. **Pedir o token**: e-mail para `cadastro.act@inmet.gov.br`, de preferência do e-mail da empresa.
2. **Guardar o token**: *Settings → Secrets and variables → Actions → New repository secret*. Nome: `INMET_TOKEN`, valor: o token recebido.
3. **Carregar o histórico**: *Actions → INMET — histórico anual → Run workflow* (padrão `2008-2026`). Leva de 20 a 60 minutos.
4. **Depois do token**: *Actions → INMET — atualização diária → Run workflow* com `dias = 60`, para cobrir o intervalo entre o fim do arquivo anual e hoje.

Sem token, o painel funciona com o histórico, atualizado uma vez por mês (o INMET publica o arquivo do ano corrente com cerca de um mês de atraso).

### Estação de cada obra

A escolha automática pega a estação mais próxima (até 120 km) cujo pluviômetro teve leitura em pelo menos 70% das horas nos últimos 6 meses; muitas estações passam meses sem medir chuva. O resultado fica em `dados/inmet/estacoes.json`, com distância, cobertura e alternativas. Para forçar outra estação, preencha `"estacao": "A8xx"` na obra em `dados/obras.json` e rode o histórico com **Refazer a escolha** marcado.

### Dias sem leitura (estações reserva)

Cada obra tem até 2 estações reserva (até 120 km). Quando o pluviômetro da estação principal não registra um dia, o painel usa a reserva mais próxima que registrou. A página mostra quanto do período veio de cada estação e quanto ficou sem leitura.

### Previsão por horário

A tabela "Próximos 7 dias" consulta o Open-Meteo ao abrir a página (ECMWF IFS HRES, GFS e ICON, de hora em hora) e mostra, por faixa do dia, quantos modelos preveem chuva ≥ 1 mm. Não precisa de automação nem de token.

### Horário

O INMET grava em UTC. O script converte para o horário civil atual da obra (MT = UTC−4; PR e MA = UTC−3) e atribui a chuva à hora em que ela começou.

## Pluviômetro do canteiro

Crie um arquivo por obra em `dados/pluviometro/<obra>.csv` (ex.: `irati.csv`, `miranda-do-norte.csv`), no formato de `dados/pluviometro/MODELO.csv`:

```
data;mm
02/12/2026;12,5
03/12/2026;4
```

- Aceita `dd/mm/aaaa` ou `aaaa-mm-dd`, e decimal com vírgula ou ponto.
- Dia sem linha conta como zero. Lance pelo menos os dias com chuva.
- Pelo GitHub: abra a pasta, clique em *Add file → Create new file* (ou edite o arquivo) e salve. O painel atualiza em 1 a 2 minutos.
