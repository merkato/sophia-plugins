// Wtyczka „Wyszukiwarka” — okno „Szukaj” nad mapą (bieżący projekt) i strona konfiguracji w panelu administracji.
// Moduł ES bez budowania; React z Sophii (host.React).

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState, useCallback, useRef } = React;

  // ------------------------------------------------------------------ okno „Szukaj”
  function OknoSzukaj({ props }) {
    const [q, setQ] = useState(props.q || "");
    const [wynik, setWynik] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const [projekt, setProjekt] = useState(host.currentProject());
    const nr = useRef(0);
    const input = useRef(null);
    useEffect(() => { if (input.current) input.current.focus(); }, []);
    // ponowne wyszukanie z pola w pasku narzędzi (okno już otwarte)
    useEffect(() => { if (typeof props.q === "string") setQ(props.q); }, [props.q, props.n]);
    // projekt mapy mógł się zmienić od otwarcia okna
    useEffect(() => { const t = setInterval(() => setProjekt(host.currentProject()), 1000); return () => clearInterval(t); }, []);
    const szukaj = useCallback((tekst) => {
      const p = host.currentProject();
      setProjekt(p);
      if (!p) { setError("Najpierw otwórz projekt mapy"); return; }
      if (tekst.trim().length < 2) { setWynik(null); return; }
      const moje = ++nr.current;
      setBusy(true); setError(null);
      host.api(`/szukaj?${new URLSearchParams({ projekt: p, q: tekst.trim() })}`)
        .then((r) => { if (moje === nr.current) setWynik(r); })
        .catch((e) => { if (moje === nr.current) setError(e.message); })
        .finally(() => { if (moje === nr.current) setBusy(false); });
    }, []);
    // wyszukiwanie w trakcie pisania (po krótkiej przerwie)
    useEffect(() => { const t = setTimeout(() => szukaj(q), 400); return () => clearTimeout(t); }, [q]);
    const pokaz = (w, o) => host.showOnMap({ project: wynik.projekt, targets: [{ table: w.table, pk: o.pk }],
      geometry: o.geometry, label: `${w.warstwa}: ${o.etykieta}` });
    const pierwszy = () => { const w = wynik && wynik.wyniki.find((x) => x.obiekty && x.obiekty.length); if (w) pokaz(w, w.obiekty[0]); };
    const razem = wynik ? wynik.wyniki.reduce((n, w) => n + (w.obiekty ? w.obiekty.length : 0), 0) : 0;
    return h("div", { style: { display: "flex", flexDirection: "column", height: "100%" } },
      h("input", { ref: input, value: q, placeholder: "Szukaj w projekcie, np. miejscowość, działka, decyzja…",
        style: { width: "100%", boxSizing: "border-box", marginBottom: 6 },
        onChange: (e) => setQ(e.target.value), onKeyDown: (e) => { if (e.key === "Enter") pierwszy(); } }),
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      !projekt && h("p", { className: "muted" }, "Otwórz projekt mapy, aby wyszukiwać."),
      busy && h("div", { className: "muted" }, "Szukanie…"),
      wynik && !busy && (!wynik.skonfigurowano
        ? h("p", { className: "muted" }, "Wyszukiwarka nie jest skonfigurowana dla tego projektu (Administracja → Wyszukiwarka → Konfiguracja).")
        : razem === 0 && !wynik.wyniki.some((w) => w.blad) ? h("p", { className: "muted" }, "Nic nie znaleziono.") : null),
      wynik && h("div", { style: { flex: 1, overflow: "auto" } },
        wynik.wyniki.map((w) => h("div", { key: w.layer_id, style: { marginBottom: 8 } },
          h("div", { style: { fontWeight: 600, fontSize: 13, margin: "2px 0" } }, w.warstwa,
            w.obiekty ? h("span", { className: "muted", style: { fontWeight: 400 } }, ` (${w.obiekty.length}${w.wiecej ? "+" : ""})`) : null),
          w.blad ? h("div", { className: "muted" }, w.blad) : h("ul", { style: { listStyle: "none", margin: 0, padding: 0 } },
            w.obiekty.map((o) => h("li", { key: o.pk },
              h("button", { className: "link", style: { textAlign: "left", padding: "2px 0", width: "100%" }, title: "Pokaż na mapie i otwórz formularz",
                onClick: () => pokaz(w, o) }, o.etykieta || o.pk)))),
          w.wiecej && h("div", { className: "muted", style: { fontSize: 12 } }, "Pokazano pierwsze wyniki — doprecyzuj zapytanie.")))));
  }

  // ------------------------------------------------------------------ konfiguracja (administracja)
  function Konfiguracja() {
    const [projekty, setProjekty] = useState([]);
    const [slug, setSlug] = useState("");
    const [warstwy, setWarstwy] = useState(null);     // warstwy projektu (z konfiguracji projektu Sophii)
    const [konf, setKonf] = useState({});             // layer_id -> { pola: Set, etykieta: Set }
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    useEffect(() => { host.request("/api/projects").then((p) => { setProjekty(p); if (p.length) setSlug(p[0].slug); }).catch((e) => setError(e.message)); }, []);
    useEffect(() => {
      if (!slug) return;
      setWarstwy(null);
      Promise.all([host.request(`/api/projects/${encodeURIComponent(slug)}`), host.api(`/konfiguracja/${encodeURIComponent(slug)}`)])
        .then(([proj, k]) => {
          setWarstwy(Object.values(proj.layers).filter((l) => l.source_table && l.primary_key));
          const m = {};
          k.warstwy.forEach((w) => { m[w.layer_id] = { pola: new Set(w.pola), etykieta: new Set(w.etykieta) }; });
          setKonf(m);
        }).catch((e) => setError(e.message));
    }, [slug]);
    const przelacz = (lid, rodzaj, pole) => setKonf((k) => {
      const w = k[lid] || { pola: new Set(), etykieta: new Set() };
      const s = new Set(w[rodzaj]);
      if (s.has(pole)) s.delete(pole); else s.add(pole);
      return { ...k, [lid]: { ...w, [rodzaj]: s } };
    });
    const zapisz = async () => {
      const body = { warstwy: (warstwy || []).filter((l) => konf[l.id] && konf[l.id].pola.size)
        .map((l) => ({ layer_id: l.id, pola: [...konf[l.id].pola], etykieta: [...konf[l.id].etykieta] })) };
      setBusy(true);
      try {
        await host.api(`/konfiguracja/${encodeURIComponent(slug)}`, { method: "PUT", body: JSON.stringify(body) });
        host.notify(`Zapisano: warstwy w wyszukiwarce — ${body.warstwy.length}`);
      } catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    const chip = (lid, rodzaj, f) => {
      const on = konf[lid] && konf[lid][rodzaj].has(f.name);
      return h("label", { key: `${rodzaj}:${f.name}`, style: { display: "inline-flex", gap: 3, marginRight: 10, whiteSpace: "nowrap" } },
        h("input", { type: "checkbox", checked: !!on, onChange: () => przelacz(lid, rodzaj, f.name) }), f.alias || f.name);
    };
    return h("div", null,
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      h("p", { className: "muted" }, "Wskaż warstwy i pola, po których okno „Szukaj” przeszukuje projekt (fragment tekstu, bez wielkości liter; ",
        "kilka słów — każde musi pasować). „Opis wyniku” — pola pokazywane na liście (puste: pola przeszukiwane). ",
        "Użytkownik znajdzie tylko obiekty, które może zobaczyć: prawa warstwy, filtr warstwy i zakres danych z projektu."),
      h("div", { className: "inline", style: { gap: 8, marginBottom: 8 } },
        h("select", { value: slug, onChange: (e) => setSlug(e.target.value) }, projekty.map((p) => h("option", { key: p.slug, value: p.slug }, p.title || p.slug))),
        h("button", { className: "primary", disabled: busy || !warstwy, onClick: zapisz }, busy ? "Zapisywanie…" : "Zapisz")),
      !warstwy ? h("p", { className: "muted" }, "Wczytywanie…") : warstwy.length === 0 ? h("p", null, "Projekt nie ma warstw PostGIS z kluczem głównym.")
        : h("table", { className: "grid" },
          h("thead", null, h("tr", null, ["Warstwa", "Szukaj w polach", "Opis wyniku"].map((t) => h("th", { key: t }, t)))),
          h("tbody", null, warstwy.map((l) => h("tr", { key: l.id },
            h("td", null, h("b", null, l.name), h("div", { className: "muted", style: { fontSize: 12 } }, l.source_table)),
            h("td", null, l.fields.filter((f) => f.widget !== "Hidden").map((f) => chip(l.id, "pola", f))),
            h("td", null, l.fields.filter((f) => f.widget !== "Hidden").map((f) => chip(l.id, "etykieta", f))))))));
  }

  // ------------------------------------------------------------------ pole „Szukaj” w pasku narzędzi
  const IkonaLupy = () => h("svg", { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    strokeWidth: 2.2, strokeLinecap: "round", "aria-hidden": true },
    h("circle", { cx: 10.5, cy: 10.5, r: 6.5 }), h("path", { d: "M15.5 15.5 21 21" }));

  function PoleSzukaj() {
    const [q, setQ] = useState("");
    if (!host.can("wyszukiwarka.szukaj")) return null;
    const szukaj = () => host.openWindow("szukaj", { props: { q: q.trim(), n: Date.now() } });
    return h("form", { className: "toolbar-field", role: "search", onSubmit: (e) => { e.preventDefault(); szukaj(); } },
      h("label", { htmlFor: "wyszukiwarka-pole" }, "Szukaj"),
      h("input", { id: "wyszukiwarka-pole", type: "search", value: q, placeholder: "np. miejscowość, działka, decyzja…",
        onChange: (e) => setQ(e.target.value) }),
      h("button", { type: "submit", title: "Szukaj w projekcie", "aria-label": "Szukaj" }, h(IkonaLupy)));
  }

  return { pages: { konfiguracja: Konfiguracja }, windows: { szukaj: OknoSzukaj }, toolbar: PoleSzukaj };
}
