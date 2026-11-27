#!/usr/bin/env python3
"""Coleta de chuva observada do INMET para o Painel Climático.

Roda no GitHub Actions (só biblioteca padrão do Python). Três comandos:

  historico  Baixa os ZIPs anuais de dados históricos do INMET (sem token),
             escolhe a estação automática mais próxima de cada obra e guarda a
             série horária de chuva de cada estação em dados/inmet/serie/.
  api        Busca os últimos dias pela API do INMET. Usa o segredo INMET_TOKEN;
             sem token tenta a rota pública antiga e, se ela recusar, sai sem erro.
  montar     Junta tudo em um JSON por obra (dados/inmet/obras/<slug>.json) com
             chuva diária no horário local e a climatologia por horário do dia.

Convenções
  * O INMET grava a hora em UTC e a chuva da hora H é a acumulada na hora que
    TERMINA em H. Convertemos para o horário civil atual da obra (MT = UTC-4,
    PR/MA = UTC-3, sem horário de verão) e atribuímos a chuva à hora em que ela
    começou. Ex.: 18 UTC em Irati = chuva entre 14h e 15h locais.
  * Valor ausente (-9999 ou vazio) fica nulo. Hora com mais de 150 mm é
    descartada como erro de leitura.
  * Dia local válido: pelo menos 22 das 24 horas com leitura.
"""
import argparse, datetime as dt, io, json, math, os, re, sys, tempfile, unicodedata, urllib.request, zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, "dados")
DIR_INMET = os.path.join(DADOS, "inmet")
DIR_SERIE = os.path.join(DIR_INMET, "serie")
DIR_OBRAS = os.path.join(DIR_INMET, "obras")
ARQ_ESTACOES = os.path.join(DIR_INMET, "estacoes.json")
URL_ZIP = os.environ.get("INMET_ZIP_URL", "https://portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip")
URL_API = os.environ.get("INMET_API_URL", "https://apitempo.inmet.gov.br")
UA = {"User-Agent": "painel-climatico-obras (GitHub Actions)"}
JANELAS = [("Madrugada 0–7h", 0, 7), ("7–10h", 7, 10), ("10–13h", 10, 13), ("13–16h", 13, 16),
           ("16–19h", 16, 19), ("Noite 19–24h", 19, 24), ("Turno 7–17h", 7, 17)]
