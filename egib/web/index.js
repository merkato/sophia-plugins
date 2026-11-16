// Wtyczka EGiB — strony w panelu administracji Sophii („EGiB — importy”, „EGiB — rejestr”).
// Zwykły moduł ES bez budowania; React od Sophii (host.React).

const RODZAJE = { jednostka: "Jednostki", obreb: "Obręby", dzialka: "Działki", uzytek: "Użytki", punkt: "Punkty graniczne" };
const AKCJE = { nowy: "nowe", zmieniony: "zmienione", bez_zmian: "bez zmian", koniec: "zamknięte (koniec obiektu)",
  brak_w_zakresie: "zamknięte (brak w zakresie)", pomin: "pominięte" };
const STATUS = { wysylanie: "wysyłanie", wczytywanie: "wczytywanie", roboczy: "do zatwierdzenia", scalanie: "scalanie",
  zatwierdzony: "zatwierdzony", odrzucony: "odrzucony", blad: "błąd" };

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState, useCallback } = React;
  const fmtDate = (d) => (d ? new Date(d).toLocaleString("pl-PL") : "—");
  const mb = (b) => `${(b / 1048576).toFixed(1)} MB`;

  // ------------------------------------------------------------------ importy
  function Importy() {
    const [list, setList] = useState([]);
    const [sel, setSel] = useState(null);
    const [error, setError] = useState(null);
    const load = useCallback(() => host.api("/importy").then(setList).catch((e) => setError(e.message)), []);
    useEffect(() => { load(); }, [load]);
    return h("div", { className: "egib" },
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h(NowyImport, { onDone: (id) => { load(); setSel(id); }, onError: setError }),
      h("table", { className: "grid", style: { marginTop: 12 } },
        h("thead", null, h("tr", null, ["#", "Plik", "Organ / system", "Status", "Utworzył", "Utworzono"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, list.map((i) => h("tr", { key: i.id, onClick: () => setSel(i.id), style: { cursor: "pointer" },
          className: sel === i.id ? "selected" : "" },
          h("td", null, i.id), h("td", null, i.plik, h("div", { className: "muted" }, mb(i.rozmiar))),
          h("td", null, i.organ || "—", h("div", { className: "muted" }, i.system_zrodlowy || "")),
          h("td", null, STATUS[i.status] || i.status, i.postep && i.status !== "zatwierdzony" ? h("div", { className: "muted" }, i.postep) : null),
          h("td", null, i.utworzyl_nazwa || "—"), h("td", null, fmtDate(i.utworzono)))))),
      sel && h(Szczegoly, { key: sel, id: sel, onChanged: load, onError: setError }));
  }

  function NowyImport({ onDone, onError }) {
    const [file, setFile] = useState(null);
    const [klauzula, setKlauzula] = useState(null);
    const [zakres, setZakres] = useState("obreb");
    const [punkty, setPunkty] = useState(false);
    const [busy, setBusy] = useState(null);
    const send = async () => {
      try {
        const warstwy = ["jednostka", "obreb", "dzialka", "uzytek", ...(punkty ? ["punkt"] : [])];
        const { id, chunk } = await host.api("/importy", { method: "POST",
          body: JSON.stringify({ plik: file.name, rozmiar: file.size, zakres_typ: zakres, warstwy }) });
        for (let off = 0; off < file.size; off += chunk) {
          setBusy(`Wysyłanie ${Math.round((off / file.size) * 100)}%`);
          await host.api(`/importy/${id}/plik?offset=${off}`, { method: "PUT", body: file.slice(off, off + chunk),
            headers: { "Content-Type": "application/octet-stream" } });
        }
        if (klauzula) {
          const fd = new FormData();
          fd.append("plik", klauzula);
          await host.api(`/importy/${id}/klauzula`, { method: "POST", body: fd });
        }
        setBusy("Uruchamianie wczytywania…");
        await host.api(`/importy/${id}/wczytaj`, { method: "POST" });
        setFile(null); setKlauzula(null);
        host.notify(`Import #${id}: plik przesłany, trwa wczytywanie`);
        onDone(id);
      } catch (e) { onError(e.message); } finally { setBusy(null); }
    };
    return h("div", { className: "form", style: { maxWidth: 760 } },
      h("h3", null, "Nowy import GML"),
      h("p", { className: "muted" }, "Plik EGiB od starostwa (GML wg rozporządzenia 2021). Klauzula (*.klauzula.xml) — opcjonalnie: ",
        "Sophia sprawdzi sumę kontrolną i zapisze organ oraz datę kopii."),
      h("label", { className: "field" }, h("span", null, "Plik GML"),
        h("input", { type: "file", accept: ".gml,.xml", onChange: (e) => setFile(e.target.files[0] || null) })),
      h("label", { className: "field" }, h("span", null, "Klauzula"),
        h("input", { type: "file", accept: ".xml", onChange: (e) => setKlauzula(e.target.files[0] || null) })),
      h("label", { className: "field" }, h("span", null, "Zakres"),
        h("select", { value: zakres, onChange: (e) => setZakres(e.target.value) },
          h("option", { value: "obreb" }, "pełne obręby — obiekty, których brak w pliku, zostaną zamknięte"),
          h("option", { value: "lista_dzialek" }, "lista działek z wniosku — nic nie jest zamykane automatycznie"))),
      h("label", null, h("input", { type: "checkbox", checked: punkty, onChange: (e) => setPunkty(e.target.checked) }),
        " importuj punkty graniczne"),
      h("div", { className: "actions" },
        h("button", { className: "primary", disabled: !file || !!busy, onClick: send }, busy || "Wyślij i wczytaj"),
        file && h("span", { className: "muted" }, ` ${file.name}, ${mb(file.size)}`)));
  }

  function Szczegoly({ id, onChanged, onError }) {
    const [imp, setImp] = useState(null);
    const [obreby, setObreby] = useState("");
    const [dzialki, setDzialki] = useState("");
    const load = useCallback(() => host.api(`/importy/${id}`).then((i) => {
      setImp(i);
      setObreby((i.zakres_obreby || []).join("\n"));
      setDzialki((i.zakres_dzialki || []).join("\n"));
      return i;
    }), [id]);
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
    const saveScope = async () => {
      try {
        const lines = (t) => t.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
        await host.api(`/importy/${id}/zakres`, { method: "PUT", body: JSON.stringify({
          zakres_typ: imp.zakres_typ, obreby: lines(obreby), dzialki: lines(dzialki) }) });
        await load();
        host.notify("Przeliczono podgląd dla nowego zakresu");
      } catch (e) { onError(e.message); }
    };
    const st = imp.statystyka || {};
    const r = imp.raport || {};
    return h("div", { className: "form", style: { marginTop: 14, maxWidth: 960 } },
      h("h3", null, `Import #${imp.id}: ${imp.plik}`),
      h("p", null, h("b", null, STATUS[imp.status] || imp.status), imp.postep ? ` — ${imp.postep}` : ""),
      h("p", { className: "muted" },
        [imp.klauzula && imp.klauzula.organProwadzacyZasob, imp.klauzula && `kopia z ${imp.klauzula.dataWykonaniaKopii}`,
          imp.system_zrodlowy, imp.schemat, imp.uklad_zrodlowy, imp.sha256 && `SHA-256 ${imp.sha256.slice(0, 16)}…`]
          .filter(Boolean).join(" · ")),
      r.blad && h("div", { className: "error" }, r.blad),
      (r.ostrzezenia || []).map((w, i) => h("div", { key: i, className: "notice" }, w)),
      r.obiekty_w_pliku && h("p", { className: "muted" }, "W pliku: ",
        Object.entries(r.obiekty_w_pliku).map(([k, v]) => `${RODZAJE[k] || k} ${v}`).join(", ")),
      Object.keys(RODZAJE).some((k) => st[k]) && h("table", { className: "grid" },
        h("thead", null, h("tr", null, h("th", null, ""), Object.values(AKCJE).map((a) => h("th", { key: a }, a)))),
        h("tbody", null, Object.entries(RODZAJE).filter(([k]) => st[k]).map(([k, label]) => h("tr", { key: k },
          h("td", null, label), Object.keys(AKCJE).map((a) => h("td", { key: a }, st[k][a] || 0)))))),
      st.zmiany_nowe != null && h("p", { className: "muted" }, `Nowe zmiany EGiB: ${st.zmiany_nowe}`),
      st.relacje_dzialka_uzytek_przeliczone != null && h("p", { className: "muted" },
        `Przeliczone relacje działka–użytek dla ${st.relacje_dzialka_uzytek_przeliczone} działek`),
      st.brak_w_pliku && st.brak_w_pliku.length > 0 && h("div", { className: "notice" },
        `Działki z wniosku, których nie ma w pliku (${st.brak_w_pliku.length}) — sprawdź, czy podzielone/zniesione: `,
        st.brak_w_pliku.join(", ")),
      imp.status === "roboczy" && h("div", null,
        h("h4", null, imp.zakres_typ === "obreb" ? "Zakres: pełne obręby" : "Zakres: lista działek z wniosku"),
        imp.zakres_typ === "obreb"
          ? h("textarea", { rows: 5, style: { width: "100%" }, value: obreby, onChange: (e) => setObreby(e.target.value) })
          : h("textarea", { rows: 5, style: { width: "100%" }, value: dzialki, onChange: (e) => setDzialki(e.target.value) }),
        h("p", { className: "muted" }, imp.zakres_typ === "obreb"
          ? "Obiekty rejestru z tych obrębów, których nie ma w pliku, zostaną zamknięte. Domyślnie: obręby działek z pliku."
          : "Działki z wniosku: brakujące w pliku trafiają tylko do raportu."),
        h("div", { className: "actions" },
          h("button", { onClick: saveScope }, "Przelicz podgląd dla zakresu"),
          h("button", { className: "primary", onClick: () => act("zatwierdz", "Zatwierdzić import i scalić z rejestrem EGiB?") }, "Zatwierdź"),
          h("button", { onClick: () => act("odrzuc", "Odrzucić import? Plik i dane robocze zostaną usunięte.") }, "Odrzuć"))),
      ["wysylanie", "blad"].includes(imp.status) && h("div", { className: "actions" },
        h("button", { onClick: () => act("odrzuc") }, "Odrzuć")));
  }

  // ------------------------------------------------------------------ rejestr
  function Rejestr() {
    const [q, setQ] = useState("");
    const [list, setList] = useState([]);
    const [det, setDet] = useState(null);
    const [error, setError] = useState(null);
    const search = () => host.api(`/dzialki?q=${encodeURIComponent(q.trim())}`).then(setList).catch((e) => setError(e.message));
    const open = (k) => host.api(`/dzialki/${encodeURIComponent(k)}`).then(setDet).catch((e) => setError(e.message));
    return h("div", { className: "egib" },
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("div", { className: "inline", style: { maxWidth: 560 } },
        h("input", { value: q, placeholder: "Identyfikator działki, np. 143801_1.0008.8189/2 albo jego początek",
          onChange: (e) => setQ(e.target.value), onKeyDown: (e) => e.key === "Enter" && q.trim().length >= 3 && search() }),
        h("button", { className: "primary", disabled: q.trim().length < 3, onClick: search }, "Szukaj")),
      list.length > 0 && h("table", { className: "grid", style: { marginTop: 10, maxWidth: 760 } },
        h("thead", null, h("tr", null, ["Działka", "Pole ewid. (ha)", "Pow. geom. (ha)", "Wersja od"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, list.map((d) => h("tr", { key: d.klucz, onClick: () => open(d.klucz), style: { cursor: "pointer" } },
          h("td", null, d.id_dzialki), h("td", null, d.pole_ewid_ha), h("td", null, (d.pow_geom_m2 / 10000).toFixed(4)),
          h("td", null, fmtDate(d.wazna_od)))))),
      det && h(Dzialka, { det }));
  }

  function Dzialka({ det }) {
    const b = det.biezaca;
    return h("div", { className: "form", style: { marginTop: 14, maxWidth: 960 } },
      b ? h("div", null,
        h("h3", null, `Działka ${b.id_dzialki}`),
        h("p", null, `Obręb ${b.obreb || b.id_obrebu}, nr ${b.numer} · pole ewidencyjne ${b.pole_ewid_ha} ha · geometryczne ${b.pow_geom_ha} ha · punkty graniczne: ${b.punkty}`),
        b.dodatkowe_info && h("p", { className: "muted" }, b.dodatkowe_info),
        h("p", { className: "muted" }, `Wersja ${b.wersja_id || "—"} (od ${fmtDate(b.wersja_od)}), w rejestrze od ${fmtDate(b.wazna_od)}, import #${b.import_id}`,
          b.nr_zmiany ? ` · zmiana ${b.nr_zmiany}: ${b.zmiana_opis || ""}` : ""),
        h("h4", null, `Użytki (udział Ls: ${(Number(b.udzial_ls) * 100).toFixed(1)}%)`),
        h("table", { className: "grid" }, h("thead", null, h("tr", null, ["OFU", "Pow. (m²)", "Udział w działce"].map((t) => h("th", { key: t }, t)))),
          h("tbody", null, (b.uzytki || []).map((u) => h("tr", { key: u.uzytek }, h("td", null, u.ofu), h("td", null, u.pow_m2),
            h("td", null, `${(u.udzial * 100).toFixed(1)}%`)))))) : h("h3", null, "Działka zamknięta (tylko historia)"),
      h("h4", null, `Historia (${det.historia.length})`),
      det.historia.length ? h("table", { className: "grid" },
        h("thead", null, h("tr", null, ["Ważna od", "do", "Powód", "Pole ewid.", "Pow. geom.", "Użytki", "Zmiana"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, det.historia.map((v, i) => h("tr", { key: i },
          h("td", null, fmtDate(v.wazna_od)), h("td", null, fmtDate(v.wazna_do)), h("td", null, v.zamkniecie),
          h("td", null, v.pole_ewid_ha), h("td", null, v.pow_geom_ha),
          h("td", null, (v.uzytki || []).map((u) => u.ofu).join(", ")), h("td", null, v.nr_zmiany ? `${v.nr_zmiany} ${v.zmiana_opis || ""}` : "—")))))
        : h("p", { className: "muted" }, "Brak wcześniejszych wersji."));
  }

  return { pages: { importy: Importy, rejestr: Rejestr } };
}
