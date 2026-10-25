import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {createScheduleGrid, clockMinute} from "/static/schedule-grid.js?v=6";

import {businessDateForLocal, calendarDateForCell} from "/static/business-day.js?v=1";

const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const anchor = "2000-01-03";

export function createTemplateEditor({api, expired, identity, parent, onOpen, onSaved}) {
  const root = node("section", "", parent);
  root.className = "team shift-editor template-editor";
  root.hidden = true;
  let epoch = 0, busy = false, dirty = false, blocked = false;
  let draft = null, roles = [], context = null, revision = 0, nextKey = 0;
  let popup = null, popupDirty = false, message = "", nameInput;

  function dismissPopup() {
    if (popup?.open) popup.close();
    popup?.remove();
    popup = null;
    popupDirty = false;
  }

  function reset() {
    epoch++;
    dismissPopup();
    busy = dirty = blocked = false;
    draft = null;
    root.replaceChildren();
    root.hidden = true;
    onOpen(false);
  }

  function canClose() {
    return !busy && (!dirty || window.confirm("Discard local template changes?"));
  }

  function close() {
    if (canClose()) reset();
  }

  const unload = event => {
    if (dirty || popupDirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", unload);

  const base = () => "/api/v1/staff/locations/" +
    encodeURIComponent(context.locationId) + "/schedule-templates";

  async function request(path, options, current) {
    const response = await api(path, options);
    if (current !== epoch) throw new Error("View changed.");
    if (response.status === 401) {
      expired();
      throw new Error("Please sign in again.");
    }
    const result = await response.json().catch(() => null);
    if (current !== epoch) throw new Error("View changed.");
    if (!response.ok) {
      const error = new Error(
        typeof result?.detail === "string" ? result.detail :
        "The request was not confirmed. Close and refresh templates before retrying."
      );
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function displaySlots() {
    return draft.slots.map(slot => {
      const day = calendarDateForCell(
        addDays(anchor, slot.weekday), slot.start_minute,
        context.rolloverMinute ?? 0
      );
      const endDay = addDays(day, slot.ends_next_day ? 1 : 0);
      const start = day + "T" + clockMinute(slot.start_minute) + ":00";
      const end = endDay + "T" + clockMinute(slot.end_minute) + ":00";
      return {
        ...slot,
        id: String(slot.key),
        role_name: roles.find(role => role.id === slot.role_id)?.name ?? "Unavailable job type",
        local_start: start,
        local_end: end,
        end_at: end + "Z"
      };
    });
  }

  function timeSelect(label, value, parent) {
    const select = node("select", "", node("label", label, parent));
    select.required = true;
    for (let cell = 0; cell < 96; cell++) {
      const minute = ((context.rolloverMinute ?? 0) + cell * 15) % 1440;
      node("option", clockMinute(minute), select).value = String(minute);
    }
    select.value = String(value);
    return select;
  }

  function openShift(existing, day, minute, element) {
    if (busy || blocked || !draft) return;
    dismissPopup();
    const box = element.getBoundingClientRect();
    const value = existing ?? {
      weekday: day, start_minute: minute,
      end_minute: (minute + 60) % 1440,
      ends_next_day: minute + 60 >= 1440, note: ""
    };
    popup = node("dialog", "", root);
    popup.className = "shift-popup";
    popup.setAttribute("aria-label", existing ? "Edit template shift" : "Add template shift");
    node("h3", existing ? "Edit template shift" : "Add template shift", popup);
    node("p", "Changes here update the draft. Click Save template to keep them.", popup);
    const status = node("p", "", popup);
    status.setAttribute("role", "status");
    const form = node("form", "", popup);
    const layout = node("div", "", form);
    layout.className = "shift-inputs";
    const role = node("select", "", node("label", "Job type", layout));
    role.required = true;
    const activeRoles = roles.filter(item => item.active);
    for (const item of activeRoles) node("option", item.name, role).value = item.id;
    if (existing) {
      if (!activeRoles.some(item => item.id === existing.role_id)) {
        const option = node("option", "Inactive job type — choose another", role);
        option.value = existing.role_id;
        option.disabled = true;
      }
      role.value = existing.role_id;
    }
    const daySelect = node("select", "", node("label", "Starting business weekday", layout));
    names.forEach((name, index) => node("option", name, daySelect).value = String(index));
    daySelect.value = String(value.weekday);
    const start = timeSelect("Start time (24-hour)", value.start_minute, layout);
    const end = timeSelect("End time (24-hour)", value.end_minute, layout);
    const next = node("input", "", node("label", "Ends next calendar day", form));
    next.type = "checkbox";
    next.checked = value.ends_next_day;
    node("p", "Times before rollover are on the next calendar day. " +
      "Ends next calendar day means the day after the actual start.", form);
    const note = node("textarea", "", node("label", "Shift note (optional)", form));
    note.maxLength = 500;
    note.rows = 2;
    note.value = value.note;
    const save = node("button", existing ? "Save shift" : "Add shift", form);
    save.type = "submit";
    save.className = "button secondary";
    save.disabled = !activeRoles.length;

    function cancel() {
      if (popupDirty && !window.confirm("Discard these shift changes?")) return;
      dismissPopup();
    }
    action("Cancel", form, cancel);
    if (existing) action("Delete shift", form, () => {
      if (!window.confirm("Remove this shift from the template draft?")) return;
      draft.slots = draft.slots.filter(slot => slot.key !== existing.key);
      dirty = true;
      message = "Unsaved template changes.";
      render();
    });
    form.addEventListener("input", () => { popupDirty = true; });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      if (!activeRoles.some(item => item.id === role.value)) {
        status.textContent = "Choose an active job type.";
        return;
      }
      const startMinute = Number(start.value), endMinute = Number(end.value);
      if (!next.checked && endMinute <= startMinute) {
        status.textContent = "End must follow start, or select Ends next day.";
        return;
      }
      if (!existing && draft.slots.length >= 500) {
        status.textContent = "A template can contain up to 500 shifts.";
        return;
      }
      const changed = {
        key: existing?.key ?? ++nextKey,
        role_id: role.value, weekday: Number(daySelect.value),
        start_minute: startMinute, end_minute: endMinute,
        ends_next_day: next.checked, note: note.value
      };
      if (existing) {
        draft.slots = draft.slots.map(slot => slot.key === existing.key ? changed : slot);
      } else draft.slots.push(changed);
      dirty = true;
      message = "Unsaved template changes.";
      render();
    });
    popup.addEventListener("cancel", event => {
      event.preventDefault();
      cancel();
    });
    popup.showModal();
    const size = popup.getBoundingClientRect();
    const margin = 16, gap = 10;
    let left = (window.innerWidth-size.width)/2;
    if (box.right+gap+size.width <= window.innerWidth-margin) left = box.right+gap;
    else if (box.left-gap-size.width >= margin) left = box.left-gap-size.width;
    popup.style.left = Math.max(margin,
      Math.min(left, window.innerWidth-size.width-margin)) + "px";
    popup.style.top = Math.max(margin,
      Math.min(box.top, window.innerHeight-size.height-margin)) + "px";
    role.focus({preventScroll: true});
  }

  function render() {
    dismissPopup();
    root.replaceChildren();
    node("h2", context.templateId ? "Edit Template" : "Create New Template", root);
    node("p", "Generic Monday–Sunday pattern. No calendar dates. " +
      "Changes do not affect schedule weeks already created from this template.", root);
    node("p", message, root).setAttribute("role", "status");
    const controls = node("fieldset", "", root);
    const save = action("Save template", controls, saveTemplate);
    save.disabled = busy || blocked || !draft;
    const cancel = action("Close template", controls, close);
    cancel.disabled = busy;
    if (!draft) return;
    nameInput = node("input", "", node("label", "Template name", root));
    nameInput.required = true;
    nameInput.maxLength = 100;
    nameInput.value = draft.name;
    nameInput.disabled = busy || blocked;
    nameInput.addEventListener("input", () => {
      draft.name = nameInput.value;
      dirty = true;
      nameInput.setCustomValidity("");
    });
    node("p", `Business days start at ${clockMinute(context.rolloverMinute ?? 0)}.`, root);
    node("p", `${draft.slots.length} shifts · ${dirty ? "Unsaved changes" : "Saved pattern"}`, root);
    createScheduleGrid(root, {
      week: {week_start_date: anchor, state: "draft",
        business_day_start_minute: context.rolloverMinute ?? 0},
      slots: displaySlots()
    }, roles,
      (day, minute, position, element) => {
        const businessDay = businessDateForLocal(
          `${day}T${clockMinute(minute)}:00`, context.rolloverMinute ?? 0
        );
        openShift(null, weekday(businessDay), minute, element);
      },
      0,
      (slot, element) => {
        const existing = draft.slots.find(item => String(item.key) === slot.id);
        if (existing) openShift(existing, existing.weekday, existing.start_minute, element);
      },
      {genericWeek: true}
    );
  }

  async function saveTemplate() {
    if (busy || blocked || !draft) return;
    if (!draft.name.trim()) {
      nameInput.setCustomValidity("Enter a template name.");
      nameInput.reportValidity();
      return;
    }
    if (!nameInput.reportValidity()) return;
    const current = epoch;
    const payload = {
      name: draft.name.trim(),
      slots: draft.slots.map(({key, ...slot}) => slot),
      expected_template_revision: revision,
      expected_location_revision: context.locationRevision
    };
    const path = base() + (context.templateId
      ? "/" + encodeURIComponent(context.templateId) + "/pattern" : "");
    busy = true;
    message = "Saving template…";
    render();
    try {
      await request(path, {
        method: context.templateId ? "PUT" : "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": identity.csrf_token
        },
        body: JSON.stringify(payload)
      }, current);
      if (current !== epoch) return;
      dirty = false;
      reset();
      await onSaved();
    } catch (error) {
      if (current !== epoch) return;
      busy = false;
      blocked = error.status !== 422;
      message = error.message + (blocked
        ? " Close this editor and refresh the template list before retrying." : "");
      render();
    }
  }

  async function open(options) {
    if (!canClose()) return;
    reset();
    context = {...options};
    root.hidden = false;
    onOpen(true);
    busy = true;
    revision = 0;
    message = "Loading template planner…";
    render();
    const current = epoch;
    try {
      const [roleList, pattern] = await Promise.all([
        request("/api/v1/staff/locations/" +
          encodeURIComponent(context.locationId) + "/roles", {}, current),
        context.templateId
          ? request(base() + "/" + encodeURIComponent(context.templateId) + "/pattern", {}, current)
          : Promise.resolve(null)
      ]);
      if (current !== epoch) return;
      roles = roleList;
      draft = {
        name: pattern?.name ?? "",
        slots: (pattern?.slots ?? []).map(slot => ({...slot, key: ++nextKey}))
      };
      context.rolloverMinute = pattern?.business_day_start_minute ?? 0;
      revision = pattern?.revision ?? 0;
      context.locationRevision = pattern?.location_revision ?? context.locationRevision;
      busy = false;
      message = "Select a 15-minute starting cell to add a shift.";
      render();
      root.scrollIntoView({block: "start", behavior: "smooth"});
    } catch (error) {
      if (current !== epoch) return;
      busy = false;
      blocked = true;
      message = error.message;
      render();
    }
  }

  return {
    open, reset,
    destroy() {
      window.removeEventListener("beforeunload", unload);
      reset();
    }
  };
}
