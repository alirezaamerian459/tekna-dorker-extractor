const CATEGORIES = {
  pages: { label: "Indexed pages", make: (domain) => `site:${domain}` },
  documents: { label: "Public documents", make: (domain) => `site:${domain} (filetype:pdf OR filetype:docx OR filetype:pptx)` },
  support: { label: "Help & support", make: (domain) => `site:${domain} (help OR support OR documentation)` },
  updates: { label: "News & updates", make: (domain) => `site:${domain} (blog OR news OR releases OR changelog)` },
  subdomains: { label: "Subdomains", make: (domain) => `site:*.${domain}` },
};

const domainInput = document.querySelector("#domain-input");
const domainError = document.querySelector("#domain-error");
const queryList = document.querySelector("#query-list");
const queryEmpty = document.querySelector("#query-empty");
const queryCount = document.querySelector("#query-count");
const copyAllButton = document.querySelector("#copy-all");
const toast = document.querySelector("#toast");
const sourceInput = document.querySelector("#source-input");
const filterDomain = document.querySelector("#filter-domain");
const urlList = document.querySelector("#url-list");
const urlEmpty = document.querySelector("#url-empty");
const urlCount = document.querySelector("#url-count");
const copyUrlsButton = document.querySelector("#copy-urls");
const downloadUrlsButton = document.querySelector("#download-urls");

let activeQueries = [];
let extractedUrls = [];
let toastTimeout;

function normalizeDomain(value) {
  const candidate = value.trim().toLowerCase().replace(/^https?:\/\//, "").split(/[/?#]/, 1)[0].replace(/\.$/, "");
  const valid = candidate.length <= 253 && /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(candidate);
  return valid ? candidate : "";
}

function selectedCategories() {
  return [...document.querySelectorAll('#category-list input[type="checkbox"]:checked')].map((input) => input.value);
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showToast(message) {
  window.clearTimeout(toastTimeout);
  toast.textContent = message;
  toast.classList.add("is-visible");
  toastTimeout = window.setTimeout(() => toast.classList.remove("is-visible"), 2200);
}

function buildQueries() {
  const domain = normalizeDomain(domainInput.value);
  const hasText = domainInput.value.trim().length > 0;
  domainError.textContent = hasText && !domain ? "Enter a valid domain, such as your-domain.com." : "";

  activeQueries = domain
    ? selectedCategories().map((key) => ({ key, label: CATEGORIES[key].label, query: CATEGORIES[key].make(domain) }))
    : [];

  renderQueries();
}

function renderQueries() {
  queryList.replaceChildren();
  const count = activeQueries.length;
  queryCount.textContent = `${count} ${count === 1 ? "query" : "queries"}`;
  queryEmpty.hidden = count > 0;
  copyAllButton.disabled = count === 0;

  for (const item of activeQueries) {
    const card = makeElement("article", "query-card");
    const main = makeElement("div", "query-main");
    const label = makeElement("div", "query-label", item.label);
    const code = makeElement("code", "query-code", item.query);
    main.append(label, code);

    const tools = makeElement("div", "query-tools");
    const copy = makeElement("button", "query-icon-button", "▢");
    copy.type = "button";
    copy.setAttribute("aria-label", `Copy ${item.label} query`);
    copy.title = "Copy query";
    copy.addEventListener("click", async () => {
      try {
        await copyText(item.query);
        showToast("Query copied");
      } catch {
        showToast("Clipboard access is unavailable");
      }
    });

    const open = makeElement("a", "open-query", "↗");
    open.href = `https://www.google.com/search?q=${encodeURIComponent(item.query)}`;
    open.target = "_blank";
    open.rel = "noopener noreferrer";
    open.setAttribute("aria-label", `Open ${item.label} in Google Search`);
    open.title = "Open Google Search";
    tools.append(copy, open);
    card.append(main, tools);
    queryList.append(card);
  }
}

async function copyText(value) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch {
    // Fall through to the local selection-based copy path for file:// and older browsers.
  }

  const temporary = makeElement("textarea");
  temporary.value = value;
  temporary.setAttribute("readonly", "");
  temporary.style.position = "fixed";
  temporary.style.opacity = "0";
  document.body.append(temporary);
  temporary.select();
  const copied = document.execCommand("copy");
  temporary.remove();
  if (!copied) throw new Error("Clipboard access is unavailable");
}

function decodeEntities(value) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = value;
  return textarea.value;
}

function trimUrlPunctuation(value) {
  return value.replace(/[.,;:!?]+$/g, "").replace(/[)\]}]+$/g, (tail) => {
    const open = { ")": "(", "]": "[", "}": "{" };
    let result = tail;
    for (const closing of tail) {
      const opening = open[closing];
      const openCount = (value.match(new RegExp(`\\${opening}`, "g")) || []).length;
      const closeCount = (value.match(new RegExp(`\\${closing}`, "g")) || []).length;
      if (closeCount > openCount) result = result.slice(0, -1);
      else break;
    }
    return result;
  });
}

function unwrapSearchRedirect(candidate) {
  try {
    const parsed = new URL(candidate, "https://www.google.com");
    if (parsed.hostname === "www.google.com" && parsed.pathname === "/url") {
      const destination = parsed.searchParams.get("q") || parsed.searchParams.get("url");
      if (destination) return destination;
    }
  } catch {
    return candidate;
  }
  return candidate;
}

