import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {createScheduleGrid, clockMinute} from "/static/schedule-grid.js?v=2";

export function createShiftEditor({api, expired, identity, parent, onOpen}) {
  const root = node("section", "", parent);
  root.className = "shift-editor";
  root.hidden = true;
  root.setAttribute("aria-label", "Schedule week");
  let generation = 0, busy = false, data = null, roles = [];
  let location = "", weekId = "", dirty = false, needsRefresh = false;
  let selectedStart = null, plannerScroll = 1408;
  let status, content, controls, closeButton, refreshButton, inputFields;

  const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const hours = minutes => {
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  };

  function reset() {
    generation++;
    busy = false;
    data = null;
    selectedStart = null;
    plannerScroll = 1408;
    dirty = false;
    needsRefresh = false;
    root.replaceChildren();
    root.hidden = true;
    onOpen(false);
  }

  function canClose() {
    return !busy && (!dirty || window.confirm("Discard the unsaved shift?"));
  }

  const unload = event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", unload);

  function endpoint() {
    return "/api/v1/staff/locations/" + encodeURIComponent(location) +
      "/schedule-weeks/" + encodeURIComponent(weekId);
  }

  async function request(path, options, current) {
    const response = await api(path, options);
    if (current !== generation) throw new Error("View changed.");
    if (response.status === 401) {
      expired();
      throw new Error("Please sign in again.");
    }
    let result = null;
    try { result = await response.json(); } catch {}
    if (current !== generation) throw new Error("View changed.");
    if (!response.ok) {
      const error = new Error(
        response.status === 409 ? "The schedule changed. Refresh this week before saving again." :
        response.status === 423 ? "This week is no longer editable as a draft. Refresh it." :
        response.status === 422 && typeof result?.detail === "string" ? result.detail :
        response.status === 403 || response.status === 404 ? "This schedule is unavailable." :
        "The request was not confirmed. Refresh this week before trying again."
      );
      error.status = response.status;
      throw error;
    }
    return result;
  }

  async function task(operation) {
    if (busy || root.hidden) return;
    const current = generation;
    busy = true;
    root.setAttribute("aria-busy", "true");
    controls.disabled = true;
    if (inputFields) inputFields.disabled = true;
    try {
      await operation(current);
    } catch (error) {
      if (current === generation) {
        if (error.status !== 422) needsRefresh = true;
        status.textContent = error.message;
      }
    } finally {
      if (current === generation) {
        busy = false;
        root.setAttribute("aria-busy", "false");
        controls.disabled = false;
        if (inputFields) {
          inputFields.disabled = needsRefresh || data?.week.state !== "draft" ||
            !roles.some(role => role.active);
        }
      }
    }
  }

  async function load(current) {
    status.textContent = "Loading schedule week…";
    const [week, roleList] = await Promise.all([
      request(endpoint(), {}, current),
      request("/api/v1/staff/locations/" + encodeURIComponent(location) + "/roles", {}, current)
    ]);
    if (current !== generation) return;
    data = week;
    roles = roleList;
    needsRefresh = false;
    dirty = false;
    render();
    status.textContent = "Schedule loaded.";
  }

  function field(text, type, parent, value) {
    const label = node("label", text, parent);
    const input = node("input", "", label);
    input.type = type;
    input.value = value;
    input.required = true;
    if (type === "time") {
      input.type = "text";
      input.placeholder = "HH:MM";
      input.maxLength = 5;
      input.pattern = "(?:[01][0-9]|2[0-3]):[0-5][0-9]";
      input.title = "Use 24-hour time, such as 09:15 or 17:30.";
      input.autocomplete = "off";
      input.spellcheck = false;
    }
    return input;
  }

  function render() {
    content.replaceChildren();
    inputFields = null;
    const week = data.week;
    node("h3", `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, content);
    node("p", `${week.state.charAt(0).toUpperCase() + week.state.slice(1)} · ${week.timezone}`, content);
    node("p",
      `${data.slots.length} unassigned shifts · ${hours(data.slots.reduce(
        (sum, slot) => sum + slot.estimated_work_minutes, 0
      ))} estimated coverage`, content);

    createScheduleGrid(content, data, roles, (day, minute, position) => {
      if (busy || needsRefresh || !canClose()) return;
      plannerScroll = position;
      selectedStart = {day, minute};
      dirty = false;
      render();
      status.textContent = `New shift starting ${day} at ${clockMinute(minute)}.`;
      const form = content.querySelector("form");
      form?.scrollIntoView({block: "nearest", behavior: "smooth"});
      form?.querySelector("select")?.focus();
    }, plannerScroll);

    if (week.state !== "draft") return;
    if (!selectedStart) {
      node("p", "Choose + Add shift in a 15-minute cell to create a shift.", content);
      return;
    }
    const form = node("form", "", content);
    content.insertBefore(form, content.querySelector(".schedule-grid-scroll"));
    inputFields = node("fieldset", "", form);
    node("legend", "Add shift", inputFields);
    const activeRoles = roles.filter(role => role.active);
    if (!activeRoles.length) {
      node("p", "Add an active job role in Team management before creating shifts.", inputFields);
      inputFields.disabled = true;
      return;
    }

    const layout = node("div", "", inputFields);
    layout.className = "shift-inputs";
    const roleLabel = node("label", "Role", layout);
    const roleSelect = node("select", "", roleLabel);
    roleSelect.required = true;
    for (const role of activeRoles) {
      node("option", role.name, roleSelect).value = role.id;
    }

    const dayLabel = node("label", "Start date", layout);
    const daySelect = node("select", "", dayLabel);
    daySelect.required = true;
    for (let offset = 0; offset < 7; offset++) {
      const day = addDays(week.week_start_date, offset);
      node("option", `${names[weekday(day)]} ${day}`, daySelect).value = day;
    }
    daySelect.value = selectedStart.day;
    const start = field("Start time (24-hour HH:MM)", "time", layout, clockMinute(selectedStart.minute));
    const defaultEnd = (selectedStart.minute + 60) % 1440;
    const end = field("End time (24-hour HH:MM)", "time", layout, clockMinute(defaultEnd));
    start.step = end.step = "60";

    const nextLabel = node("label", "Ends next day", inputFields);
    const next = node("input", "", nextLabel);
    next.type = "checkbox";
    next.checked = selectedStart.minute + 60 >= 1440;
    node("p", "For a midnight finish, choose 00:00 and select Ends next day.", inputFields);
    const noteLabel = node("label", "Shift note (optional)", inputFields);
    const note = node("textarea", "", noteLabel);
    note.maxLength = 500;
    note.rows = 2;

    const save = node("button", "Add shift", inputFields);
    save.type = "submit";
    save.className = "button secondary";
    action("Cancel", inputFields, () => {
      if (!canClose()) return;
      selectedStart = null;
      dirty = false;
      render();
      status.textContent = "Shift entry cancelled.";
    });
    form.addEventListener("input", () => {
      dirty = true;
      status.textContent = "Unsaved shift.";
    });

    form.addEventListener("submit", event => {
      event.preventDefault();
      if (busy || needsRefresh || !form.reportValidity()) return;
      const minute = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
      const startMinute = minute(start.value), endMinute = minute(end.value);
      if (!next.checked && endMinute <= startMinute) {
        status.textContent = "End must follow start. Select Ends next day for an overnight shift.";
        return;
      }
      const body = {
        role_id: roleSelect.value,
        local_date: daySelect.value,
        start_minute: startMinute,
        end_minute: endMinute,
        ends_next_day: next.checked,
        expected_week_revision: data.week.revision,
        note: note.value
      };
      task(async current => {
        const result = await request(endpoint() + "/shifts", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": identity.csrf_token
          },
          body: JSON.stringify(body)
        }, current);
        if (current !== generation) return;
        data = result;
        selectedStart = null;
        dirty = false;
        render();
        status.textContent = "Shift added. The week overview shows its estimated working hours.";
      });
    });
  }

  async function open(targetLocation, targetWeek) {
    if (!canClose()) return;
    reset();
    location = targetLocation;
    weekId = targetWeek;
    root.hidden = false;
    onOpen(true);
    const heading = node("h3", "Schedule week", root);
    heading.tabIndex = -1;
    status = node("p", "", root);
    status.className = "team-status";
    status.setAttribute("role", "status");
    controls = node("fieldset", "", root);
    node("legend", "Week controls", controls);
    refreshButton = action("Refresh week", controls, () => {
      if (canClose()) task(load);
    });
    closeButton = action("Close week", controls, () => {
      if (canClose()) reset();
    });
    content = node("div", "", root);
    inputFields = null;
    heading.focus();
    root.scrollIntoView({block: "start", behavior: "smooth"});
    await task(load);
  }

  return {
    open, reset,
    destroy() {
      window.removeEventListener("beforeunload", unload);
      reset();
    }
  };
}
