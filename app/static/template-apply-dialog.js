import {node, action, addDays} from "/static/planner-grid.js?v=2";

export function askTemplateApplication(parent, templates, day, existing, signal) {
  return new Promise(resolve => {
    if (signal.aborted) { resolve(null); return; }
    const dialog = node("dialog", "", parent);
    dialog.className = "role-order-dialog";
    dialog.setAttribute("aria-label", "Apply Template");
    node("h3", "Apply Template", dialog);
    node("p", `${day} – ${addDays(day, 6)}`, dialog);
    node("p", existing
      ? "Append keeps existing shifts. Replace removes all current shifts before adding the template."
      : "A new draft week will be created from the selected template.", dialog);
    const form = node("form", "", dialog);
    const choice = node("select", "", node("label", "Template", form));
    choice.required = true;
    node("option", "Choose a template", choice).value = "";
    for (const template of templates) {
      node("option", `${template.name} · ${template.shift_count} shifts`, choice).value = template.id;
    }
    const mode = node("select", "", node("label", "How to apply", form));
    node("option", "Append template shifts", mode).value = "append";
    node("option", "Replace all shifts with template", mode).value = "replace";
    let finished = false;
    function finish(value) {
      if (finished) return;
      finished = true;
      signal.removeEventListener("abort", abort);
      if (dialog.open) dialog.close();
      dialog.remove();
      resolve(value);
    }
    const abort = () => finish(null);
    signal.addEventListener("abort", abort, {once: true});
    const submit = node("button", "Apply Template", form);
    submit.type = "submit";
    submit.className = "button secondary";
    action("Cancel", form, () => finish(null));
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const template = templates.find(item => item.id === choice.value);
      if (template) finish({template, mode: mode.value});
    });
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      finish(null);
    });
    dialog.showModal();
    choice.focus();
  });
}
