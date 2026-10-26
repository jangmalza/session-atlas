(() => {
  "use strict";
  const key="context-graph-ui-theme", allowed=["paper","dark","system"];
  const system=window.matchMedia("(prefers-color-scheme: dark)");
  document.documentElement.dataset.embedded=String(window.parent!==window);
  let choice="paper";
  try {const saved=localStorage.getItem(key);if(allowed.includes(saved))choice=saved;} catch (_) {}
  function apply() {
    document.documentElement.dataset.theme=choice==="system"?(system.matches?"dark":"paper"):choice;
    for(const control of document.querySelectorAll("[data-ui-theme]"))control.value=choice;
  }
  for(const control of document.querySelectorAll("[data-ui-theme]"))control.addEventListener("change",()=>{
    if(!allowed.includes(control.value))return;
    choice=control.value;apply();
    try {localStorage.setItem(key,choice);} catch (_) {}
  });
  for(const control of document.querySelectorAll("[data-theme-standalone]"))control.hidden=window.parent!==window;
  window.addEventListener("storage",event=>{if(event.key===key){choice=allowed.includes(event.newValue)?event.newValue:"paper";apply();}});
  system.addEventListener("change",()=>{if(choice==="system")apply();});
  apply();
})();
