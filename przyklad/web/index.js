// Moduł WebUI wtyczki „Przykład” — zwykły moduł ES bez budowania. React przychodzi od Sophii (host.React),
// więc paczka nie zawiera własnej kopii. Kontrakt: export function register(host) → { pages: {...} }.

export function register(host) {
  const { React } = host;
  const h = React.createElement;
  const { useEffect, useState } = React;

  function Notatki() {
    const [list, setList] = useState([]);
    const [text, setText] = useState("");
    const [error, setError] = useState(null);
    const load = () => host.api("/notatki").then(setList).catch((e) => setError(e.message));
    useEffect(() => { load(); }, []);
    const add = async () => {
      try {
        await host.api("/notatki", { method: "POST", body: JSON.stringify({ tresc: text }) });
        setText("");
        host.notify("Dodano notatkę");
        load();
      } catch (e) { setError(e.message); }
    };
    return h("div", { className: "form", style: { maxWidth: 640 } },
      h("h3", null, "Notatki"),
      h("p", { className: "muted" }, "Wtyczka wzorcowa — pokazuje uprawnienia: czytanie i dodawanie są osobne."),
      error && h("div", { className: "error" }, error),
      host.can("przyklad.zapis") && h("div", { className: "inline" },
        h("input", { value: text, placeholder: "Treść notatki", onChange: (e) => setText(e.target.value) }),
        h("button", { className: "primary", disabled: !text.trim(), onClick: add }, "Dodaj")),
      h("ul", null, list.map((n) => h("li", { key: n.id },
        h("b", null, n.autor), " — ", new Date(n.utworzono).toLocaleString("pl-PL"), ": ", n.tresc))));
  }

  return { pages: { notatki: Notatki } };
}
