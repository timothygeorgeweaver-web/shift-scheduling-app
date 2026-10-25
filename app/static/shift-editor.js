import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {createScheduleGrid, clockMinute} from "/static/schedule-grid.js?v=6";
import {businessDateForLocal, calendarDateForCell} from "/static/business-day.js?v=1";

export function createShiftEditor({api, expired, identity, parent, onOpen}) {
  const root = node("section", "", parent);
  root.className = "shift-editor";
  root.hidden = true;
  root.setAttribute("aria-label", "Schedule week");
  let generation = 0, busy = false, data = null, roles = [];
  let location = "", weekId = "", dirty = false, needsRefresh = false;
  let selectedStart = null, plannerScroll = 1408;
  let popup = null, popupMessage = null, popupAnchor = null;
  let status, content, controls, closeButton, refreshButton, inputFields;

  const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const hours = minutes => {
    const h = Math.floor(minutes / 60), m = minutes % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  };

  function reset() {
    dismissPopup();
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
        say(error.message);
      }
    } finally {
      if (current === generation) {
        busy = false;
        root.setAttribute("aria-busy", "false");
        controls.disabled = false;
        if (inputFields) {
          inputFields.disabled = needsRefresh || data?.week.state !== "draft";
        }
      }
    }
  }

  async function load(current) {
    say("Loading schedule week…");
    const [week, roleList] = await Promise.all([
      request(endpoint(), {}, current),
      request("/api/v1/staff/locations/" + encodeURIComponent(location) + "/roles", {}, current)
    ]);
    if (current !== generation) return;
    data = week;
    roles = roleList;
    needsRefresh = false;
    selectedStart = null;
    dirty = false;
    render();
    say("Schedule loaded.");
  }

  function say(text) {
    if (status) status.textContent = text;
    if (popupMessage) popupMessage.textContent = text;
  }

  function dismissPopup() {
    if (popup?.open) popup.close();
    popup?.remove();
    popup = null;
    popupMessage = null;
  }

  function cancelPopup() {
    if (!canClose()) return;
    selectedStart = null;
    dirty = false;
    render();
    say("Shift entry closed.");
  }

  function positionPopup() {
    if (!popup?.open) return;
    const margin = 16, gap = 10;
    const box = popup.getBoundingClientRect();
    const width = window.innerWidth;
    const height = window.innerHeight;
    let left = (width - box.width) / 2;
    let top = (height - box.height) / 2;
    if (popupAnchor && width >= 640) {
      if (popupAnchor.right + gap + box.width <= width - margin) {
        left = popupAnchor.right + gap;
      } else if (popupAnchor.left - gap - box.width >= margin) {
        left = popupAnchor.left - gap - box.width;
      }
      top = popupAnchor.top;
    }
    popup.style.left = Math.max(
      margin, Math.min(left, width - box.width - margin)
    ) + "px";
    popup.style.top = Math.max(
      margin, Math.min(top, height - box.height - margin)
    ) + "px";
  }

  function presentPopup() {
    if (!popup) return;
    popup.showModal();
    positionPopup();
    popup.querySelector("select")?.focus({preventScroll: true});
    requestAnimationFrame(positionPopup);
  }

  window.addEventListener("resize", positionPopup);

  function field(text, type, parent, value) {
    const label = node("label", text, parent);
    const select = node("select", "", label);
    select.required = true;
    const rollover = data.week.business_day_start_minute;
    for (let cell = 0; cell < 96; cell++) {
      const minute = (rollover + cell * 15) % 1440;
      const time = clockMinute(minute);
      node("option", time, select).value = time;
    }
    const supported = Array.from(select.options).some(
      option => option.value === value
    );
    if (!supported) {
      const placeholder = node("option",
        `Saved time ${value} — select a 15-minute time`, select);
      placeholder.value = "";
      placeholder.disabled = true;
      select.value = "";
    } else {
      select.value = value;
    }
    return select;
  }

  function chooseExisting(shift, anchor) {
    if (busy || needsRefresh || !canClose()) return;
    popupAnchor = anchor.getBoundingClientRect();
    selectedStart = {
      day: shift.local_start.slice(0, 10),
      minute: Number(shift.local_start.slice(11, 13)) * 60 +
        Number(shift.local_start.slice(14, 16)),
      shift
    };
    dirty = false;
    render();
    say("Editing saved shift.");
  }

  function renderCoverage() {
    const details = node("details", "", content);
    node("summary", "Shift counts by starting business day and job type", details);
    node("p",
      "Counts use the business day on which each shift starts. " +
      "Counts include assigned and unassigned coverage slots.",
      details);
    const table = node("table", "", details);
    table.className = "schedule-summary";
    const head = node("tr", "", node("thead", "", table));
    node("th", "Job type", head).scope = "col";
    const days = [];
    for (let offset = 0; offset < 7; offset++) {
      const day = addDays(data.week.week_start_date, offset);
      days.push(day);
      node("th", names[weekday(day)].slice(0, 3), head).scope = "col";
    }
    node("th", "Total", head).scope = "col";
    const body = node("tbody", "", table);
    const roleMap = new Map(roles.map(role => [role.id, role]));
    const used = [...new Set(data.slots.map(slot => slot.role_id))];
    used.sort((a, b) =>
      (roleMap.get(a)?.display_order ?? 0) - (roleMap.get(b)?.display_order ?? 0) ||
      (roleMap.get(a)?.name ?? a).localeCompare(roleMap.get(b)?.name ?? b)
    );
    for (const id of used) {
      const slots = data.slots.filter(slot => slot.role_id === id);
      const row = node("tr", "", body);
      node("th", slots[0].role_name, row).scope = "row";
      for (const day of days) {
        node("td", String(slots.filter(slot =>
          businessDateForLocal(slot.local_start, data.week.business_day_start_minute) === day).length), row);
      }
      node("td", String(slots.length), row);
    }
    const total = node("tr", "", body);
    node("th", "Total shifts", total).scope = "row";
    for (const day of days) {
      node("td", String(data.slots.filter(slot =>
        businessDateForLocal(slot.local_start, data.week.business_day_start_minute) === day).length), total);
    }
    node("td", String(data.slots.length), total);
  }

  function render() {
    dismissPopup();
    content.replaceChildren();
    inputFields = null;
    const week = data.week;
    node("h3", `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, content);
    node("p", `${week.state.charAt(0).toUpperCase() + week.state.slice(1)} · ${week.timezone}`, content);
    node("p",
      `${data.slots.length} shifts · ${(data.assignments ?? []).length} assigned · ${hours(data.slots.reduce(
        (sum, slot) => sum + slot.estimated_work_minutes, 0
      ))} estimated coverage`, content);

    const rollover = week.business_day_start_minute;
    node("p", `Business days run from ${clockMinute(rollover)} to ` +
      `${clockMinute(rollover)} the following calendar day. ` +
      "Early-morning rows marked Next day belong to the column above.", content);

    renderCoverage();

    createScheduleGrid(content, data, roles, (day, minute, position, anchor) => {
      if (busy || needsRefresh || !canClose()) return;
      popupAnchor = anchor.getBoundingClientRect();
      plannerScroll = position;
      selectedStart = {day, minute};
      dirty = false;
      render();
      say(`New shift starting ${day} at ${clockMinute(minute)}.`);
    }, plannerScroll, chooseExisting);

    if (week.state !== "draft") return;
    if (!selectedStart) {
      node("p", "Choose + Add shift in a 15-minute cell to create a shift.", content);
      return;
    }
    const editing = selectedStart.shift ?? null;
    popup = node("dialog", "", root);
    popup.className = "shift-popup";
    popup.setAttribute("aria-label", editing ? "Edit shift" : "Add shift");
    const bar = node("div", "", popup);
    bar.className = "shift-popup-bar";
    node("strong", editing ? "Edit shift" : "Add shift", bar);
    action("Close", bar, cancelPopup);
    popupMessage = node("p", "", popup);
    popupMessage.className = "team-status";
    popupMessage.setAttribute("role", "status");
    popup.addEventListener("cancel", event => {
      event.preventDefault();
      cancelPopup();
    });
    popup.addEventListener("click", event => {
      if (event.target !== popup) return;
      const box = popup.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right ||
          event.clientY < box.top || event.clientY > box.bottom) {
        cancelPopup();
      }
    });
    const form = node("form", "", popup);
    inputFields = node("fieldset", "", form);
    node("legend", editing ? "Edit shift" : "Add shift", inputFields);
    if (editing) {
      node("p", `Saved shift: ${editing.role_name}, ` +
        `${editing.local_start.slice(0, 16).replace("T", " ")} to ` +
        `${editing.local_end.slice(0, 16).replace("T", " ")}.`, inputFields);
    }
    const activeRoles = roles.filter(role => role.active);
    if (!activeRoles.length && !editing) {
      node("p", "Add an active job role in Team management before creating shifts.", inputFields);
      inputFields.disabled = true;
      presentPopup();
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
    if (editing) {
      if (!activeRoles.some(role => role.id === editing.role_id)) {
        const inactive = node("option",
          editing.role_name + " (inactive — choose an active role to save)", roleSelect);
        inactive.value = editing.role_id;
        inactive.disabled = true;
      }
      roleSelect.value = editing.role_id;
    }

    const dayLabel = node("label", "Starting business day", layout);
    const daySelect = node("select", "", dayLabel);
    daySelect.required = true;
    for (let offset = 0; offset < 7; offset++) {
      const day = addDays(week.week_start_date, offset);
      node("option", `${names[weekday(day)]} ${day}`, daySelect).value = day;
    }
    daySelect.value = businessDateForLocal(
      `${selectedStart.day}T${clockMinute(selectedStart.minute)}:00`, rollover
    );
    const start = field("Start time (24-hour HH:MM)", "time", layout, clockMinute(selectedStart.minute));
    const defaultEnd = editing
      ? Number(editing.local_end.slice(11, 13)) * 60 +
        Number(editing.local_end.slice(14, 16))
      : (selectedStart.minute + 60) % 1440;
    const end = field("End time (24-hour HH:MM)", "time", layout, clockMinute(defaultEnd));

    const calendarNotice = node("p", "", inputFields);
    const updateCalendarNotice = () => {
      if (!start.value || !daySelect.value) return;
      const minute = Number(start.value.slice(0, 2)) * 60 +
        Number(start.value.slice(3));
      const actualDay = calendarDateForCell(daySelect.value, minute, rollover);
      calendarNotice.textContent = `Actual start: ${actualDay} at ${start.value}.`;
    };
    start.addEventListener("change", updateCalendarNotice);
    daySelect.addEventListener("change", updateCalendarNotice);
    updateCalendarNotice();

    const nextLabel = node("label", "Ends next calendar day", inputFields);
    const next = node("input", "", nextLabel);
    next.type = "checkbox";
    next.checked = editing
      ? editing.local_end.slice(0, 10) !== editing.local_start.slice(0, 10)
      : selectedStart.minute + 60 >= 1440;
    node("p", "Ends next calendar day means the date after the actual start date shown above.", inputFields);
    const noteLabel = node("label", "Shift note (optional)", inputFields);
    const note = node("textarea", "", noteLabel);
    note.maxLength = 500;
    note.rows = 2;
    note.value = editing?.note ?? "";

    const save = node("button", editing ? "Save changes" : "Add shift", inputFields);
    save.disabled = !activeRoles.length;
    save.type = "submit";
    save.className = "button secondary";
    action("Cancel", inputFields, () => {
      if (!canClose()) return;
      selectedStart = null;
      dirty = false;
      render();
      say("Shift entry cancelled.");
    });
    if (editing) {
      const remove = action("Delete shift", inputFields, () => {
        if (busy || needsRefresh) return;
        const description = `${editing.role_name}, ` +
          `${editing.local_start.slice(0, 16).replace("T", " ")} to ` +
          `${editing.local_end.slice(0, 16).replace("T", " ")}`;
        if (!window.confirm(
          `Delete this saved shift?\n${description}\n` +
          (dirty ? "Your unsaved changes will also be discarded." : "")
        )) return;
        task(async current => {
          const result = await request(
            endpoint() + "/shifts/" + encodeURIComponent(editing.id) + "/remove",
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-CSRF-Token": identity.csrf_token
              },
              body: JSON.stringify({
                expected_week_revision: data.week.revision,
                expected_shift_revision: editing.revision
              })
            }, current
          );
          if (current !== generation) return;
          await load(current);
          say("Shift deleted from the planner.");
        });
      });
      remove.classList.add("shift-delete");
    }
    form.addEventListener("input", () => {
      dirty = true;
      say("Unsaved shift.");
    });

    form.addEventListener("submit", event => {
      event.preventDefault();
      if (busy || needsRefresh || !form.reportValidity()) return;
      if (!activeRoles.some(role => role.id === roleSelect.value)) {
        say("Choose an active job role before saving.");
        return;
      }
      const minute = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
      const startMinute = minute(start.value), endMinute = minute(end.value);
      if (!next.checked && endMinute <= startMinute) {
        say("End must follow start. Select Ends next day for an overnight shift.");
        return;
      }
      const body = {
        role_id: roleSelect.value,
        local_date: calendarDateForCell(daySelect.value, startMinute, rollover),
        start_minute: startMinute,
        end_minute: endMinute,
        ends_next_day: next.checked,
        expected_week_revision: data.week.revision,
        note: note.value
      };
      if (editing) body.expected_shift_revision = editing.revision;
      task(async current => {
        const path = endpoint() + "/shifts" +
          (editing ? "/" + encodeURIComponent(editing.id) : "");
        const result = await request(path, {
          method: editing ? "PATCH" : "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": identity.csrf_token
          },
          body: JSON.stringify(body)
        }, current);
        if (current !== generation) return;
        await load(current);
        say(editing ? "Shift changes saved." : "Shift added.");
      });
    });
    presentPopup();
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
      window.removeEventListener("resize", positionPopup);
      reset();
    }
  };
}