function normalizeWebUrl(raw) {
  let candidate = decodeEntities(raw.trim()).replace(/^<|>$/g, "");
  if (!candidate) return "";
  candidate = unwrapSearchRedirect(candidate);
  candidate = trimUrlPunctuation(candidate);
  if (/^www\./i.test(candidate)) candidate = `https://${candidate}`;

  try {
    const url = new URL(candidate);
    if (url.protocol !== "http:" && url.protocol !== "https:") return "";
    if (!url.hostname || url.username || url.password) return "";
    if (/(^|\.)google\.[a-z.]+$/i.test(url.hostname) && ["/search", "/url", "/imgres"].includes(url.pathname)) return "";
    url.hash = "";
    return url.href;
  } catch {
    return "";
  }
}

function collectCandidates(source) {
  const candidates = [];
  const documentFragment = new DOMParser().parseFromString(source, "text/html");
  for (const anchor of documentFragment.querySelectorAll("a[href]")) {
    candidates.push(anchor.getAttribute("href"));
  }

  const urlPattern = /(?:https?:\/\/|www\.)[^\s"'<>\\]+/gi;
  for (const match of source.matchAll(urlPattern)) candidates.push(match[0]);
  return candidates;
}

function extractUrls() {
  const filter = filterDomain.value.trim() ? normalizeDomain(filterDomain.value) : "";
  if (filterDomain.value.trim() && !filter) {
    showToast("Enter a valid filter domain");
    return;
  }

  const unique = new Set();
  for (const candidate of collectCandidates(sourceInput.value)) {
    const url = normalizeWebUrl(candidate || "");
    if (!url) continue;
    if (filter) {
      const hostname = new URL(url).hostname.toLowerCase();
      if (hostname !== filter && !hostname.endsWith(`.${filter}`)) continue;
    }
    unique.add(url);
    if (unique.size >= 500) break;
  }

  extractedUrls = [...unique];
  renderUrls();
  if (extractedUrls.length === 0) showToast("No matching web links found");
  else if (extractedUrls.length === 500) showToast("Showing the first 500 unique links");
}

function renderUrls() {
  urlList.replaceChildren();
  const count = extractedUrls.length;
  urlCount.textContent = `${count} ${count === 1 ? "link" : "links"}`;
  urlEmpty.hidden = count > 0;
  copyUrlsButton.disabled = count === 0;
  downloadUrlsButton.disabled = count === 0;

  extractedUrls.forEach((url, index) => {
    const row = makeElement("li");
    const number = makeElement("span", "url-number", String(index + 1).padStart(2, "0"));
    const link = makeElement("a", "url-value", url);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    row.append(number, link);
    urlList.append(row);
  });
}

function downloadResults() {
  if (extractedUrls.length === 0) return;
  const blob = new Blob([`${extractedUrls.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
  const objectUrl = URL.createObjectURL(blob);
  const link = makeElement("a");
  link.href = objectUrl;
  link.download = "tekna-extracted-urls.txt";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
  showToast("Text file downloaded");
}

for (const tab of document.querySelectorAll(".mode-tab")) {
  tab.addEventListener("click", () => switchMode(tab.dataset.mode));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const nextMode = tab.dataset.mode === "builder" ? "extractor" : "builder";
    switchMode(nextMode);
    document.querySelector(`[data-mode="${nextMode}"]`).focus();
  });
}

function switchMode(mode) {
  for (const tab of document.querySelectorAll(".mode-tab")) {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".tool-panel")) panel.hidden = panel.dataset.panel !== mode;
}

domainInput.addEventListener("input", buildQueries);
document.querySelector("#clear-domain").addEventListener("click", () => {
  domainInput.value = "";
  domainInput.focus();
  buildQueries();
});
function syncCategoryToggle() {
  const inputs = [...document.querySelectorAll('#category-list input[type="checkbox"]')];
  document.querySelector("#select-all").textContent = inputs.every((input) => input.checked) ? "Clear all" : "Select all";
}

document.querySelector("#select-all").addEventListener("click", () => {
  const inputs = [...document.querySelectorAll('#category-list input[type="checkbox"]')];
  const shouldSelect = inputs.some((input) => !input.checked);
  inputs.forEach((input) => { input.checked = shouldSelect; });
  syncCategoryToggle();
  buildQueries();
});
document.querySelectorAll('#category-list input[type="checkbox"]').forEach((input) => input.addEventListener("change", () => {
  syncCategoryToggle();
  buildQueries();
}));
copyAllButton.addEventListener("click", async () => {
  try {
    await copyText(activeQueries.map((item) => item.query).join("\n"));
    showToast("All queries copied");
  } catch {
    showToast("Clipboard access is unavailable");
  }
});
document.querySelector("#extract-button").addEventListener("click", extractUrls);
document.querySelector("#clear-source").addEventListener("click", () => {
  sourceInput.value = "";
  extractedUrls = [];
  renderUrls();
  sourceInput.focus();
});
copyUrlsButton.addEventListener("click", async () => {
  try {
    await copyText(extractedUrls.join("\n"));
    showToast("Links copied");
  } catch {
    showToast("Clipboard access is unavailable");
  }
});
downloadUrlsButton.addEventListener("click", downloadResults);

buildQueries();
renderUrls();
