(() => {
  "use strict";
  const dialog = document.getElementById("cm-question-order");
  const actions = document.querySelector(".topbar .actions");
  if (!dialog || !actions || document.querySelector("[data-question-order-toggle]")) return;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "btn cm-question-order-toggle";
  toggle.textContent = "질문 순서";
  toggle.title = "질문을 시간순으로 보기";
  toggle.setAttribute("aria-haspopup", "dialog");
  toggle.setAttribute("aria-controls", dialog.id);
  toggle.setAttribute("data-question-order-toggle", "");
  actions.prepend(toggle);
  toggle.addEventListener("click", () => {
    if (!dialog.open) dialog.showModal();
  });
  dialog.querySelector("[data-question-close]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-question-link]");
    if (!link || !dialog.contains(link)) return;
    dialog.close();
    // Use the existing viewer controls and #node deep links, preserving Store,
    // source data, ids and relationships. Clear only active display filters.
    const clear = document.querySelector('[data-action="clear-filters"]');
    if (clear && !clear.disabled) clear.click();
    if (matchMedia("(max-width: 900px)").matches) {
      document.getElementById("tab-inspector")?.click();
    }
    requestAnimationFrame(() => document.getElementById("pane-inspector")?.focus({preventScroll: true}));
  });
  const formatter = new Intl.DateTimeFormat("ko-KR", {dateStyle: "medium", timeStyle: "medium"});
  for (const time of dialog.querySelectorAll("time[data-question-time]")) {
    const date = new Date(time.dateTime);
    time.textContent = Number.isFinite(date.getTime()) ? formatter.format(date) : "시간 정보 없음";
  }
})();
