import {node, action} from "/static/planner-grid.js?v=2";

const labels = {
  unavailability: "Employee is unavailable",
  daily_hours: "More than eight estimated working hours",
  inactive_employee: "Inactive employee",
  inactive_role: "Inactive job type",
  not_qualified: "Not qualified for this job type",
  overlap: "Overlapping shift",
  rest_before: "Insufficient rest before this shift",
  rest_after: "Insufficient rest after this shift"
};

export function createAssignmentControls({
  parent, endpoint, shift, assignment, weekRevision,
  request, run, canEdit, markDirty, onSaved, say
}) {
  const section = node("section", "", parent);
  section.className = "assignment-panel";
  node("h4", "Employee assignment", section);
  node("p", assignment
    ? `Assigned to ${assignment.display_name}${assignment.employee_active ? "" : " (inactive)"}.`
    : "No employee assigned.", section);
  if (assignment) {
    node("p",
      "Unassign the employee before changing this shift’s job type or times.",
      section);
  }

  const form = node("form", "", section);
  const fields = node("fieldset", "", form);
  node("legend", "Choose an employee", fields);
  const message = node("p", "Load employees to check current eligibility.", fields);
  message.setAttribute("role", "status");
  const choices = node("div", "", fields);
  const approvals = node("div", "", fields);
  const blocked = node("details", "", fields);
  node("summary", "Employees who cannot be assigned", blocked);
  const blockedList = node("ul", "", blocked);
  blocked.hidden = true;

  let selected = null, select = null, policyRevision = null;
  let candidates = new Map(), approvalFields = [];
  let loading = false;

  const loadButton = action("Load employees", fields, () => {
    if (loading || !canEdit()) {
      say("Save or cancel any unsaved shift changes before checking employees.");
      return;
    }
    run(async current => {
      loading = true;
      fields.disabled = true;
      try {
        const result = await request(endpoint + "/candidates", {}, current);
        if (!section.isConnected) return;
        choices.replaceChildren();
        approvals.replaceChildren();
        blockedList.replaceChildren();
        candidates = new Map();
        selected = null;
        approvalFields = [];
        policyRevision = null;
        save.disabled = true;

        const label = node("label", "Employee", choices);
        select = node("select", "", label);
        select.required = true;
        node("option", "Select an employee", select).value = "";

        for (const [category, title] of [
          ["eligible", "Eligible"],
          ["override_required", "Requires approval"],
          ["blocked", "Cannot assign"]
        ]) {
          const rows = result.groups[category] ?? [];
          if (!rows.length) continue;
          const group = node("optgroup", "", select);
          group.label = title;
          for (const candidate of rows) {
            candidates.set(candidate.employee_id, candidate);
            policyRevision = candidate.location_revision;
            const option = node("option", candidate.display_name, group);
            option.value = candidate.employee_id;
            option.disabled = category === "blocked";
            if (category === "blocked") {
              node("li", `${candidate.display_name}: ` +
                candidate.hard_blocks.map(code => labels[code] ?? code).join("; "),
                blockedList);
            }
          }
        }
        blocked.hidden = !blockedList.children.length;
        remove.disabled = !assignment || policyRevision === null;
        loadButton.textContent = "Reload employees";
        message.textContent = candidates.size
          ? "Eligibility is checked again when you save."
          : "No employee profiles are available at this workplace.";

        select.addEventListener("change", () => {
          selected = candidates.get(select.value) ?? null;
          approvals.replaceChildren();
          approvalFields = [];
          save.disabled = !selected || selected.category === "blocked";
          markDirty();
          if (!selected) return;

          for (const code of selected.warnings) {
            const box = node("fieldset", "", approvals);
            node("legend", labels[code] ?? code, box);
            const confirmLabel = node("label", "I approve this warning", box);
            const confirm = node("input", "", confirmLabel);
            confirm.type = "checkbox";
            confirm.required = true;
            const reasonLabel = node("label", "Reason for approval", box);
            const reason = node("textarea", "", reasonLabel);
            reason.required = true;
            reason.maxLength = 500;
            reason.rows = 2;
            approvalFields.push({code, confirm, reason});
          }
        });
      } finally {
        loading = false;
        if (section.isConnected) fields.disabled = false;
      }
    });
  });

  const save = node("button", "Save assignment", fields);
  save.type = "submit";
  save.className = "button secondary";
  save.disabled = true;

  const remove = action("Unassign employee", fields, () => {
    if (loading || !canEdit() || !assignment || policyRevision === null) return;
    if (!window.confirm(
      `Unassign ${assignment.display_name}? The shift will remain in the planner.`
    )) return;
    run(async current => {
      loading = true;
      fields.disabled = true;
      try {
        await request(endpoint + "/assignment/remove", {
          method: "POST",
          body: JSON.stringify({
            expected_assignment_id: assignment.id,
            expected_assignment_revision: assignment.revision,
            expected_week_revision: weekRevision,
            expected_slot_revision: shift.revision,
            expected_location_revision: policyRevision
          })
        }, current);
        await onSaved(current);
        say("Employee unassigned. The shift is still available.");
      } finally {
        loading = false;
        if (section.isConnected) fields.disabled = false;
      }
    });
  });
  remove.hidden = !assignment;
  remove.disabled = true;

  form.addEventListener("input", markDirty);
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (loading || !canEdit() || !selected || !form.reportValidity()) return;
    if (selected.category === "blocked") return;

    const reasons = [];
    for (const row of approvalFields) {
      const reason = row.reason.value.trim();
      if (!row.confirm.checked || !reason) {
        message.textContent = "Confirm each warning and provide its reason.";
        return;
      }
      reasons.push({rule_code: row.code, reason});
    }

    const replacing = assignment &&
      assignment.employee_id !== selected.employee_id;
    if (replacing && !window.confirm(
      `Replace ${assignment.display_name} with ${selected.display_name}?`
    )) return;

    const candidate = selected;
    run(async current => {
      loading = true;
      fields.disabled = true;
      try {
        await request(endpoint + "/assignment", {
          method: "PUT",
          body: JSON.stringify({
            employee_id: candidate.employee_id,
            expected_week_revision: candidate.week_revision,
            expected_slot_revision: candidate.slot_revision,
            expected_location_revision: candidate.location_revision,
            expected_assignment_id: assignment?.id ?? null,
            expected_assignment_revision: assignment?.revision ?? 0,
            confirm_replacement: Boolean(replacing),
            approvals: reasons
          })
        }, current);
        await onSaved(current);
        say("Employee assignment saved.");
      } finally {
        loading = false;
        if (section.isConnected) fields.disabled = false;
      }
    });
  });
}
