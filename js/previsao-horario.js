/* Previsão de chuva por faixa de horário — próximos 7 dias.
 * Usado pela página inicial (index.html) e por clima-obra.html.
 *
 *   PrevisaoHorario.render(elemento, {nome, lat, lon, tz})
 *
 * Previsão: Open-Meteo (ECMWF IFS HRES, GFS, ICON, de hora em hora) + probabilidade do ensemble.
 * Linha "Histórico": dados/inmet/obras/<obra>.json gerado pela automação do INMET (se existir).
 */
(function () {
  const MODELOS = [['ecmwf_ifs', 'ECMWF'], ['gfs_seamless', 'GFS'], ['icon_seamless', 'ICON']];
  const JANELAS_PADRAO = {
    nomes: ['Madrugada 0–7h', '7–10h', '10–13h', '13–16h', '16–19h', 'Noite 19–24h', 'Turno 7–17h'],
    inicio_fim: [[0, 7], [7, 10], [10, 13], [13, 16], [16, 19], [19, 24], [7, 17]]
  };
  const MES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
  const DSEM = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'];
  const pad = n => String(n).padStart(2, '0');
  const addDias = (s, n) => { const d = new Date(s + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const fmt = (v, dec) => v == null || isNaN(v) ? '—' : v.toLocaleString('pt-BR', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const slug = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const cacheFc = {}, cacheInmet = {};

  const CSS = `
.ph-info{font-size:13.5px;line-height:1.5;margin:0 0 10px}
.ph-scroll{overflow-x:auto}
.ph-tab{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
.ph-tab th,.ph-tab td{padding:7px 6px;border-bottom:1px solid var(--line,var(--border,rgba(0,0,0,.1)));text-align:center;white-space:nowrap}
.ph-tab th{font-weight:600;opacity:.7;font-size:12px}
.ph-tab td{min-width:78px;line-height:1.25;font-weight:600}
.ph-tab td:first-child,.ph-tab th:first-child{text-align:left;font-weight:400;min-width:96px}
.ph-tab td:last-child,.ph-tab th:last-child{border-left:2px solid var(--line,var(--border2,rgba(0,0,0,.18)))}
.ph-tab td small{display:block;font-weight:400;font-size:11px;opacity:.85}
.ph-tab tr.ph-longe td:not(:first-child){opacity:.5}
.ph-tab tr.ph-hist td{font-weight:400;font-style:italic;border-bottom:2px solid var(--line,var(--border2,rgba(0,0,0,.18)))}
.ph-tab td.ph-passou{opacity:.3}
.ph-nota{font-size:12.5px;opacity:.75;margin:10px 0 0;line-height:1.5}
.ph-alerta{font-size:13px;padding:8px 10px;border-radius:7px;background:rgba(217,119,6,.12)}`;
  function injetarCss() {
    if (document.getElementById('ph-css')) return;
    const st = document.createElement('style'); st.id = 'ph-css'; st.textContent = CSS; document.head.appendChild(st);
  }

  async function getJSON(url) {
    const r = await fetch(url); const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.reason || ('HTTP ' + r.status));
    return j;
  }
  async function carregarInmet(nome) {
    const k = slug(nome); if (k in cacheInmet) return cacheInmet[k];
    try { const r = await fetch(`dados/inmet/obras/${k}.json`, { cache: 'no-cache' }); if (!r.ok) throw 0; return cacheInmet[k] = await r.json(); }
    catch (e) { return cacheInmet[k] = null; }
  }
  // chave "data|hora de início" -> valor (o Open-Meteo dá a chuva da hora ANTERIOR ao horário)
  function porInicio(H, chave) {
    const m = new Map(); if (!H || !H[chave]) return m;
    H.time.forEach((t, i) => {
      const v = H[chave][i]; if (v == null) return;
      const d = new Date(t + ':00Z'); d.setUTCHours(d.getUTCHours() - 1);
      m.set(d.toISOString().slice(0, 10) + '|' + d.getUTCHours(), v);
    });
    return m;
  }
  async function carregarPrevisao(o) {
    const k = o.nome + '|' + new Date().toISOString().slice(0, 13); if (cacheFc[k]) return cacheFc[k];
    const base = `https://api.open-meteo.com/v1/forecast?latitude=${o.lat}&longitude=${o.lon}&forecast_days=7&timezone=${encodeURIComponent(o.tz)}`;
    const [r1, r2] = await Promise.allSettled([
      getJSON(`${base}&hourly=precipitation&models=${MODELOS.map(m => m[0]).join(',')}`),
      getJSON(`${base}&hourly=precipitation_probability`)
    ]);
    const out = { modelos: {}, prob: new Map(), ok: false };
    if (r1.status === 'fulfilled') {
      const H = r1.value.hourly;
      MODELOS.forEach(([id]) => { if (H[`precipitation_${id}`]) { out.modelos[id] = porInicio(H, `precipitation_${id}`); out.ok = true; } });
    }
    if (r2.status === 'fulfilled') { out.prob = porInicio(r2.value.hourly, 'precipitation_probability'); out.ok = true; }
    return cacheFc[k] = out;
  }
  function agoraLocal(tz) {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23' })
      .formatToParts(new Date()).map(x => [x.type, x.value]));
    return { dia: `${p.year}-${p.month}-${p.day}`, hora: +p.hour };
  }

  async function render(el, o, opcoes) {
    opcoes = Object.assign({ nota: true }, opcoes || {});
    injetarCss();
    const pedido = String(Math.random()); el.dataset.phPedido = pedido;
    el.innerHTML = '<div class="ph-info" style="opacity:.6">Carregando previsão por horário…</div>';
    let F = null, I = null;
    try { [F, I] = await Promise.all([carregarPrevisao(o), carregarInmet(o.nome)]); } catch (e) { F = null; }
    if (el.dataset.phPedido !== pedido) return; // usuário já trocou de obra
    if (!F || !F.ok) { el.innerHTML = '<div class="ph-alerta">Não foi possível carregar a previsão agora.</div>'; return; }

    const J = I ? I.clima.janelas : JANELAS_PADRAO;
    const now = agoraLocal(o.tz), mes = +now.dia.slice(5, 7) - 1, iT = J.nomes.length - 1;
    const mods = MODELOS.filter(([id]) => F.modelos[id]);
    let info = '', html = `<thead><tr><th>Dia</th>${J.nomes.map(n => `<th>${n}</th>`).join('')}</tr></thead><tbody>`;
    if (I) {
      const p = J.nomes.map((_, k) => J.n[mes][k] ? J.chuva1[mes][k] / J.n[mes][k] : null);
      html += `<tr class="ph-hist"><td>Histórico de ${MES[mes]}<small>chance de ≥ 1 mm</small></td>${p.map(v => `<td>${v == null ? '—' : Math.round(v * 100) + '%'}</td>`).join('')}</tr>`;
      const pk = p.slice(0, iT).reduce((b, v, k) => v != null && (b < 0 || v > p[b]) ? k : b, -1);
      if (pk >= 0 && p[pk] > 0) info = `No histórico da estação INMET ${I.estacao.codigo} (${I.estacao.nome || ''}, ${fmt(I.estacao.distancia_km, 0)} km), em ${MES[mes]} a faixa com mais chuva é <b>${J.nomes[pk]}</b> (${Math.round(p[pk] * 100)}% dos dias).`;
    }
    for (let d = 0; d < 7; d++) {
      const dia = addDias(now.dia, d), dw = new Date(dia + 'T12:00:00Z').getUTCDay();
      html += `<tr class="${d >= 3 ? 'ph-longe' : ''}"><td>${d === 0 ? 'Hoje' : d === 1 ? 'Amanhã' : DSEM[dw]} ${dia.slice(8)}/${dia.slice(5, 7)}</td>`;
      J.inicio_fim.forEach(([a, b]) => {
        const hrs = [...Array(b - a)].map((_, i) => a + i);
        const somas = mods.map(([id, nm]) => { const v = hrs.map(h => F.modelos[id].get(dia + '|' + h)); return v.some(x => x == null) ? null : [nm, v.reduce((x, y) => x + y, 0)]; }).filter(Boolean);
        const k = somas.filter(x => x[1] >= 1).length, n = somas.length, mm = n ? somas.reduce((x, y) => x + y[1], 0) / n : null;
        const pr = hrs.map(h => F.prob.get(dia + '|' + h)).filter(x => x != null), pmax = pr.length ? Math.max(...pr) : null;
        if (!n && pmax == null) { html += '<td style="opacity:.5">—</td>'; return; }
        const a_ = n ? k / n : 0, passou = d === 0 && b <= now.hora;
        html += `<td class="${passou ? 'ph-passou' : ''}" title="${somas.map(x => `${x[0]}: ${fmt(x[1], 1)} mm`).join(' · ')}" style="background:rgba(45,108,192,${(a_ * 0.85).toFixed(2)});color:${a_ > 0.6 ? '#fff' : 'inherit'}">${n ? `${k}/${n}` : '—'}<small>${pmax != null ? pmax + '%' : ''}${mm != null ? ` · ${fmt(mm, mm < 10 ? 1 : 0)} mm` : ''}</small></td>`;
      });
      html += '</tr>';
    }
    el.innerHTML = (info ? `<div class="ph-info">${info}</div>` : '') +
      `<div class="ph-scroll"><table class="ph-tab">${html}</tbody></table></div>` +
      (opcoes.nota ? `<p class="ph-nota"><b>Como ler:</b> cada célula mostra quantos dos 3 modelos (ECMWF, GFS, ICON) preveem chuva ≥ 1 mm na faixa; embaixo, a maior chance de chuva hora a hora (inclui garoa) e a chuva média prevista. Passe o mouse para ver cada modelo. 3/3 = risco alto; 1/3 = possível pancada isolada. Até o 3º dia o horário ajuda a programar o serviço; do 4º em diante (linhas apagadas), use só para saber se o dia tem risco. Os modelos tendem a <b>adiantar a chuva da tarde</b> em 2–3 h: compare com a linha "Histórico", que mostra quando ela costuma cair de fato.</p>` : '');
  }

  window.PrevisaoHorario = { render };
})();
