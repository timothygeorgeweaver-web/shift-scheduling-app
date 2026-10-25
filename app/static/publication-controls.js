import {node, action, addDays} from "/static/planner-grid.js?v=3";
import {renderPublishedRoster} from "/static/published-roster.js?v=1";

const labels = {
  inactive_employee: "Employee inactive", inactive_role: "Job type inactive",
  not_qualified: "Qualification required", overlap: "Overlapping assignment",
  rest_before: "Violates Rest Policy before this shift",
  rest_after: "Violates Rest Policy after this shift",
  unavailability: "Availability approval required",
  daily_hours: "Daily hours approval required"
};

function dialogShell({parent, title, signal, isActive, wide = false, anchor}) {
  const dialog = node("dialog", "", parent);
  dialog.className = "publication-dialog" + (wide ? " publication-history-dialog" : "");
  dialog.setAttribute("aria-label", title);
  const bar = node("div", "", dialog);
  bar.className = "shift-popup-bar";
  node("h3", title, bar);
  let resolve, finished = false, saving = false;
  const promise = new Promise(done => { resolve = done; });
  const active = () => !finished && parent.isConnected && isActive() && !signal.aborted;
  const close = action("Close", bar, () => { if (!saving) finish(null); });
  const status = node("p", "Loading…", dialog);
  status.setAttribute("role", "status");
  const body = node("div", "", dialog);
  const box = anchor?.getBoundingClientRect();
  function position() {
    if (!dialog.open) return;
    const rect = dialog.getBoundingClientRect();
    const width = window.innerWidth, height = window.innerHeight;
    let left = (width - rect.width) / 2;
    if (!wide && box?.right + 10 + rect.width <= width - 16) left = box.right + 10;
    else if (!wide && box?.left - 10 - rect.width >= 16) left = box.left - 10 - rect.width;
    const top = !wide && box ? box.top : (height - rect.height) / 2;
    dialog.style.left = Math.max(16, Math.min(left, width - rect.width - 16)) + "px";
    dialog.style.top = Math.max(16, Math.min(top, height - rect.height - 16)) + "px";
  }
  const observer = new ResizeObserver(position);
  const aborted = () => finish(null);
  function finish(value) {
    if (finished) return;
    finished = true;
    observer.disconnect();
    window.removeEventListener("resize", position);
    signal.removeEventListener("abort", aborted);
    if (dialog.open) dialog.close();
    dialog.remove();
    resolve(value);
  }
  dialog.addEventListener("cancel", event => {
    event.preventDefault();
    if (!saving) finish(null);
  });
  signal.addEventListener("abort", aborted, {once: true});
  window.addEventListener("resize", position);
  dialog.showModal();
  observer.observe(dialog);
  position();
  close.focus({preventScroll: true});
  return {body, status, active, finish, promise, setSaving(value) {
    saving = value;
    close.disabled = value;
    dialog.setAttribute("aria-busy", String(value));
  }};
}

function requester(api, expired, shell) {
  return async (path, options = {}) => {
    const response = await api(path, options);
    if (!shell.active()) throw new Error("View changed.");
    if (response.status === 401) {
      expired();
      throw new Error("Please sign in again.");
    }
    const result = await response.json().catch(() => null);
    if (!shell.active()) throw new Error("View changed.");
    if (!response.ok) throw new Error(typeof result?.detail === "string"
      ? result.detail : "The request was not confirmed. Close this window and refresh.");
    return result;
  };
}

