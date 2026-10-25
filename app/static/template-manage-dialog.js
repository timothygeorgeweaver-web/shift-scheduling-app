import {confirmAction} from "/static/confirm-dialog.js?v=1";
import {node, action, addDays} from "/static/planner-grid.js?v=2";

export function askTemplateChange(parent, template, weeks) {
  return new Promise(resolve => {
    const dialog = node("dialog", "", parent);
    dialog.className = "role-order-dialog";
    dialog.setAttribute("aria-label", "Manage template");
    node("h3", "Manage template", dialog);
    node("p", "Template changes do not change weeks already created from it.", dialog);
    const form = node("form", "", dialog);
    const nameLabel = node("label", "Template name", form);
    const name = node("input", "", nameLabel);
    name.type = "text";
    name.required = true;
    name.maxLength = 100;
    name.value = template.name;

    const patternLabel = node("label",
      "Replace pattern from a week in the displayed month (optional)", form);
    const source = node("select", "", patternLabel);
    node("option", "Keep the saved pattern", source).value = "";
    for (const week of weeks) {
      node("option",
        `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`,
        source).value = week.id;
    }
    node("p",
      "To edit a pattern: create a week from the template, edit its shifts, " +
      "then choose that week here to update the template.", form);

    let dirty = false, finished = false;
    function finish(value) {
      if (finished) return;
      finished = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    }
    async function cancel() {
      if (dirty && !await confirmAction("Discard unsaved template changes?")) return;
      finish(null);
    }

    const save = node("button", "Save changes", form);
    save.type = "submit";
    save.className = "button secondary";
    action("Cancel", form, cancel);
    action("Archive template", form, async () => {
      if (!await confirmAction(
        `Archive "${template.name}"? It will disappear from the template list. ` +
        "Existing schedule weeks stay unchanged. Unsaved changes will be discarded."
      )) return;
      finish({name: template.name, active: false});
    });

    form.addEventListener("input", () => {
      dirty = true;
      name.setCustomValidity("");
    });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const cleaned = name.value.trim();
      if (!cleaned) {
        name.setCustomValidity("Enter a template name.");
        name.reportValidity();
        return;
      }
      if (!form.reportValidity()) return;
      const week = weeks.find(item => item.id === source.value);
      if (week && !await confirmAction(
        `Replace "${template.name}" with the shifts from ${week.week_start_date} – ` +
        `${addDays(week.week_start_date, 6)}? Previously created weeks stay unchanged.`
      )) return;
      finish({
        name: cleaned,
        active: true,
        ...(week ? {
          source_week_id: week.id,
          expected_source_revision: week.revision
        } : {})
      });
    });
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      cancel();
    });
    dialog.showModal();
    name.focus();
  });
}
