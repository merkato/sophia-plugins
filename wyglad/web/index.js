// Wtyczka „Wygląd” — strona w administracji: motyw, tryb, nazwa aplikacji, logo i edytor motywów własnych.
// Moduł ES bez budowania; React z hosta Sophii (docs/WTYCZKI.md).

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState, useCallback } = React;

  const TRYBY = [["auto", "Automatycznie (jak w systemie)"], ["jasny", "Zawsze jasny"], ["ciemny", "Zawsze ciemny"]];
  const odswiezWyglad = () => window.dispatchEvent(new Event("sophia-theme"));
  const hex6 = (v) => {
    const s = (v || "").trim();
    if (/^#[0-9a-f]{6}$/i.test(s)) return s.toLowerCase();
    if (/^#[0-9a-f]{3}$/i.test(s)) return "#" + s.slice(1).split("").map((c) => c + c).join("").toLowerCase();
    return null;
  };

  // podgląd na żywo: zmienne edytowanego trybu nakładane na całą aplikację (do zapisu albo anulowania)
  const PODGLAD = "wyglad-podglad";
  function pokazPodglad(zmienne, schemat, css) {
    let el = document.getElementById(PODGLAD);
    if (!el) { el = document.createElement("style"); el.id = PODGLAD; document.head.appendChild(el); }
    const linie = Object.entries(zmienne).filter(([, v]) => v).map(([k, v]) => `  --${k}: ${v};`).join("\n");
    el.textContent = `:root {\n${linie}\n  color-scheme: ${schemat};\n}\n${css || ""}`;
  }
  const ukryjPodglad = () => { const el = document.getElementById(PODGLAD); if (el) el.remove(); };

  // miniatura motywu: pasek górny, przycisk akcentu, panel na tle aplikacji
  function Miniatura({ z }) {
    const c = (k, d) => z[k] || d;
    return h("div", { "aria-hidden": true, style: { borderRadius: 8, overflow: "hidden", border: `1px solid ${c("line", "#ddd")}`, background: c("bg", "#f6f7f9") } },
      h("div", { style: { display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", background: c("topbar-bg", c("surface", "#fff")),
        borderBottom: `1px solid ${c("topbar-line", c("line", "#ddd"))}` } },
        h("span", { style: { width: 34, height: 8, borderRadius: 4, background: c("topbar-ink", c("ink", "#222")), opacity: 0.85 } }),
        h("span", { style: { flex: 1 } }),
        ["", ""].map((_, i) => h("span", { key: i, style: { width: 26, height: 12, borderRadius: 4, background: c("topbar-btn", c("surface", "#fff")),
          border: `1px solid ${c("topbar-btn-line", c("line", "#ddd"))}` } }))),
      h("div", { style: { display: "flex", gap: 8, padding: 8 } },
        h("div", { style: { flex: 1, background: c("surface", "#fff"), border: `1px solid ${c("line", "#ddd")}`, borderRadius: 6, padding: 6 } },
          h("div", { style: { height: 6, width: "70%", borderRadius: 3, background: c("ink", "#222"), opacity: 0.8, marginBottom: 5 } }),
          h("div", { style: { height: 5, width: "50%", borderRadius: 3, background: c("muted", "#888"), marginBottom: 7 } }),
          h("span", { style: { display: "inline-block", width: 40, height: 12, borderRadius: 4, background: c("accent", "#1f6feb") } }))));
  }

  function Pole({ etykieta, children, opis }) {
    return h("label", { style: { display: "block", margin: "8px 0" } },
      h("div", { style: { fontSize: 13, marginBottom: 3 } }, etykieta), children,
      opis ? h("div", { className: "muted", style: { marginTop: 2 } }, opis) : null);
  }

  // ------------------------------------------------------------------ edytor motywu własnego
  function Edytor({ motyw, zmienne, onZapisz, onUsun, onZamknij }) {
    const [szkic, setSzkic] = useState(() => ({ nazwa: motyw.nazwa, jasny: { ...motyw.jasny }, ciemny: { ...motyw.ciemny }, css: motyw.css || "" }));
    const [tryb, setTryb] = useState("jasny");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    useEffect(() => { pokazPodglad(szkic[tryb], tryb === "ciemny" ? "dark" : "light", szkic.css); }, [szkic, tryb]);
    useEffect(() => ukryjPodglad, []);
    const ustaw = (k, v) => setSzkic((s) => ({ ...s, [tryb]: { ...s[tryb], [k]: v } }));
    const kopiujZJasnego = () => setSzkic((s) => ({ ...s, ciemny: { ...s.jasny } }));
    const zapisz = async () => {
      setBusy(true); setError(null);
      try { await onZapisz(szkic); ukryjPodglad(); } catch (e) { setError(e.message); } finally { setBusy(false); }
    };
    const grupy = [...new Set(zmienne.map((z) => z.grupa))];
    const wartosci = szkic[tryb];
    return h("section", { style: { border: "1px solid var(--line)", borderRadius: 10, padding: 14, marginTop: 16, background: "var(--surface)" } },
      h("div", { style: { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" } },
        h("h3", { style: { margin: 0, flex: 1 } }, "Edycja motywu"),
        h("span", { className: "muted" }, "Podgląd na żywo — zmiany widać w całej aplikacji do zapisu albo anulowania.")),
      error && h("div", { className: "error" }, error),
      h(Pole, { etykieta: "Nazwa motywu" },
        h("input", { value: szkic.nazwa, maxLength: 60, style: { width: 320 }, onChange: (e) => setSzkic((s) => ({ ...s, nazwa: e.target.value })) })),
      h("div", { style: { display: "flex", gap: 6, margin: "10px 0", alignItems: "center", flexWrap: "wrap" } },
        [["jasny", "Tryb jasny"], ["ciemny", "Tryb ciemny"]].map(([k, t]) =>
          h("button", { key: k, className: tryb === k ? "active" : undefined, onClick: () => setTryb(k) }, t)),
        tryb === "ciemny" && h("button", { className: "link", onClick: kopiujZJasnego, style: { marginLeft: 8 } }, "skopiuj kolory z trybu jasnego"),
        tryb === "ciemny" && !Object.values(szkic.ciemny).some(Boolean) &&
          h("span", { className: "muted" }, "Brak kolorów trybu ciemnego — motyw będzie zawsze jasny.")),
      h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(330px, 1fr))", gap: "4px 24px" } },
        grupy.map((g) => h("div", { key: g },
          h("h4", { style: { margin: "8px 0 4px", fontSize: 12, textTransform: "uppercase", letterSpacing: ".05em", color: "var(--muted)" } }, g),
          zmienne.filter((z) => z.grupa === g).map((z) => {
            const v = wartosci[z.id] || "";
            return h("div", { key: z.id, style: { display: "flex", alignItems: "center", gap: 6, margin: "3px 0" } },
              h("input", { type: "color", value: hex6(v) || "#000000", title: hex6(v) ? "Wybierz kolor" : "Wartość spoza palety — popraw w polu tekstowym",
                style: { width: 36, height: 28, padding: 1, opacity: hex6(v) || !v ? 1 : 0.4 }, onChange: (e) => ustaw(z.id, e.target.value) }),
              h("input", { value: v, placeholder: "domyślny", style: { width: 120, fontFamily: "monospace", fontSize: 12 },
                onChange: (e) => ustaw(z.id, e.target.value) }),
              h("span", { style: { fontSize: 13 } }, z.etykieta));
          })))),
      h(Pole, { etykieta: "Dodatkowy CSS (opcjonalnie)", opis: "Reguły dołączane po kolorach, np. .brand-name { font-weight: 400; }. Bez @import i zasobów spoza Sophii." },
        h("textarea", { value: szkic.css, rows: 5, spellCheck: false, style: { width: "100%", fontFamily: "monospace", fontSize: 12 },
          onChange: (e) => setSzkic((s) => ({ ...s, css: e.target.value })) })),
      h("div", { style: { display: "flex", gap: 8, marginTop: 10 } },
        h("button", { className: "primary", disabled: busy || !szkic.nazwa.trim(), onClick: zapisz }, busy ? "Zapisywanie…" : "Zapisz motyw"),
        h("button", { disabled: busy, onClick: () => { ukryjPodglad(); onZamknij(); } }, "Anuluj"),
        h("span", { style: { flex: 1 } }),
        h("button", { className: "danger", disabled: busy, onClick: () => { if (confirm(`Usunąć motyw „${motyw.nazwa}”?`)) { ukryjPodglad(); onUsun(); } } }, "Usuń motyw")));
  }

  // ------------------------------------------------------------------ zakładka „Ogólne”
  function Ogolne() {
    const [dane, setDane] = useState(null);
    const [form, setForm] = useState(null);         // nazwa, pokaz_nazwe, logo_wysokosc, tryb
    const [edytuj, setEdytuj] = useState(null);     // id motywu własnego w edycji
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const przyjmij = useCallback((d, zmiana) => {
      setDane(d);
      const u = d.ustawienia;
      setForm({ nazwa: u.nazwa, pokaz_nazwe: u.pokaz_nazwe, logo_wysokosc: u.logo_wysokosc, tryb: u.tryb });
      if (zmiana) odswiezWyglad();
    }, []);
    useEffect(() => { host.api("/konfiguracja").then((d) => przyjmij(d, false)).catch((e) => setError(e.message)); }, []);
    const wykonaj = async (fn, komunikat) => {
      setBusy(true); setError(null);
      try { const d = await fn(); przyjmij(d, true); if (komunikat) host.notify(komunikat); return d; }
      catch (e) { setError(e.message); throw e; } finally { setBusy(false); }
    };
    const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const zapiszUstawienia = (zmiany, komunikat) => wykonaj(() => host.api("/ustawienia",
      json("PUT", { motyw: dane.ustawienia.motyw, ...form, ...zmiany })), komunikat).catch(() => {});
    const nowyMotyw = async (wzor) => {
      const nazwa = prompt("Nazwa nowego motywu:", `${wzor.nazwa.replace(/ \(domyślny\)$/, "")} — własny`);
      if (!nazwa || !nazwa.trim()) return;
      try { const d = await wykonaj(() => host.api("/motywy", json("POST", { nazwa: nazwa.trim(), z: wzor.id }))); setEdytuj(d.id); } catch (e) { /* błąd pokazany */ }
    };
    const wgrajLogo = async (plik) => {
      if (!plik) return;
      const fd = new FormData();
      fd.append("plik", plik);
      await wykonaj(() => host.api("/logo", { method: "POST", body: fd }), "Logo zapisane").catch(() => {});
    };

    if (error && !dane) return h("div", { className: "error" }, error);
    if (!dane || !form) return h("p", { className: "muted" }, "Wczytywanie…");
    const u = dane.ustawienia;
    const aktywny = dane.motywy.find((m) => m.id === u.motyw) || dane.motywy[0];
    const edytowany = edytuj ? dane.motywy.find((m) => m.id === edytuj && !m.wbudowany) : null;
    const zmienionoForm = form.nazwa !== u.nazwa || form.pokaz_nazwe !== u.pokaz_nazwe || form.logo_wysokosc !== u.logo_wysokosc;

    return h("div", null,
      error && h("div", { className: "error", onClick: () => setError(null) }, error),

      // --- motywy
      h("h3", null, "Motyw ogólny"),
      h("p", { className: "muted", style: { marginTop: -6 } }, "Obowiązuje wszędzie poza projektami z własnym motywem (zakładka „Projekty”)."),
      h("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 } },
        dane.motywy.map((m) => {
          const wybrany = m.id === u.motyw;
          return h("div", { key: m.id, style: { border: `2px solid ${wybrany ? "var(--accent)" : "var(--line)"}`, borderRadius: 10, padding: 10,
            background: "var(--surface)", display: "flex", flexDirection: "column", gap: 8 } },
            h("div", { style: { display: "grid", gridTemplateColumns: Object.keys(m.ciemny || {}).length ? "1fr 1fr" : "1fr", gap: 6 } },
              h(Miniatura, { z: m.jasny }), Object.keys(m.ciemny || {}).length ? h(Miniatura, { z: m.ciemny }) : null),
            h("div", null, h("strong", null, m.nazwa), wybrany ? h("span", { className: "muted" }, " — używany") : null,
              h("div", { className: "muted" }, m.opis || (m.wbudowany ? "" : `Własny${m.zmienil ? ` · zmienił ${m.zmienil}` : ""}`))),
            h("div", { style: { display: "flex", gap: 6, flexWrap: "wrap", marginTop: "auto" } },
              !wybrany && h("button", { className: "primary", disabled: busy, onClick: () => zapiszUstawienia({ motyw: m.id }, `Motyw: ${m.nazwa}`) }, "Użyj"),
              m.wbudowany
                ? h("button", { disabled: busy, onClick: () => nowyMotyw(m), title: "Nowy motyw własny z kolorami tego motywu" }, "Utwórz własny…")
                : h("button", { disabled: busy, className: edytuj === m.id ? "active" : undefined, onClick: () => setEdytuj(edytuj === m.id ? null : m.id) }, "Edytuj")));
        })),
      h(Pole, { etykieta: "Tryb jasny / ciemny" },
        h("select", { value: form.tryb, disabled: busy, onChange: (e) => { setForm({ ...form, tryb: e.target.value }); zapiszUstawienia({ tryb: e.target.value }); } },
          TRYBY.map(([k, t]) => h("option", { key: k, value: k }, t)))),

      edytowany && h(Edytor, { key: edytowany.id, motyw: edytowany, zmienne: dane.zmienne,
        onZapisz: (s) => wykonaj(() => host.api(`/motywy/${encodeURIComponent(edytowany.id)}`, json("PUT", s)), "Motyw zapisany").then(() => setEdytuj(null)),
        onUsun: () => wykonaj(() => host.api(`/motywy/${encodeURIComponent(edytowany.id)}`, { method: "DELETE" }), "Motyw usunięty").then(() => setEdytuj(null)).catch(() => {}),
        onZamknij: () => setEdytuj(null) }),

      // --- nazwa i logo
      h("h3", { style: { marginTop: 24 } }, "Nazwa aplikacji i logo"),
      h("div", { style: { display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" } },
        h("div", { style: { minWidth: 300, flex: 1 } },
          h(Pole, { etykieta: "Nazwa w górnym pasku i na karcie przeglądarki", opis: "Puste — „Sophia”." },
            h("input", { value: form.nazwa, maxLength: 80, placeholder: "Sophia", style: { width: "100%" }, onChange: (e) => setForm({ ...form, nazwa: e.target.value }) })),
          h("label", { style: { display: "flex", gap: 6, alignItems: "center", margin: "8px 0" } },
            h("input", { type: "checkbox", checked: form.pokaz_nazwe, onChange: (e) => setForm({ ...form, pokaz_nazwe: e.target.checked }) }),
            "Pokazuj nazwę aplikacji w górnym pasku"),
          !form.pokaz_nazwe && h("div", { className: "muted", style: { marginTop: -4 } },
            "W pasku tylko logo (bez logo — pusto). Wpisana nazwa zostaje tytułem karty przeglądarki; pusta — „Sophia”."),
          h(Pole, { etykieta: `Wysokość logo: ${form.logo_wysokosc} px` },
            h("input", { type: "range", min: 16, max: 64, step: 2, value: form.logo_wysokosc, style: { width: 240 },
              onChange: (e) => setForm({ ...form, logo_wysokosc: Number(e.target.value) }) })),
          h("button", { className: "primary", disabled: busy || !zmienionoForm, onClick: () => zapiszUstawienia({}, "Zapisano") }, "Zapisz")),
        h("div", { style: { minWidth: 300, flex: 1 } },
          h("div", { style: { fontSize: 13, marginBottom: 6 } }, "Logo (PNG albo WEBP, najwyżej 2 MB; najlepiej z przezroczystym tłem)"),
          // podgląd paska z bieżącym motywem
          h("div", { style: { display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", borderRadius: 8,
            background: (aktywny.jasny || {})["topbar-bg"] || "var(--surface)", color: (aktywny.jasny || {})["topbar-ink"] || "var(--ink)",
            border: "1px solid var(--line)", minHeight: 48 } },
            u.logo_url ? h("img", { src: u.logo_url, alt: "Logo", style: { height: form.logo_wysokosc, width: "auto", maxWidth: 240, objectFit: "contain" } })
              : h("span", { className: "muted", style: { color: "inherit", opacity: 0.7 } }, "brak logo"),
            form.pokaz_nazwe && h("strong", { style: { fontSize: 16 } }, form.nazwa || "Sophia")),
          h("div", { style: { display: "flex", gap: 8, marginTop: 8, alignItems: "center", flexWrap: "wrap" } },
            h("label", { className: "button", style: { opacity: busy ? 0.5 : 1 } }, u.logo_url ? "Zmień logo…" : "Wgraj logo…",
              h("input", { type: "file", accept: "image/png,image/webp", disabled: busy, style: { display: "none" },
                onChange: (e) => { wgrajLogo(e.target.files[0]); e.target.value = ""; } })),
            u.logo_url && h("button", { className: "danger", disabled: busy,
              onClick: () => { if (confirm("Usunąć logo?")) wykonaj(() => host.api("/logo", { method: "DELETE" }), "Logo usunięte").catch(() => {}); } }, "Usuń logo")),
          h("p", { className: "muted" }, "Z logo powstaje też ikona karty przeglądarki (64×64 px)."))),
      h("p", { className: "muted", style: { marginTop: 24 } },
        `Ostatnia zmiana: ${u.zmieniono ? new Date(u.zmieniono).toLocaleString("pl-PL") : "—"}${u.zmienil ? ` (${u.zmienil})` : ""}. ` +
        "Wygląd obowiązuje wszystkich użytkowników, także na ekranie logowania."));
  }

  // ------------------------------------------------------------------ zakładka „Projekty”: motyw dla projektu
  function Projekty() {
    const [lista, setLista] = useState(null);
    const [motywy, setMotywy] = useState([]);
    const [ogolny, setOgolny] = useState(null);
    const [busy, setBusy] = useState(null);       // slug zapisywanego projektu
    const [error, setError] = useState(null);
    useEffect(() => {
      Promise.all([host.api("/projekty"), host.api("/konfiguracja")])
        .then(([p, k]) => { setLista(p); setMotywy(k.motywy); setOgolny(k.motywy.find((m) => m.id === k.ustawienia.motyw) || null); })
        .catch((e) => setError(e.message));
    }, []);
    const przypisz = async (p, motyw) => {
      setBusy(p.slug); setError(null);
      try {
        setLista(await host.api(`/projekty/${encodeURIComponent(p.slug)}`, { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ motyw: motyw || null }) }));
        odswiezWyglad();
        host.notify(`${p.title}: ${motyw ? motywy.find((m) => m.id === motyw).nazwa : "motyw ogólny"}`);
      } catch (e) { setError(e.message); } finally { setBusy(null); }
    };
    if (error && !lista) return h("div", { className: "error" }, error);
    if (!lista) return h("p", { className: "muted" }, "Wczytywanie…");
    return h("div", null,
      h("p", { className: "muted" }, "Projekt z przypisanym motywem wygląda inaczej, gdy jest otwarty w mapie. Logo i nazwa aplikacji są wspólne."),
      error && h("div", { className: "error", onClick: () => setError(null) }, error),
      lista.length === 0 ? h("p", { className: "muted" }, "Brak projektów.") :
      h("table", { className: "grid", style: { borderCollapse: "collapse", minWidth: 520 } },
        h("thead", null, h("tr", null, ["Projekt", "Motyw", ""].map((t, i) => h("th", { key: i, style: { textAlign: "left", padding: "6px 10px", borderBottom: "1px solid var(--line)" } }, t)))),
        h("tbody", null, lista.map((p) => {
          const m = p.motyw ? motywy.find((x) => x.id === p.motyw) : null;
          return h("tr", { key: p.slug },
            h("td", { style: { padding: "6px 10px", borderBottom: "1px solid var(--line)" } }, h("strong", null, p.title), h("div", { className: "muted" }, p.slug)),
            h("td", { style: { padding: "6px 10px", borderBottom: "1px solid var(--line)" } },
              h("select", { value: p.motyw || "", disabled: busy === p.slug, onChange: (e) => przypisz(p, e.target.value), style: { minWidth: 240 } },
                h("option", { value: "" }, `— ogólny${ogolny ? ` (${ogolny.nazwa})` : ""} —`),
                motywy.map((x) => h("option", { key: x.id, value: x.id }, x.nazwa)))),
            h("td", { style: { padding: "6px 10px", borderBottom: "1px solid var(--line)", width: 120 } },
              h(Miniatura, { z: (m || ogolny || { jasny: {} }).jasny })));
        }))));
  }

  // ------------------------------------------------------------------ strona „Wygląd”
  function Wyglad() {
    const [zakladka, setZakladka] = useState("ogolne");
    return h("div", { style: { maxWidth: 1100 } },
      h("h2", { style: { marginTop: 0 } }, "Wygląd"),
      h("div", { style: { display: "flex", gap: 6, marginBottom: 12 } },
        [["ogolne", "Ogólne"], ["projekty", "Projekty"]].map(([k, t]) =>
          h("button", { key: k, className: zakladka === k ? "active" : undefined, onClick: () => setZakladka(k) }, t))),
      zakladka === "ogolne" ? h(Ogolne) : h(Projekty));
  }

  return { pages: { wyglad: Wyglad } };
}
