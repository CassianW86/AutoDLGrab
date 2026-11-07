"use strict";
// Shared by the dashboard and extension. API values are never localized.
(() => {
  let dictionary = {}, reverse = new Map(), templates = [];
  let language;
  try { language = localStorage.getItem("autodlgrab.language"); } catch (_) {}
  if (!["zh-CN", "en"].includes(language)) language = navigator.language.startsWith("zh") ? "zh-CN" : "en";
  const source = document.currentScript.src;
  const rendered = new Map();
  function canonical(text) { return rendered.get(text) || reverse.get(text) || text; }
  function t(text, values) {
    if (typeof text !== "string") return text;
    const original = canonical(text);
    let key = original, args = values;
    if (!args && !dictionary[key]) {
      for (const template of templates) {
        const match = template.pattern.exec(original);
        if (match) {
          key = template.key;
          args = Object.fromEntries(template.names.map((name, index) => [name, match[index + 1]]));
          break;
        }
      }
    }
    const fill = (value, translate) => value.replace(/\{(\w+)\}/g, (whole, name) =>
      args && args[name] !== undefined ? (translate ? t(String(args[name])) : canonical(String(args[name]))) : whole);
    const result = fill(language === "en" ? dictionary[key] || key : key, true);
    if (result !== original) {
      if (rendered.size > 512) rendered.clear();
      rendered.set(result, fill(key, false));
    }
    return result;
  }
  function apply() {
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach(node => {
      if (!node.dataset.i18n) node.dataset.i18n = node.textContent;
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(node => {
      node.placeholder = t(node.dataset.i18nPlaceholder);
    });
    const selector = document.getElementById("language");
    if (selector) selector.value = language;
  }
  window.t = t;
  window.i18n = {
    get language() { return language; },
    setLanguage(value) {
      if (!["zh-CN", "en"].includes(value)) return;
      language = value;
      try { localStorage.setItem("autodlgrab.language", value); } catch (_) {}
      apply();
      window.dispatchEvent(new Event("languagechange"));
    },
    ready: fetch(new URL("translations.json", source)).then(response => {
      if (!response.ok) throw Error("Cannot load translations");
      return response.json();
    }).then(data => {
      dictionary = data;
      reverse = new Map(Object.entries(data).map(([zh, en]) => [en, zh]));
      templates = Object.keys(data).filter(key => key.includes("{")).map(key => {
        const names = [];
        const escaped = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        const pattern = escaped.replace(/\\\{(\w+)\\\}/g, (_, name) => { names.push(name); return "(.+?)"; });
        return {key, names, pattern:new RegExp("^" + pattern + "$")};
      });
      apply();
    }).catch(error => { console.error(error); apply(); })
  };
  const selector = document.getElementById("language");
  if (selector) selector.addEventListener("change", event => i18n.setLanguage(event.target.value));
})();