export async function reviewAndPublish(options) {
  const {parent, api, expired, identity, locationId, locationRevision, week, signal, isActive} = options;
  if (signal.aborted || !isActive() || !parent.isConnected) return null;
  const shell = dialogShell({...options, title: "Publish Review"});
  const request = requester(api, expired, shell);
  const base = "/api/v1/staff/locations/" + encodeURIComponent(locationId) +
    "/schedule-weeks/" + encodeURIComponent(week.id);
  const expected = {
    expected_week_revision: week.revision,
    expected_location_revision: locationRevision,
    expected_publication_number: week.publication_number ?? 0
  };
  const post = body => ({method: "POST", headers: {
    "Content-Type": "application/json", "X-CSRF-Token": identity.csrf_token
  }, body: JSON.stringify(body)});
  void (async () => {
    try {
      const review = await request(base + "/publication-review", post(expected));
      if (!shell.active()) return;
      const shifts = new Map(review.roster.shifts.map(shift => [shift.id, shift]));
      const describe = id => {
        const shift = shifts.get(id);
        return shift ? `${shift.role_name}, ${shift.employee?.display_name ?? "Unfilled"}, ` +
          shift.local_start.slice(0, 16).replace("T", " ") : "Shift";
      };
      node("p", `${week.week_start_date} – ${addDays(week.week_start_date, 6)} · ` +
        `${review.roster.shifts.length} shifts · ${review.unfilled_slot_ids.length} Unfilled`, shell.body);
      node("p", "Publication makes this roster official and locks employee availability for its business week. " +
        "Later working edits remain private until republished.", shell.body);
      if (review.hard_conflicts.length) {
        node("h4", "Resolve before publication", shell.body);
        const list = node("ul", "", shell.body);
        for (const row of review.hard_conflicts) node("li", describe(row.slot_id) + ": " +
          row.rule_codes.map(code => labels[code] ?? code).join("; "), list);
      }
      if (review.warnings.length) {
        node("h4", "Assignment warnings", shell.body);
        const list = node("ul", "", shell.body);
        for (const row of review.warnings) node("li", describe(row.slot_id) + ": " +
          (labels[row.rule_code] ?? row.rule_code) +
          (row.approved ? " — current approval recorded" : " — renew approval in Assign Shifts"), list);
      }
      if (review.unfilled_slot_ids.length) {
        const details = node("details", "", shell.body);
        node("summary", `${review.unfilled_slot_ids.length} Unfilled shifts`, details);
        const list = node("ul", "", details);
        for (const id of review.unfilled_slot_ids) node("li", describe(id), list);
      }
      const fields = node("fieldset", "", shell.body);
      node("legend", "Confirm publication", fields);
      const confirm = node("input", "", node("label", "I confirm publication of this roster", fields));
      confirm.type = "checkbox";
      let unfilled = null;
      if (review.unfilled_slot_ids.length) {
        unfilled = node("input", "", node("label", "I confirm publication with Unfilled shifts", fields));
        unfilled.type = "checkbox";
      }
      let blocked = !review.can_publish;
      const publish = action(`Publish version ${review.publication_number + 1}`, fields, async () => {
        if (blocked || !confirm.checked || (unfilled && !unfilled.checked)) return;
        fields.disabled = true;
        shell.setSaving(true);
        shell.status.textContent = "Publishing…";
        try {
          const result = await request(base + "/publish", post({
            ...expected, confirm_publish: true, confirm_unfilled: Boolean(unfilled?.checked)
          }));
          shell.finish(result);
        } catch (error) {
          if (shell.active()) {
            blocked = true;
            shell.status.textContent = error.message + " Close and reopen Publish Review before trying again.";
          }
        } finally {
          if (shell.active()) {
            shell.setSaving(false);
            fields.disabled = false;
            update();
          }
        }
      });
      function update() {
        publish.disabled = blocked || !confirm.checked || Boolean(unfilled && !unfilled.checked);
      }
      fields.addEventListener("change", update);
      update();
      shell.status.textContent = review.can_publish
        ? "Review the roster and confirm when ready. Eligibility is checked again on publication."
        : "Resolve conflicts or renew warning approvals in Assign Shifts, then reopen Publish Review.";
    } catch (error) {
      if (shell.active()) shell.status.textContent = error.message;
    }
  })();
  return shell.promise;
}

export async function showPublicationHistory(options) {
  const {parent, api, expired, locationId, week, signal, isActive} = options;
  if (signal.aborted || !isActive() || !parent.isConnected) return null;
  const shell = dialogShell({...options, title: "Publication History", wide: true});
  const request = requester(api, expired, shell);
  const base = "/api/v1/staff/locations/" + encodeURIComponent(locationId) +
    "/schedule-weeks/" + encodeURIComponent(week.id) + "/publications";
  void (async () => {
    try {
      const versions = await request(base);
      if (!shell.active()) return;
      if (!versions.length) {
        shell.status.textContent = "This week has not been published.";
        return;
      }
      const select = node("select", "", node("label", "Published version", shell.body));
      for (const version of versions) node("option", `Version ${version.publication_number}`, select)
        .value = String(version.publication_number);
      const view = node("div", "", shell.body);
      async function load() {
        select.disabled = true;
        view.replaceChildren();
        shell.status.textContent = "Loading immutable published version…";
        try {
          const result = await request(base + "/" + encodeURIComponent(select.value));
          if (!shell.active()) return;
          renderPublishedRoster(view, result);
          shell.status.textContent = "Read-only published history. Working edits do not change these versions.";
        } catch (error) {
          if (shell.active()) shell.status.textContent = error.message;
        } finally {
          if (shell.active()) select.disabled = false;
        }
      }
      select.value = String(versions[versions.length - 1].publication_number);
      select.addEventListener("change", load);
      await load();
    } catch (error) {
      if (shell.active()) shell.status.textContent = error.message;
    }
  })();
  return shell.promise;
}
