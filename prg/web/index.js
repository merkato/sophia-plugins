// Wtyczka PRG — strony „PRG — Import”, „PRG — Rejestr”, „PRG — Porównanie”.
// Zwykły moduł ES bez budowania; React od Sophii (host.React).

const RODZAJE = { obreb: "Obręby ewidencyjne", nadlesnictwo: "Nadleśnictwa" };
const STATUS = { wysylanie: "wysyłanie", wczytywanie: "wczytywanie", roboczy: "do zatwierdzenia", scalanie: "scalanie",
  zatwierdzony: "zatwierdzony", odrzucony: "odrzucony", blad: "błąd" };
const AKCJE = { nowy: "nowe", zmieniony: "zmienione", bez_zmian: "bez zmian", brak_w_zakresie: "zamknięte (brak w obszarze)",
  zastapiony: "zamknięte (kod przejęty)", pomin: "pominięte" };
const STANY = { roznica: "rozbieżność", tylko_prg: "tylko w PRG", tylko_porownywane: "tylko w porównywanych", zgodne: "zgodne" };
const ZAMKNIECIE = { nowa_wersja: "nowa wersja", brak_w_zakresie: "brak w obszarze", zastapiony: "kod przejęty" };

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState, useCallback } = React;
  const fmtDate = (d) => (d ? new Date(d).toLocaleString("pl-PL") : "—");
  const fmtDay = (d) => (d ? new Date(d).toLocaleDateString("pl-PL") : "—");
  const mb = (b) => `${(b / 1048576).toFixed(1)} MB`;
  const lines = (t) => t.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
  const num = (v, d = 2) => (v == null ? "—" : Number(v).toLocaleString("pl-PL", { maximumFractionDigits: d }));
  const obszarTxt = (o) => (o && o.length ? o.join(", ") : "cały kraj");

  function useRodzaje(onError) {
    const [data, setData] = useState(null);
    const load = useCallback(() => host.api("/rodzaje").then(setData).catch((e) => onError(e.message)), []);
    useEffect(() => { load(); }, [load]);
    return [data, load];
  }

  // ------------------------------------------------------------------ import
  function Importy() {
    const [list, setList] = useState([]);
    const [sel, setSel] = useState(null);
    const [error, setError] = useState(null);
    const [rodzaje, loadRodzaje] = useRodzaje(setError);
    const load = useCallback(() => host.api("/importy").then(setList).catch((e) => setError(e.message)), []);
    useEffect(() => { load(); }, [load]);
    const changed = () => { load(); loadRodzaje(); };
    return h("div", { className: "prg" },
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      rodzaje && h("p", { className: "muted" }, "W rejestrze: ",
        rodzaje.rodzaje.map((r) => `${r.nazwa.toLowerCase()} ${r.w_rejestrze}` +
          (r.ostatni_import ? ` (import ${fmtDay(r.ostatni_import.zatwierdzono)})` : "")).join(" · ")),
      h(NowyImport, { rodzaje, onDone: (id) => { load(); setSel(id); }, onError: setError }),
      h("table", { className: "grid", style: { marginTop: 12 } },
        h("thead", null, h("tr", null, ["#", "Rodzaj", "Źródło", "Obszar", "Status", "Utworzył", "Utworzono"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, list.map((i) => h("tr", { key: i.id, onClick: () => setSel(i.id), style: { cursor: "pointer" },
          className: sel === i.id ? "selected" : "" },
          h("td", null, i.id), h("td", null, RODZAJE[i.rodzaj] || i.rodzaj),
          h("td", null, i.zrodlo === "wfs" ? "usługa WFS PRG" : i.plik, i.rozmiar ? h("div", { className: "muted" }, mb(i.rozmiar)) : null),
          h("td", null, obszarTxt(i.obszar), h("div", { className: "muted" }, i.zakres_typ === "pelny" ? "pełny" : "uzupełnienie")),
          h("td", null, STATUS[i.status] || i.status, i.postep && i.status !== "zatwierdzony" ? h("div", { className: "muted" }, i.postep) : null),
          h("td", null, i.utworzyl_nazwa || "—"), h("td", null, fmtDate(i.utworzono)))))),
      sel && h(Szczegoly, { key: sel, id: sel, onChanged: changed, onError: setError }));
  }

  function NowyImport({ rodzaje, onDone, onError }) {
    const [rodzaj, setRodzaj] = useState("nadlesnictwo");
    const [zrodlo, setZrodlo] = useState("wfs");
    const [obszar, setObszar] = useState("");
    const [zakres, setZakres] = useState("pelny");
    const [file, setFile] = useState(null);
    const [busy, setBusy] = useState(null);
    const opis = rodzaje && (rodzaje.rodzaje.find((r) => r.id === rodzaj) || {}).obszar_opis;
    const send = async () => {
      try {
        const body = { rodzaj, zrodlo, obszar: lines(obszar), zakres_typ: zakres };
        if (zrodlo === "plik") Object.assign(body, { plik: file.name, rozmiar: file.size });
        if (zakres === "pelny" && !body.obszar.length && !window.confirm(
          "Pełny import całego kraju: obiekty rejestru, których nie będzie w źródle, zostaną zamknięte. Kontynuować?")) return;
        setBusy("Zakładanie importu…");
        const { id, chunk } = await host.api("/importy", { method: "POST", body: JSON.stringify(body) });
        if (zrodlo === "plik") {
          for (let off = 0; off < file.size; off += chunk) {
            setBusy(`Wysyłanie ${Math.round((off / file.size) * 100)}%`);
            await host.api(`/importy/${id}/plik?offset=${off}`, { method: "PUT", body: file.slice(off, off + chunk),
              headers: { "Content-Type": "application/octet-stream" } });
          }
          await host.api(`/importy/${id}/wczytaj`, { method: "POST" });
          setFile(null);
        }
        host.notify(`Import #${id}: trwa ${zrodlo === "wfs" ? "pobieranie z usługi PRG" : "wczytywanie pliku"}`);
        onDone(id);
      } catch (e) { onError(e.message); } finally { setBusy(null); }
    };
    return h("div", { className: "form", style: { maxWidth: 760 } },
      h("h3", null, "Nowy import PRG"),
      h("p", { className: "muted" }, "Dane z Państwowego Rejestru Granic (GUGiK). Przed zapisem w rejestrze zobaczysz, co jest nowe, ",
        "co się zmieniło i co zostanie zamknięte; obiekty bez identyfikatora, kodu albo geometrii i duble trafiają do raportu."),
      h("label", { className: "field" }, h("span", null, "Rodzaj"),
        h("select", { value: rodzaj, onChange: (e) => setRodzaj(e.target.value) },
          Object.entries(RODZAJE).map(([k, v]) => h("option", { key: k, value: k }, v)))),
      h("label", { className: "field" }, h("span", null, "Źródło"),
        h("select", { value: zrodlo, onChange: (e) => setZrodlo(e.target.value) },
          h("option", { value: "wfs" }, "usługa WFS PRG (pobranie z GUGiK)"),
          h("option", { value: "plik" }, "plik z paczki PRG (ZIP z SHP albo GML)"))),
      zrodlo === "plik" && h("label", { className: "field" }, h("span", null, "Plik"),
        h("input", { type: "file", accept: ".zip,.gml,.xml", onChange: (e) => setFile(e.target.files[0] || null) })),
      h("label", { className: "field" }, h("span", null, "Obszar"),
        h("input", { value: obszar, placeholder: "puste = cały kraj", onChange: (e) => setObszar(e.target.value) })),
      opis && h("p", { className: "muted" }, `Obszar: ${opis}; kilka wartości rozdziel przecinkami.`),
      h("label", { className: "field" }, h("span", null, "Zakres"),
        h("select", { value: zakres, onChange: (e) => setZakres(e.target.value) },
          h("option", { value: "pelny" }, "pełny — obiekty z obszaru, których nie ma w źródle, zostaną zamknięte"),
          h("option", { value: "uzupelnienie" }, "uzupełnienie — nic nie jest zamykane (poza przejętym kodem)"))),
      h("div", { className: "actions" },
        h("button", { className: "primary", disabled: !!busy || (zrodlo === "plik" && !file), onClick: send },
          busy || (zrodlo === "wfs" ? "Pobierz z PRG" : "Wyślij i wczytaj")),
        file && h("span", { className: "muted" }, ` ${file.name}, ${mb(file.size)}`)));
  }

  function Szczegoly({ id, onChanged, onError }) {
    const [imp, setImp] = useState(null);
    const load = useCallback(() => host.api(`/importy/${id}`).then((i) => { setImp(i); return i; }), [id]);
    useEffect(() => {
      let timer;
      const tick = () => load().then((i) => {
        if (["wczytywanie", "scalanie"].includes(i.status)) timer = setTimeout(tick, 2000); else onChanged();
      }).catch((e) => onError(e.message));
      tick();
      return () => clearTimeout(timer);
    }, [load]);
    if (!imp) return h("p", { className: "muted" }, "Wczytywanie…");
    const act = async (path, confirmText) => {
      if (confirmText && !window.confirm(confirmText)) return;
      try { await host.api(`/importy/${id}/${path}`, { method: "POST" }); await load(); onChanged(); }
      catch (e) { onError(e.message); }
    };
    const st = imp.statystyka || {};
    const r = imp.raport || {};
    const zamyka = (st.brak_w_zakresie || 0) + (st.zastapiony || 0);
    return h("div", { className: "form", style: { marginTop: 14, maxWidth: 960 } },
      h("h3", null, `Import #${imp.id}: ${RODZAJE[imp.rodzaj]} — ${imp.zrodlo === "wfs" ? "usługa WFS PRG" : imp.plik}`),
      h("p", null, h("b", null, STATUS[imp.status] || imp.status), imp.postep ? ` — ${imp.postep}` : ""),
      h("p", { className: "muted" }, [`obszar: ${obszarTxt(imp.obszar)}`, imp.zakres_typ === "pelny" ? "zakres pełny" : "uzupełnienie",
        r.obiekty_w_zrodle != null && `w źródle ${r.obiekty_w_zrodle}`, r.poza_obszarem && `poza obszarem ${r.poza_obszarem} (pominięte)`,
        imp.sha256 && `SHA-256 ${imp.sha256.slice(0, 16)}…`].filter(Boolean).join(" · ")),
      r.blad && h("div", { className: "error" }, r.blad),
      (r.ostrzezenia || []).map((w, i) => h("div", { key: i, className: "notice" }, w)),
      Object.keys(AKCJE).some((k) => st[k]) && h("table", { className: "grid" },
        h("thead", null, h("tr", null, Object.values(AKCJE).map((a) => h("th", { key: a }, a)), h("th", null, "w rejestrze"))),
        h("tbody", null, h("tr", null, Object.keys(AKCJE).map((a) => h("td", { key: a }, st[a] || 0)), h("td", null, st.w_rejestrze ?? "—")))),
      (r.pominiete || []).length > 0 && h("details", { style: { marginTop: 8 } },
        h("summary", null, `Pominięte obiekty (${st.pomin || r.pominiete.length})`),
        h("table", { className: "grid" }, h("tbody", null, r.pominiete.map((p, i) => h("tr", { key: i },
          h("td", null, p.obiekt), h("td", null, p.uwaga)))))),
      imp.status === "roboczy" && h("div", { className: "actions" },
        h("button", { className: "primary", onClick: () => act("zatwierdz",
          `Zatwierdzić import i zapisać w rejestrze PRG?${zamyka ? `\n\nZostanie zamkniętych ${zamyka} obiektów (trafią do historii).` : ""}`) },
          "Zatwierdź"),
        h("button", { onClick: () => act("odrzuc", "Odrzucić import? Dane robocze zostaną usunięte.") }, "Odrzuć")),
      ["wysylanie", "blad"].includes(imp.status) && h("div", { className: "actions" },
        h("button", { onClick: () => act("odrzuc") }, "Odrzuć")));
  }

  // ------------------------------------------------------------------ rejestr
  function Rejestr() {
    const [rodzaj, setRodzaj] = useState("nadlesnictwo");
    const [q, setQ] = useState("");
    const [list, setList] = useState(null);
    const [det, setDet] = useState(null);
    const [error, setError] = useState(null);
    const [rodzaje] = useRodzaje(setError);
    const search = () => host.api(`/obiekty/${rodzaj}?q=${encodeURIComponent(q.trim())}`).then(setList).catch((e) => setError(e.message));
    const open = (k) => host.api(`/obiekty/${rodzaj}/${encodeURIComponent(k)}?crs=4326`).then(setDet).catch((e) => setError(e.message));
    const show = (b) => {
      if (!rodzaje || !rodzaje.projekt) { setError("Administrator nie wskazał projektu mapy (Wtyczki → PRG → Ustawienia)"); return; }
      host.showOnMap({ project: rodzaje.projekt, geometry: b.geometry, label: `${b.kod} ${b.nazwa || ""}`, newTab: true });
    };
    return h("div", { className: "prg" },
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("div", { className: "inline", style: { maxWidth: 720 } },
        h("select", { value: rodzaj, onChange: (e) => { setRodzaj(e.target.value); setList(null); setDet(null); } },
          Object.entries(RODZAJE).map(([k, v]) => h("option", { key: k, value: k }, v))),
        h("input", { value: q, placeholder: rodzaj === "obreb" ? "Identyfikator obrębu (początek) albo nazwa" : "Kod (np. 17-02) albo nazwa",
          onChange: (e) => setQ(e.target.value), onKeyDown: (e) => e.key === "Enter" && q.trim().length >= 2 && search() }),
        h("button", { className: "primary", disabled: q.trim().length < 2, onClick: search }, "Szukaj")),
      list && (list.length === 0 ? h("p", { className: "muted" }, "Nic nie znaleziono.") : h("table", { className: "grid", style: { marginTop: 10, maxWidth: 820 } },
        h("thead", null, h("tr", null, ["Kod", "Nazwa", "Pow. (ha)", "Ważny w PRG od", "W rejestrze od"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, list.map((o) => h("tr", { key: o.klucz, onClick: () => open(o.klucz), style: { cursor: "pointer" } },
          h("td", null, o.kod), h("td", null, o.nazwa), h("td", null, num(o.pow_ha)), h("td", null, fmtDay(o.wazny_od)),
          h("td", null, fmtDate(o.wazna_od))))))),
      det && h(Obiekt, { det, onShow: show }));
  }

  function Obiekt({ det, onShow }) {
    const b = det.biezacy;
    return h("div", { className: "form", style: { marginTop: 14, maxWidth: 960 } },
      b ? h("div", null,
        h("h3", null, `${b.kod} ${b.nazwa || ""}`),
        h("p", null, `Powierzchnia ${num(b.pow_ha, 4)} ha · ważny w PRG od ${fmtDay(b.wazny_od)} · wersja ${b.wersja_id || "—"}`),
        h("p", { className: "muted" }, `Identyfikator ${b.klucz} · w rejestrze od ${fmtDate(b.wazna_od)}, import #${b.import_id}`),
        h("div", { className: "actions" }, h("button", { onClick: () => onShow(b), title: "Otwiera mapę w nowej karcie" }, "Pokaż na mapie ↗")),
        h("details", null, h("summary", null, "Wszystkie pola PRG"),
          h("table", { className: "grid" }, h("tbody", null, Object.entries(b.atrybuty || {}).map(([k, v]) =>
            h("tr", { key: k }, h("td", null, k), h("td", null, v)))))))
        : h("h3", null, "Obiekt zamknięty (tylko historia)"),
      h("h4", null, `Historia (${det.historia.length})`),
      det.historia.length ? h("table", { className: "grid" },
        h("thead", null, h("tr", null, ["Ważna od", "do", "Powód", "Kod", "Nazwa", "Pow. (ha)", "Wersja"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, det.historia.map((v, i) => h("tr", { key: i },
          h("td", null, fmtDate(v.wazna_od)), h("td", null, fmtDate(v.wazna_do)), h("td", null, ZAMKNIECIE[v.zamkniecie] || v.zamkniecie),
          h("td", null, v.kod), h("td", null, v.nazwa), h("td", null, num(v.pow_ha, 4)), h("td", null, v.wersja_id || "—")))))
        : h("p", { className: "muted" }, "Brak wcześniejszych wersji."));
  }

  // ------------------------------------------------------------------ porównanie
  function Porownanie() {
    const [list, setList] = useState([]);
    const [sel, setSel] = useState(null);
    const [error, setError] = useState(null);
    const [rodzaje] = useRodzaje(setError);
    const [rodzaj, setRodzaj] = useState("nadlesnictwo");
    const [obszar, setObszar] = useState("");
    const [busy, setBusy] = useState(false);
    const load = useCallback(() => host.api("/porownania").then((l) => { setList(l); return l; }).catch((e) => setError(e.message)), []);
    useEffect(() => {
      let timer;
      const tick = () => load().then((l) => { if (l && l.some((p) => p.status === "w_toku")) timer = setTimeout(tick, 3000); });
      tick();
      return () => clearTimeout(timer);
    }, [load, sel]);
    const r = rodzaje && rodzaje.rodzaje.find((x) => x.id === rodzaj);
    const start = async () => {
      setBusy(true);
      try {
        const { id } = await host.api("/porownania", { method: "POST", body: JSON.stringify({ rodzaj, obszar: lines(obszar) }) });
        host.notify(`Porównanie #${id} uruchomione`);
        setSel(id); await load();
      } catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    return h("div", { className: "prg" },
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("div", { className: "form", style: { maxWidth: 760 } },
        h("h3", null, "Porównanie z granicami z innego źródła"),
        h("p", { className: "muted" }, "Granice z rejestru PRG zestawiane z tabelą wskazaną w ustawieniach wtyczki (np. dane Lasów Państwowych). ",
          "Wynik: obiekty zgodne, z rozbieżnościami, tylko w PRG, tylko w drugim źródle; obszary rozbieżności szersze niż tolerancja ",
          "są też w widoku prg.v_roznice_ostatnie (warstwa do QGIS)."),
        h("label", { className: "field" }, h("span", null, "Rodzaj"),
          h("select", { value: rodzaj, onChange: (e) => setRodzaj(e.target.value) },
            Object.entries(RODZAJE).map(([k, v]) => h("option", { key: k, value: k }, v)))),
        r && h("p", { className: "muted" }, r.porownanie ? `Porównywana tabela: ${r.porownanie}` : "Brak tabeli do porównania — ustawia ją administrator (Wtyczki → PRG → Ustawienia).",
          ` · w rejestrze PRG: ${r.w_rejestrze}`),
        h("label", { className: "field" }, h("span", null, "Obszar"),
          h("input", { value: obszar, placeholder: "puste = wszystko", onChange: (e) => setObszar(e.target.value) })),
        h("div", { className: "actions" },
          host.can("prg.import") && h("button", { className: "primary", disabled: busy || !r || !r.porownanie || !r.w_rejestrze, onClick: start }, "Porównaj"))),
      h("table", { className: "grid", style: { marginTop: 12 } },
        h("thead", null, h("tr", null, ["#", "Rodzaj", "Tabela", "Obszar", "Status", "Wynik", "Utworzono"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, list.map((p) => h("tr", { key: p.id, onClick: () => setSel(p.id), style: { cursor: "pointer" },
          className: sel === p.id ? "selected" : "" },
          h("td", null, p.id), h("td", null, RODZAJE[p.rodzaj]), h("td", null, p.tabela, h("div", { className: "muted" },
            p.pole_kodu ? `po kodzie (${p.pole_kodu})` : "po położeniu", ` · tolerancja ${num(p.tolerancja_m)} m`)),
          h("td", null, obszarTxt(p.obszar)),
          h("td", null, p.status === "gotowe" ? "gotowe" : p.status === "blad" ? "błąd" : "w toku", p.status !== "gotowe" && p.postep ? h("div", { className: "muted" }, p.postep) : null),
          h("td", null, Object.keys(STANY).filter((s) => (p.statystyka || {})[s]).map((s) => `${STANY[s]} ${p.statystyka[s]}`).join(", ")),
          h("td", null, fmtDate(p.utworzono)))))),
      sel && h(Wyniki, { key: sel, id: sel, projekt: rodzaje && rodzaje.projekt, onError: setError, onDeleted: () => { setSel(null); load(); } }));
  }

  function Wyniki({ id, projekt, onError, onDeleted }) {
    const [data, setData] = useState(null);
    const [stan, setStan] = useState("");
    useEffect(() => {
      let timer;
      const tick = () => host.api(`/porownania/${id}${stan ? `?stan=${stan}` : ""}`).then((d) => {
        setData(d);
        if (d.porownanie.status === "w_toku") timer = setTimeout(tick, 3000);
      }).catch((e) => onError(e.message));
      tick();
      return () => clearTimeout(timer);
    }, [id, stan]);
    if (!data) return h("p", { className: "muted" }, "Wczytywanie…");
    const p = data.porownanie;
    const st = p.statystyka || {};
    const show = async (w) => {
      if (!projekt) { onError("Administrator nie wskazał projektu mapy (Wtyczki → PRG → Ustawienia)"); return; }
      try {
        const g = await host.api(`/porownania/${id}/mapa/${w.nr}`);
        host.showOnMap({ project: projekt, geometry: g.geometry, label: g.label, newTab: true });
      } catch (e) { onError(e.message); }
    };
    const del = async () => {
      if (!window.confirm(`Usunąć porównanie #${id} z wynikami?`)) return;
      try { await host.api(`/porownania/${id}`, { method: "DELETE" }); onDeleted(); } catch (e) { onError(e.message); }
    };
    return h("div", { className: "form", style: { marginTop: 14, maxWidth: 1200 } },
      h("h3", null, `Porównanie #${p.id}: ${RODZAJE[p.rodzaj]} — PRG × ${p.tabela}`),
      p.status === "blad" && h("div", { className: "error" }, st.blad || p.postep),
      p.status === "w_toku" && h("p", null, p.postep),
      p.status === "gotowe" && h("p", null, `Obiekty PRG ${st.obiekty_prg}, porównywane ${st.obiekty_porownywane} · `,
        Object.keys(STANY).map((s) => `${STANY[s]}: ${st[s] || 0}`).join(" · "), ` · obszary rozbieżności: ${st.rozbieznosci || 0}`),
      h("div", { className: "actions" },
        h("select", { value: stan, onChange: (e) => setStan(e.target.value) },
          h("option", { value: "" }, "wszystkie"),
          Object.entries(STANY).map(([k, v]) => h("option", { key: k, value: k }, v))),
        host.can("prg.import") && p.status !== "w_toku" && h("button", { onClick: del }, "Usuń porównanie")),
      h("table", { className: "grid", style: { marginTop: 8 } },
        h("thead", null, h("tr", null, ["Stan", "PRG", "Porównywany", "Pow. PRG (ha)", "Pow. porówn. (ha)", "Tylko w PRG (ha)",
          "Tylko w porówn. (ha)", "Najszersza rozb. (m)", ""].map((t, i) => h("th", { key: i }, t)))),
        h("tbody", null, data.wyniki.map((w) => h("tr", { key: w.nr },
          h("td", null, STANY[w.stan] || w.stan, w.roznice ? h("div", { className: "muted" }, `${w.roznice} obszarów`) : null),
          h("td", null, w.prg_kod || "—", h("div", { className: "muted" }, w.prg_nazwa || "")),
          h("td", null, w.por_kod || w.por_id || "—", h("div", { className: "muted" }, w.por_nazwa || "")),
          h("td", null, num(w.pow_prg_ha)), h("td", null, num(w.pow_por_ha)), h("td", null, num(w.tylko_prg_ha, 4)),
          h("td", null, num(w.tylko_por_ha, 4)), h("td", null, num(w.max_szer_m)),
          h("td", null, (w.prg_kod || w.roznice) ? h("button", { onClick: () => show(w), title: "Otwiera mapę w nowej karcie" }, "Na mapie ↗") : null))))),
      data.wyniki.length >= 5000 && h("p", { className: "muted" }, "Pokazano pierwsze 5000 wyników — zawęź filtr stanu."));
  }

  return { pages: { importy: Importy, rejestr: Rejestr, porownanie: Porownanie } };
}
