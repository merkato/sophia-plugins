// Wtyczka „Eksport danych” — okno „Pobierz” nad mapą, konfiguracja warstw i dziennik pobrań w administracji.
// Moduł ES bez budowania; React z Sophii (host.React).

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState, useCallback } = React;
  const dt = (d) => (d ? new Date(d).toLocaleString("pl-PL") : "—");
  const kb = (b) => (b == null ? "" : b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} kB`);

  // pobranie pliku (POST JSON → plik): nazwa z nagłówka, błąd z treści odpowiedzi
  async function pobierz(sciezka, body) {
    const res = await fetch(`/api/plugins/eksport${sciezka}`, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) {
      let msg = res.statusText;
      try { const b = await res.json(); msg = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail); } catch (e) { /* nie JSON */ }
      throw new Error(msg);
    }
    const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = m ? m[1] : "dane";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    return a.download;
  }

  const row = (label, el) => h("label", { style: { display: "flex", flexDirection: "column", gap: 3, marginBottom: 8 } },
    h("span", { className: "muted", style: { fontSize: 12 } }, label), el);

  // ------------------------------------------------------------------ okno „Pobierz”
  function OknoPobierz() {
    const [zrodla, setZrodla] = useState(null);
    const [tryb, setTryb] = useState("warstwy");
    const [wybrane, setWybrane] = useState(new Set());
    const [format, setFormat] = useState("xlsx");
    const [zakres, setZakres] = useState("caly");
    const [zest, setZest] = useState("");
    const [par, setPar] = useState({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const projekt = host.currentProject();
    const load = useCallback(() => {
      const p = host.currentProject();
      host.api(`/zrodla${p ? `?projekt=${encodeURIComponent(p)}` : ""}`).then((z) => {
        setZrodla(z);
        if (!z.warstwy.length && z.zestawienia.length) setTryb("zestawienia");
        if (z.zestawienia.length) setZest((v) => v || z.zestawienia[0].id);
      }).catch((e) => setError(e.message));
    }, []);
    useEffect(() => { load(); }, [load, projekt]);
    if (!zrodla) return h("p", { className: "muted" }, error || "Wczytywanie…");
    const z = zrodla.zestawienia.find((x) => x.id === zest);
    const przestrzenny = (f) => (zrodla.formaty.find((x) => x.id === f) || {}).przestrzenny;
    const formatyDla = tryb === "warstwy" ? zrodla.formaty : zrodla.formaty.filter((f) => !f.przestrzenny || (z && z.przestrzenne));
    const fmt = formatyDla.some((f) => f.id === format) ? format : (formatyDla[0] || {}).id;
    const toggle = (lid) => setWybrane((s) => { const n = new Set(s); if (n.has(lid)) n.delete(lid); else n.add(lid); return n; });
    const warstwyDoPobrania = zrodla.warstwy.filter((w) => wybrane.has(w.layer_id) && (fmt !== "mlas" || w.przestrzenna));
    const start = async () => {
      setBusy(true); setError(null);
      try {
        let plik;
        if (tryb === "warstwy") {
          const body = { projekt: zrodla.projekt, warstwy: warstwyDoPobrania.map((w) => w.layer_id), format: fmt, zakres };
          if (zakres === "widok") {
            const e = host.currentExtent();
            if (!e) throw new Error("Brak widoku mapy");
            body.bbox = e.bbox; body.crs = e.crs;
          }
          plik = await pobierz("/warstwy", body);
        } else {
          plik = await pobierz(`/zestawienia/${encodeURIComponent(zest)}`, { format: fmt, parametry: par });
        }
        host.notify(`Pobrano ${plik}`);
      } catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    const mozna = tryb === "warstwy" ? warstwyDoPobrania.length > 0 : !!z;
    return h("div", null,
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("div", { className: "inline", style: { gap: 6, marginBottom: 8 } },
        h("button", { className: tryb === "warstwy" ? "primary" : "", onClick: () => setTryb("warstwy") }, "Warstwy mapy"),
        h("button", { className: tryb === "zestawienia" ? "primary" : "", disabled: !zrodla.zestawienia.length, onClick: () => setTryb("zestawienia") }, "Zestawienia")),
      tryb === "warstwy" ? [
        !zrodla.projekt ? h("p", { key: "p", className: "muted" }, "Otwórz projekt mapy, aby pobrać jego warstwy.")
          : !zrodla.warstwy.length ? h("p", { key: "p", className: "muted" }, "W tym projekcie nie ma warstw udostępnionych do pobrania (Administracja → Eksport → Konfiguracja).")
          : h("div", { key: "w", style: { marginBottom: 8, maxHeight: 220, overflow: "auto" } },
              zrodla.warstwy.map((w) => h("label", { key: w.layer_id, style: { display: "block", opacity: fmt === "mlas" && !w.przestrzenna ? 0.5 : 1 } },
                h("input", { type: "checkbox", checked: wybrane.has(w.layer_id), disabled: fmt === "mlas" && !w.przestrzenna, onChange: () => toggle(w.layer_id) }),
                ` ${w.nazwa}`, w.przestrzenna ? null : h("span", { className: "muted" }, " (tabela)")))),
        h("div", { key: "z", style: { marginBottom: 8 } },
          h("label", { style: { marginRight: 12 } }, h("input", { type: "radio", checked: zakres === "caly", onChange: () => setZakres("caly") }), " cały zakres"),
          h("label", null, h("input", { type: "radio", checked: zakres === "widok", onChange: () => setZakres("widok") }), " bieżący widok mapy"))]
        : [
          row("Zestawienie", h("select", { key: "zs", value: zest, onChange: (e) => { setZest(e.target.value); setPar({}); } },
            zrodla.zestawienia.map((x) => h("option", { key: x.id, value: x.id }, x.tytul)))),
          ...(z ? z.parametry : []).map((p) => row(p.etykieta, h("select", { key: p.id, value: par[p.id] ?? (p.opcje[0] || {}).wartosc ?? "",
            onChange: (e) => setPar((v) => ({ ...v, [p.id]: e.target.value })) },
            p.opcje.map((o) => h("option", { key: o.wartosc, value: o.wartosc }, o.etykieta)))))],
      row("Format", h("select", { value: fmt, onChange: (e) => setFormat(e.target.value) },
        formatyDla.map((f) => h("option", { key: f.id, value: f.id }, f.tytul)))),
      fmt === "mlas" && h("p", { className: "muted", style: { fontSize: 12 } }, "SHP (mLas): tylko warstwy przestrzenne, SHP w EPSG:2180, w archiwum ZIP."),
      (fmt === "xlsx" || fmt === "csv") && h("p", { className: "muted", style: { fontSize: 12 } }, "XLSX i CSV: same atrybuty (bez geometrii), nagłówki i wartości słownikowe czytelne."),
      h("div", { className: "actions" },
        h("button", { className: "primary", disabled: busy || !mozna, onClick: start }, busy ? "Przygotowywanie pliku…" : "Pobierz")),
      h("p", { className: "muted", style: { fontSize: 12, marginTop: 6 } }, "Plik zawiera tylko dane, które możesz zobaczyć (Twój zakres danych). Pobranie jest odnotowywane."));
  }

  // ------------------------------------------------------------------ konfiguracja (administracja)
  function Konfiguracja() {
    const [projekty, setProjekty] = useState([]);
    const [slug, setSlug] = useState("");
    const [warstwy, setWarstwy] = useState(null);
    const [konf, setKonf] = useState({});        // layer_id -> Set(pól) (pusty zbiór = wszystkie pola)
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    useEffect(() => { host.request("/api/projects").then((p) => { setProjekty(p); if (p.length) setSlug(p[0].slug); }).catch((e) => setError(e.message)); }, []);
    useEffect(() => {
      if (!slug) return;
      setWarstwy(null);
      Promise.all([host.request(`/api/projects/${encodeURIComponent(slug)}`), host.api(`/konfiguracja/${encodeURIComponent(slug)}`)])
        .then(([proj, k]) => {
          setWarstwy(Object.values(proj.layers).filter((l) => l.source_table));
          const m = {}; k.warstwy.forEach((w) => { m[w.layer_id] = new Set(w.pola); }); setKonf(m);
        }).catch((e) => setError(e.message));
    }, [slug]);
    const wlacz = (lid) => setKonf((k) => { const n = { ...k }; if (n[lid]) delete n[lid]; else n[lid] = new Set(); return n; });
    const pole = (lid, f) => setKonf((k) => { const s = new Set(k[lid]); if (s.has(f)) s.delete(f); else s.add(f); return { ...k, [lid]: s }; });
    const zapisz = async () => {
      const body = { warstwy: (warstwy || []).filter((l) => konf[l.id]).map((l) => ({ layer_id: l.id, pola: [...konf[l.id]] })) };
      setBusy(true);
      try { await host.api(`/konfiguracja/${encodeURIComponent(slug)}`, { method: "PUT", body: JSON.stringify(body) }); host.notify(`Zapisano: warstwy do pobrania — ${body.warstwy.length}`); }
      catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    return h("div", null,
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("p", { className: "muted" }, "Zaznacz warstwy projektu, które użytkownicy z uprawnieniem „Pobieranie danych” mogą pobrać. Pola: bez zaznaczenia — ",
        "wszystkie widoczne pola warstwy. Zestawienia wtyczek (np. „Do lustracji”) konfiguruje sama wtyczka. Zakres danych użytkownika obowiązuje zawsze."),
      h("div", { className: "inline", style: { gap: 8, marginBottom: 8 } },
        h("select", { value: slug, onChange: (e) => setSlug(e.target.value) }, projekty.map((p) => h("option", { key: p.slug, value: p.slug }, p.title || p.slug))),
        h("button", { className: "primary", disabled: busy || !warstwy, onClick: zapisz }, busy ? "Zapisywanie…" : "Zapisz")),
      !warstwy ? h("p", { className: "muted" }, "Wczytywanie…") : h("table", { className: "grid" },
        h("thead", null, h("tr", null, ["Do pobrania", "Warstwa", "Pola (puste — wszystkie)"].map((t) => h("th", { key: t }, t)))),
        h("tbody", null, warstwy.map((l) => h("tr", { key: l.id },
          h("td", null, h("input", { type: "checkbox", checked: !!konf[l.id], onChange: () => wlacz(l.id) })),
          h("td", null, h("b", null, l.name), h("div", { className: "muted", style: { fontSize: 12 } }, `${l.source_table}${l.geometry_type ? "" : " · tabela"}`)),
          h("td", null, konf[l.id] ? l.fields.filter((f) => f.widget !== "Hidden").map((f) => h("label", { key: f.name, style: { marginRight: 10, whiteSpace: "nowrap" } },
            h("input", { type: "checkbox", checked: konf[l.id].has(f.name), onChange: () => pole(l.id, f.name) }), ` ${f.alias || f.name}`)) : null))))));
  }

  // ------------------------------------------------------------------ dziennik pobrań
  function Dziennik() {
    const [lista, setLista] = useState(null);
    const [error, setError] = useState(null);
    useEffect(() => { host.api("/dziennik").then(setLista).catch((e) => setError(e.message)); }, []);
    if (!lista) return h("p", { className: "muted" }, error || "Wczytywanie…");
    return h("table", { className: "grid" },
      h("thead", null, h("tr", null, ["Kiedy", "Kto", "Źródło", "Zawartość", "Format", "Zakres", "Wiersze", "Rozmiar"].map((t) => h("th", { key: t }, t)))),
      h("tbody", null, lista.map((d, i) => h("tr", { key: i },
        h("td", null, dt(d.kiedy)), h("td", null, d.login), h("td", null, d.zrodlo === "warstwy" ? `warstwy (${d.projekt})` : d.zrodlo),
        h("td", null, (d.elementy || []).join(", ")), h("td", null, d.format), h("td", null, d.zakres || ""),
        h("td", null, d.wiersze), h("td", null, kb(d.rozmiar))))));
  }

  return { pages: { konfiguracja: Konfiguracja, dziennik: Dziennik }, windows: { pobierz: OknoPobierz } };
}
