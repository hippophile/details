// Renders the top bar: the detAIls wordmark plus the Record | Library | Analyse | Prompts nav,
// with a sliding pill under the current page. Pages only need <nav id="nav"> in the body.
(function () {
  const pages = [["Record", "/record"], ["Library", "/library"], ["Analyse", "/analyse"], ["Prompts", "/prompts"]];
  const host = document.getElementById("nav");
  if (!host) return;
  const here = location.pathname.replace(/\/+$/, "");

  // Wrap the nav in a sticky top bar with the wordmark.
  const bar = document.createElement("header");
  bar.className = "topbar";
  const inner = document.createElement("div");
  inner.className = "topbar-inner";
  const brand = document.createElement("a");
  brand.className = "brand";
  brand.href = "/record";
  brand.setAttribute("aria-label", "detAIls home");
  brand.innerHTML =
    '<svg class="mark" viewBox="0 0 26 26" aria-hidden="true"><defs><linearGradient id="dg" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="26" y2="26">' +
    '<stop offset="0" stop-color="#7c83ff"/><stop offset="1" stop-color="#22d3ee"/></linearGradient></defs>' +
    '<rect x="1" y="1" width="24" height="24" rx="8" fill="url(#dg)" opacity=".18"/>' +
    '<g stroke="url(#dg)" stroke-width="2.4" stroke-linecap="round"><line x1="7" y1="11" x2="7" y2="15"/>' +
    '<line x1="11" y1="8" x2="11" y2="18"/><line x1="15" y1="6" x2="15" y2="20"/><line x1="19" y1="10" x2="19" y2="16"/></g></svg>' +
    '<span>det<span class="ai">AI</span>ls</span><span class="badge">on-device</span>';
  host.parentNode.insertBefore(bar, host);
  bar.appendChild(inner);
  inner.appendChild(brand);
  inner.appendChild(host);

  for (const [label, href] of pages) {
    const a = document.createElement("a");
    a.href = href;
    a.textContent = label;
    if (here === href) { a.className = "current"; a.setAttribute("aria-current", "page"); }
    host.appendChild(a);
  }

  // Pill under the current link; it glides in from the left edge on load.
  const pill = document.createElement("span");
  pill.className = "pill";
  pill.setAttribute("aria-hidden", "true");
  host.prepend(pill);
  const current = host.querySelector("a.current");
  const place = () => {
    if (!current) { pill.style.display = "none"; return; }
    pill.style.left = current.offsetLeft + "px";
    pill.style.width = current.offsetWidth + "px";
  };
  pill.style.left = "4px"; pill.style.width = "0px";
  requestAnimationFrame(() => requestAnimationFrame(place));
  window.addEventListener("resize", place);
})();
