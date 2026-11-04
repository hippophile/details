// Renders the Record | Library | Analyse | Prompts bar into <nav id="nav">, highlighting the current page.
(function () {
  const pages = [["Record", "/record"], ["Library", "/library"], ["Analyse", "/analyse"], ["Prompts", "/prompts"]];
  const host = document.getElementById("nav");
  if (!host) return;
  const here = location.pathname.replace(/\/+$/, "");
  for (const [label, href] of pages) {
    const a = document.createElement("a");
    a.href = href;
    a.textContent = label;
    if (here === href) { a.className = "current"; a.setAttribute("aria-current", "page"); }
    host.appendChild(a);
  }
})();