MAX_HORA_MM = 150.0
MIN_HORAS_DIA = 22


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def ler_json(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return padrao


def gravar_json(caminho, obj, compacto=False):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compacto:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def obras():
    return ler_json(os.path.join(DADOS, "obras.json"))["obras"]


def km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def num(txt):
    txt = (txt or "").strip().replace(",", ".")
    if not txt:
        return None
    try:
        v = float(txt)
    except ValueError:
        return None
    return None if v <= -9990 else v


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").upper()


# ---------------------------------------------------------------- leitura do CSV anual

def ler_cabecalho(linhas):
    meta = {}
    for ln in linhas[:12]:
        partes = ln.split(";")
        if len(partes) < 2:
            continue
        chave = sem_acento(partes[0]).strip().rstrip(":")
        valor = partes[1].strip()
        if chave.startswith("CODIGO"):
            meta["codigo"] = valor.upper()
        elif chave == "ESTACAO":
            meta["nome"] = valor.title()
        elif chave == "UF":
            meta["uf"] = valor.upper()
        elif chave == "LATITUDE":
            meta["lat"] = num(valor)
        elif chave == "LONGITUDE":
            meta["lon"] = num(valor)
        elif chave == "ALTITUDE":
            meta["alt"] = num(valor)
    return meta


def data_hora_utc(txt_data, txt_hora):
    partes = re.split(r"[/-]", txt_data.strip())
    if len(partes) != 3:
        return None
    try:
        if len(partes[0]) == 4:
            a, m, d = int(partes[0]), int(partes[1]), int(partes[2])
        else:
            d, m, a = int(partes[0]), int(partes[1]), int(partes[2])
        digitos = re.sub(r"\D", "", txt_hora)
        if not digitos:
            return None
        h = int(digitos[:-2]) if len(digitos) >= 3 else int(digitos)
        return dt.datetime(a, m, d, h)
    except ValueError:
        return None


def ler_csv(texto):
    """Devolve (meta, {datetime_utc_fim_da_hora: mm ou None})."""
    linhas = texto.splitlines()
    meta = ler_cabecalho(linhas)
    ini = next((i for i, ln in enumerate(linhas) if sem_acento(ln).startswith("DATA") and "HORA" in sem_acento(ln)), None)
    if ini is None:
        return meta, {}
    cols = [sem_acento(c) for c in linhas[ini].split(";")]
    ip = next((i for i, c in enumerate(cols) if "PRECIPITA" in c), None)
    if ip is None:
        return meta, {}
    serie = {}
    for ln in linhas[ini + 1:]:
        p = ln.split(";")
        if len(p) <= ip:
            continue
        t = data_hora_utc(p[0], p[1])
        if t is None:
            continue
        v = num(p[ip])
        if v is not None and (v < 0 or v > MAX_HORA_MM):
            v = None
        serie[t] = v
    return meta, serie


# ---------------------------------------------------------------- série horária por estação/ano

def chave_ano(caminho_codigo, ano):
    return os.path.join(DIR_SERIE, caminho_codigo, f"{ano}.json")


def gravar_serie_ano(codigo, ano, serie, meta):
    """Guarda a série do ano como lista compacta: décimos de mm, de 01/01 00 UTC a 31/12 23 UTC."""
    t0 = dt.datetime(ano, 1, 1, 0)
    n = int((dt.datetime(ano + 1, 1, 1, 0) - t0).total_seconds() // 3600)
    vals = []
    for i in range(n):
        v = serie.get(t0 + dt.timedelta(hours=i))
        vals.append(None if v is None else int(round(v * 10)))
    gravar_json(chave_ano(codigo, ano), {"codigo": codigo, "ano": ano, "inicio_utc": t0.isoformat(),
                                         "unidade": "decimos_mm_hora_terminando", "meta": meta, "v": vals}, compacto=True)
    validas = sum(1 for v in vals if v is not None)
    return validas / n


def baixar_zip(ano, pasta_local=None):
    if pasta_local:
        return os.path.join(pasta_local, f"{ano}.zip")
    url = URL_ZIP.format(ano=ano)
    log(f"Baixando {url}")
    fd, caminho = tempfile.mkstemp(suffix=".zip")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as r, os.fdopen(fd, "wb") as f:
        while True:
            bloco = r.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
    return caminho


def membros_csv(z):
    return [n for n in z.namelist() if n.upper().endswith(".CSV")]


RAIO_KM = 120          # só considera estações até esta distância da obra
MIN_COBERTURA = 0.70   # fração mínima de horas com leitura de chuva nos últimos 180 dias do ZIP


def escolher_estacoes(z, estacoes_atuais, reselecionar):
    """Escolhe, para cada obra, a estação mais próxima QUE ESTEJA MEDINDO CHUVA.

    Muitas estações automáticas passam meses com o pluviômetro sem leitura. A regra é:
    a mais próxima (até RAIO_KM) com pelo menos MIN_COBERTURA de horas com leitura nos
    últimos 180 dias do arquivo mais recente; se nenhuma atingir, a de maior cobertura;
    se nenhuma tiver leitura, a mais próxima.
    """
    cands = []
    for nome in membros_csv(z):
        with z.open(nome) as f:
            cab = f.read(2000).decode("latin-1", "replace")
        meta = ler_cabecalho(cab.splitlines())
        if meta.get("codigo") and meta.get("lat") is not None and meta.get("lon") is not None:
            meta["arquivo"] = nome
            cands.append(meta)
    log(f"{len(cands)} estações no ZIP")
    resultado = dict(estacoes_atuais or {})
    pendentes = [o for o in obras() if o.get("estacao") or reselecionar or o["slug"] not in resultado]
    # cobertura recente de chuva das estações próximas das obras pendentes
    perto = {c["codigo"]: c for o in pendentes for c in cands if km(o["lat"], o["lon"], c["lat"], c["lon"]) <= RAIO_KM}
    series = {}
    for cod, c in perto.items():
        with z.open(c["arquivo"]) as f:
            series[cod] = ler_csv(f.read().decode("latin-1", "replace"))[1]
    fim_zip = max((max(sr) for sr in series.values() if sr), default=None)
    cob = {}
    if fim_zip:
        ini_janela = max(dt.datetime(fim_zip.year, 1, 1, 0), fim_zip - dt.timedelta(days=180))
        total = int((fim_zip - ini_janela).total_seconds() // 3600) + 1
        for cod, sr in series.items():
            cob[cod] = sum(1 for t, v in sr.items() if v is not None and ini_janela <= t <= fim_zip) / total
        log(f"Cobertura medida de {ini_janela:%d/%m/%Y} a {fim_zip:%d/%m/%Y}")

    for o in pendentes:
        dist = lambda c: km(o["lat"], o["lon"], c["lat"], c["lon"])
        ordem = sorted(cands, key=dist)
        if not ordem:
            continue
        no_raio = [c for c in ordem if dist(c) <= RAIO_KM]
        fixa = [c for c in ordem if o.get("estacao") and c["codigo"] == o["estacao"].upper()]
        if o.get("estacao") and not fixa:
            log(f"AVISO: estação {o['estacao']} de {o['nome']} não está no ZIP; escolhendo automaticamente")
        if fixa:
            escolha, motivo = fixa[0], "fixada manualmente em dados/obras.json"
        else:
            boas = [c for c in no_raio if cob.get(c["codigo"], 0) >= MIN_COBERTURA]
            if boas:
                escolha, motivo = boas[0], f"mais próxima com ≥ {MIN_COBERTURA:.0%} de leitura recente"
            elif any(cob.get(c["codigo"], 0) > 0 for c in no_raio):
                escolha = max(no_raio, key=lambda c: cob.get(c["codigo"], 0))
                motivo = f"nenhuma estação até {RAIO_KM} km com ≥ {MIN_COBERTURA:.0%} de leitura; escolhida a de maior cobertura"
            else:
                escolha, motivo = ordem[0], "sem leitura recente nas estações próximas; escolhida a mais próxima"

        def resumo(c):
            r = {"codigo": c["codigo"], "nome": c.get("nome"), "uf": c.get("uf"), "lat": c["lat"], "lon": c["lon"],
                 "alt": c.get("alt"), "distancia_km": round(dist(c), 1)}
            if c["codigo"] in cob:
                r["cobertura_recente"] = round(cob[c["codigo"]], 3)
            return r
        escolhida = resumo(escolha)
        escolhida["fixada_manualmente"] = bool(fixa)
        escolhida["motivo"] = motivo
        escolhida["cobertura_ate"] = fim_zip.date().isoformat() if fim_zip else None
        escolhida["alternativas"] = [resumo(c) for c in no_raio[:6] if c["codigo"] != escolha["codigo"]][:5]
        resultado[o["slug"]] = escolhida
        log(f"{o['nome']}: {escolhida['codigo']} {escolhida['nome']} a {escolhida['distancia_km']} km, "
            f"leitura recente {escolhida.get('cobertura_recente', 0):.0%} ({motivo})")
    return resultado


def cmd_historico(args):
    anos = sorted(set(intervalo_anos(args.anos)), reverse=True)
    estacoes = ler_json(ARQ_ESTACOES, {}).get("obras", {})
    reselecionar = args.reselecionar
    for ano in anos:
        try:
            caminho = baixar_zip(ano, args.pasta_zip)
        except Exception as e:  # ano ainda não publicado, rede etc.
            log(f"ERRO ao baixar {ano}: {e}")
            continue
        try:
            with zipfile.ZipFile(caminho) as z:
                if not estacoes or reselecionar or any(o["slug"] not in estacoes for o in obras()):
                    estacoes = escolher_estacoes(z, estacoes, reselecionar)
                    reselecionar = False
                codigos = {e["codigo"] for e in estacoes.values()}
                for nome in membros_csv(z):
                    m = re.search(r"_(A\d{3}|[A-Z]\d{3})_", nome.upper())
                    cod = m.group(1) if m else None
                    if cod not in codigos:
                        with z.open(nome) as f:
                            cod = ler_cabecalho(f.read(2000).decode("latin-1", "replace").splitlines()).get("codigo")
                        if cod not in codigos:
                            continue
                    with z.open(nome) as f:
                        meta, serie = ler_csv(f.read().decode("latin-1", "replace"))
                    cobertura = gravar_serie_ano(cod, ano, serie, meta)
                    log(f"{ano} {cod}: {cobertura:.0%} das horas com leitura")
        except zipfile.BadZipFile:
            log(f"ERRO: {ano}.zip inválido")
        finally:
            if not args.pasta_zip:
                os.remove(caminho)
    gravar_json(ARQ_ESTACOES, {"atualizado_em": agora(), "obras": estacoes})
    cmd_montar(args)


def intervalo_anos(txt):
    hoje = dt.date.today()
    if not txt:
        return [hoje.year - 1, hoje.year] if hoje.month <= 2 else [hoje.year]
    anos = []
    for parte in txt.split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-")
            anos += list(range(int(a), int(b) + 1))
        elif parte:
            anos.append(int(parte))
    return anos


def agora():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- API (dias recentes)

def cmd_api(args):
    estacoes = ler_json(ARQ_ESTACOES, {}).get("obras")
    if not estacoes:
        log("Sem estações escolhidas ainda: rode o histórico primeiro.")
        return
    token = os.environ.get("INMET_TOKEN", "").strip()
    fim = dt.date.today()
    ini = fim - dt.timedelta(days=args.dias)
    status = {"executado_em": agora(), "com_token": bool(token), "estacoes": {}}
    for cod in sorted({e["codigo"] for e in estacoes.values()}):
        if token:
            url = f"{URL_API}/token/estacao/{ini}/{fim}/{cod}/{token}"
        else:
            url = f"{URL_API}/estacao/{ini}/{fim}/{cod}"
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                dados = json.loads(r.read().decode("utf-8", "replace") or "[]")
        except Exception as e:
            msg = str(e).replace(token, "***") if token else str(e)
            log(f"{cod}: API recusou ({msg})")
            status["estacoes"][cod] = f"erro: {msg}"
            continue
        if not isinstance(dados, list):
            status["estacoes"][cod] = "resposta inesperada"
            continue
        caminho = os.path.join(DIR_SERIE, cod, "api.json")
        atual = ler_json(caminho, {"codigo": cod, "h": {}})
        novos = 0
        for item in dados:
            t = data_hora_utc(str(item.get("DT_MEDICAO", "")), str(item.get("HR_MEDICAO", "")))
            if t is None:
                continue
            v = num(str(item.get("CHUVA", "")))
            if v is not None and (v < 0 or v > MAX_HORA_MM):
                v = None
            atual["h"][t.strftime("%Y-%m-%dT%H")] = None if v is None else round(v, 1)
            novos += 1
        limite = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=400)).strftime("%Y-%m-%dT%H")
        atual["h"] = {k: v for k, v in sorted(atual["h"].items()) if k >= limite}
        gravar_json(caminho, atual, compacto=True)
        status["estacoes"][cod] = f"ok: {novos} horas"
        log(f"{cod}: {novos} horas recebidas")
    gravar_json(os.path.join(DIR_INMET, "api_status.json"), status)
    cmd_montar(args)


# ---------------------------------------------------------------- montagem por obra

def carregar_horas(cod):
    """{datetime_utc_fim_da_hora: mm ou None}; ZIP tem prioridade sobre a API."""
    horas, anos = {}, []
    pasta = os.path.join(DIR_SERIE, cod)
    if not os.path.isdir(pasta):
        return horas, anos, None
    for arq in sorted(os.listdir(pasta)):
        if not re.fullmatch(r"\d{4}\.json", arq):
            continue
        d = ler_json(os.path.join(pasta, arq))
        t0 = dt.datetime.fromisoformat(d["inicio_utc"])
        for i, v in enumerate(d["v"]):
            if v is not None:
                horas[t0 + dt.timedelta(hours=i)] = v / 10.0
        anos.append(d["ano"])
    fim_zip = max(horas) if horas else None
    api = ler_json(os.path.join(pasta, "api.json"), {}).get("h", {})
    for k, v in api.items():
        t = dt.datetime.strptime(k, "%Y-%m-%dT%H")
        if v is not None and (fim_zip is None or t > fim_zip):
            horas[t] = v
    return horas, anos, fim_zip


def cmd_montar(args=None):
    estacoes = ler_json(ARQ_ESTACOES, {}).get("obras", {})
    for o in obras():
        est = estacoes.get(o["slug"])
        if not est:
            continue
        horas, anos_zip, fim_zip = carregar_horas(est["codigo"])
        if not horas:
            log(f"{o['nome']}: sem dados da estação {est['codigo']}")
            continue
        off = dt.timedelta(hours=o["utc"])
        # chuva por (dia local, hora local de início)
        local = {}
        for t_fim, v in horas.items():
            ini_local = t_fim - dt.timedelta(hours=1) + off
            local[ini_local] = v
        dias = {}
        for t, v in local.items():
            dias.setdefault(t.date(), {})[t.hour] = v
        d0, d1 = min(dias), max(dias)
        diario, n = [], (d1 - d0).days + 1
        for i in range(n):
            hs = dias.get(d0 + dt.timedelta(days=i), {})
            diario.append(round(sum(hs.values()), 1) if len(hs) >= MIN_HORAS_DIA else None)
        # climatologia: só anos do ZIP (série consolidada pelo INMET)
        f02 = [[0] * 24 for _ in range(12)]; nh = [[0] * 24 for _ in range(12)]; mm_h = [[0.0] * 24 for _ in range(12)]
        jn = [[0] * len(JANELAS) for _ in range(12)]; j1 = [[0] * len(JANELAS) for _ in range(12)]
        j5 = [[0] * len(JANELAS) for _ in range(12)]; jmm = [[0.0] * len(JANELAS) for _ in range(12)]
        for dia, hs in dias.items():
            if dia.year not in anos_zip:
                continue
            m = dia.month - 1
            for h, v in hs.items():
                nh[m][h] += 1; mm_h[m][h] += v
                if v >= 0.2:
                    f02[m][h] += 1
            for k, (_, a, b) in enumerate(JANELAS):
                if all(h in hs for h in range(a, b)):
                    tot = sum(hs[h] for h in range(a, b))
                    jn[m][k] += 1; jmm[m][k] += tot
                    if tot >= 1: j1[m][k] += 1
                    if tot >= 5: j5[m][k] += 1
        validos = sum(1 for v in diario if v is not None)
        saida = {
            "obra": o["nome"], "slug": o["slug"], "utc": o["utc"], "atualizado_em": agora(),
            "estacao": {k: est.get(k) for k in ("codigo", "nome", "uf", "lat", "lon", "alt", "distancia_km", "fixada_manualmente",
                                                 "motivo", "cobertura_recente", "cobertura_ate", "alternativas")},
            "fonte": "INMET — estações automáticas (dados históricos anuais + API)",
            "zip_ate": (fim_zip + off - dt.timedelta(hours=1)).date().isoformat() if fim_zip else None,
            "diario": {"inicio": d0.isoformat(), "fim": d1.isoformat(), "cobertura": round(validos / n, 3), "mm": diario},
            "clima": {
                "anos": sorted(anos_zip),
                "horas": {"n": nh, "freq02": f02, "mm": [[round(x, 1) for x in r] for r in mm_h]},
                "janelas": {"nomes": [j[0] for j in JANELAS], "inicio_fim": [[j[1], j[2]] for j in JANELAS],
                             "n": jn, "chuva1": j1, "chuva5": j5, "mm": [[round(x, 1) for x in r] for r in jmm]},
            },
        }
        gravar_json(os.path.join(DIR_OBRAS, f"{o['slug']}.json"), saida, compacto=True)
        log(f"{o['nome']}: {d0} a {d1}, cobertura diária {validos / n:.0%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("historico")
    h.add_argument("--anos", default="", help="ex.: 2008-2026 (padrão: ano atual; em jan/fev também o anterior)")
    h.add_argument("--reselecionar", action="store_true", help="refaz a escolha da estação mais próxima")
    h.add_argument("--pasta-zip", default=None, help="lê <ano>.zip desta pasta em vez de baixar (testes)")
    a = sub.add_parser("api")
    a.add_argument("--dias", type=int, default=20)
    sub.add_parser("montar")
    args = ap.parse_args()
    {"historico": cmd_historico, "api": cmd_api, "montar": cmd_montar}[args.cmd](args)


if __name__ == "__main__":
    main()
