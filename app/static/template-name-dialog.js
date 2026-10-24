import {node, action, addDays} from "/static/planner-grid.js?v=2";

export function askTemplateName(parent, week) {
  return new Promise(resolve => {
    const dialog = node("dialog", "", parent);
    dialog.className = "role-order-dialog";
    dialog.setAttribute("aria-label", "Save as template");
    node("h3", "Save as template", dialog);
    node("p",
      `Save the shifts from ${week.week_start_date} – ${addDays(week.week_start_date, 6)}. ` +
      "Future changes to that week will not change this template.", dialog);
    const form = node("form", "", dialog);
    const label = node("label", "Template name", form);
    const input = node("input", "", label);
    input.type = "text";
    input.required = true;
    input.maxLength = 100;
    input.autocomplete = "off";
    let dirty = false, finished = false;

    function finish(value) {
      if (finished) return;
      finished = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    }

    function cancel() {
      if (dirty && !window.confirm("Discard the unsaved template name?")) return;
      finish(null);
    }

    const save = node("button", "Save template", form);
    save.type = "submit";
    save.className = "button secondary";
    action("Cancel", form, cancel);
    input.addEventListener("input", () => {
      dirty = true;
      input.setCustomValidity("");
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      const name = input.value.trim();
      if (!name) {
        input.setCustomValidity("Enter a template name.");
        input.reportValidity();
        return;
      }
      if (form.reportValidity()) finish(name);
    });
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      cancel();
    });
    dialog.showModal();
    input.focus();
  });
}
