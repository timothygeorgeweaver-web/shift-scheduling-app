import {confirmAction} from "/static/confirm-dialog.js?v=1";
import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {compareShifts, clockMinute} from "/static/schedule-grid.js?v=6";
import {businessDateForLocal} from "/static/business-day.js?v=1";
import {createAssignmentControls} from "/static/assignment-controls.js?v=3";

const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const startMinute = shift =>
  Number(shift.local_start.slice(11, 13)) * 60 +
  Number(shift.local_start.slice(14, 16));

export function assignmentStartRows(slots, rollover) {
  return [...new Set(slots.map(startMinute))].sort((a, b) =>
    ((a - rollover + 1440) % 1440) - ((b - rollover + 1440) % 1440)
  );
}

export function createAssignmentEditor({api, expired, identity, parent, onOpen}) {
  const root = node("section", "", parent);
  root.className = "shift-editor assignment-editor";
  root.hidden = true;
  root.setAttribute("aria-label", "Assign shifts");

  let generation = 0, busy = false, dirty = false, needsRefresh = false;
  let location = "", weekId = "", data = null, roles = [];
  let status, controls, content, popup = null, anchorBox = null;
  let popupObserver = null;

  function say(text) {
    if (status) status.textContent = text;
    const message = popup?.querySelector(".assignment-popup-status");
    if (message) message.textContent = text;
  }
  function dismiss() {
    popupObserver?.disconnect();
    popupObserver = null;
    if (popup?.open) popup.close();
    popup?.remove();
    popup = null;
  }
  function reset() {
    generation++;
    dismiss();
    busy = dirty = needsRefresh = false;
    data = null;
    root.replaceChildren();
    root.hidden = true;
    onOpen(false);
  }
  async function canClose() {
    return !busy && (!dirty ||
      await confirmAction("Discard unsaved employee assignment changes?"));
  }
  function endpoint() {
    return "/api/v1/staff/locations/" + encodeURIComponent(location) +
      "/schedule-weeks/" + encodeURIComponent(weekId);
  }
  async function request(path, options = {}, current = generation) {
    const response = await api(path, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": identity.csrf_token,
        ...(options.headers ?? {})
      }
    });
    if (current !== generation) throw new Error("View changed.");
    if (response.status === 401) {
      expired();
      throw new Error("Please sign in again.");
    }
    const result = await response.json().catch(() => null);
    if (current !== generation) throw new Error("View changed.");
    if (!response.ok) {
      const error = new Error(
        typeof result?.detail === "string" ? result.detail :
          "The request was not confirmed. Refresh the assigner before trying again."
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
    controls.disabled = true;
    root.setAttribute("aria-busy", "true");
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
        controls.disabled = false;
        root.setAttribute("aria-busy", "false");
      }
    }
  }
  function positionPopup() {
    if (!popup?.open) return;
    const viewport = window.visualViewport;
    const width = viewport?.width ?? window.innerWidth;
    const height = viewport?.height ?? window.innerHeight;
    const originLeft = viewport?.offsetLeft ?? 0;
    const originTop = viewport?.offsetTop ?? 0;
    const margin = 16, gap = 10;

    popup.style.boxSizing = "border-box";
    popup.style.maxWidth = Math.max(1, width - margin * 2) + "px";
    popup.style.maxHeight = Math.max(1, height - margin * 2) + "px";
    popup.style.overflowY = "auto";

    const box = popup.getBoundingClientRect();
    const minimumLeft = originLeft + margin;
    const minimumTop = originTop + margin;
    const maximumLeft = Math.max(minimumLeft,
      originLeft + width - box.width - margin);
    const maximumTop = Math.max(minimumTop,
      originTop + height - box.height - margin);

    let left = originLeft + (width - box.width) / 2;
    let top = originTop + (height - box.height) / 2;
    if (anchorBox && width >= 640) {
      if (anchorBox.right + gap + box.width <= originLeft + width - margin) {
        left = anchorBox.right + gap;
      } else if (anchorBox.left - gap - box.width >= minimumLeft) {
        left = anchorBox.left - gap - box.width;
      }
      top = anchorBox.top;
    }
    popup.style.left = Math.max(minimumLeft, Math.min(left, maximumLeft)) + "px";
    popup.style.top = Math.max(minimumTop, Math.min(top, maximumTop)) + "px";
  }
  async function closePopup() {
    if (!(await canClose())) return;
    dirty = false;
    dismiss();
  }
  async function choose(shift, card) {
    if (busy || needsRefresh || !(await canClose())) return;
    dirty = false;
    dismiss();
    anchorBox = card.getBoundingClientRect();
    popup = node("dialog", "", root);
    popup.className = "shift-popup";
    popup.setAttribute("aria-label", "Assign employee");
    const bar = node("div", "", popup);
    bar.className = "shift-popup-bar";
    node("strong", shift.role_name, bar);
    action("Close", bar, closePopup);
    node("p",
      `${shift.local_start.slice(0, 16).replace("T", " ")} to ` +
      `${shift.local_end.slice(0, 16).replace("T", " ")}`, popup);
    const message = node("p", "", popup);
    message.className = "assignment-popup-status team-status";
    message.setAttribute("role", "status");

    const assignmentControls = createAssignmentControls({
      parent: popup,
      endpoint: endpoint() + "/shifts/" + encodeURIComponent(shift.id),
      shift,
      assignment: (data.assignments ?? []).find(a => a.slot_id === shift.id) ?? null,
      weekRevision: data.week.revision,
      locationRevision: data.location_revision,
      request,
      run: task,
      canEdit: () => !busy && !needsRefresh,
      markDirty: () => { dirty = true; },
      onSaved: async current => {
        dirty = false;
        await load(current);
      },
      say
    });
    popup.addEventListener("cancel", event => {
      event.preventDefault();
      closePopup();
    });
    popup.showModal();
    assignmentControls.start();
    popupObserver = new ResizeObserver(positionPopup);
    popupObserver.observe(popup);
    positionPopup();
    popup.querySelector("button")?.focus({preventScroll: true});
    requestAnimationFrame(positionPopup);
  }
  function render() {
    dismiss();
    content.replaceChildren();
    const week = data.week;
    const rollover = week.business_day_start_minute;
    const assignments = new Map((data.assignments ?? []).map(a => [a.slot_id, a]));
    node("h3", `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, content);
    node("p",
      `${data.slots.length} shifts · ${assignments.size} assigned · ` +
      `${data.slots.length - assignments.size} unassigned`, content);
    node("p",
      "Only start times containing shifts are shown. Click a shift to assign an employee. " +
      `Business days begin at ${clockMinute(rollover)}.`, content);

    if (!data.slots.length) {
      node("p", "This week has no shifts. Close the assigner and open the Schedule Planner to add them.", content);
      return;
    }

    const days = Array.from({length: 7}, (_, n) => addDays(week.week_start_date, n));
    const roleMap = new Map(roles.map(role => [role.id, role]));
    const grouped = new Map();
    for (const shift of data.slots) {
      const day = businessDateForLocal(shift.local_start, rollover);
      if (!days.includes(day)) throw new Error("A shift falls outside the displayed business week.");
      const key = day + "/" + startMinute(shift);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(shift);
    }
    for (const shifts of grouped.values()) {
      shifts.sort((a, b) => compareShifts(a, b, roleMap));
    }

    const grid = node("div", "", content);
    grid.className = "schedule-time-grid assignment-time-grid";
    node("strong", "Start", grid).className = "schedule-grid-corner";
    for (const day of days) {
      const header = node("div", "", grid);
      header.className = "schedule-grid-heading";
      node("strong", names[weekday(day)], header);
      node("span", day, header);
    }

    for (const minute of assignmentStartRows(data.slots, rollover)) {
      const label = node("div", clockMinute(minute), grid);
      label.className = "schedule-grid-time";
      if (minute < rollover) node("small", "Next Day", label);
      for (const day of days) {
        const cell = node("div", "", grid);
        cell.className = "schedule-start-cell";
        const shifts = grouped.get(day + "/" + minute) ?? [];
        if (!shifts.length) node("span", "—", cell).className = "assignment-empty";
        for (const shift of shifts) {
          const assigned = assignments.get(shift.id);
          const card = node("button", "", cell);
          card.type = "button";
          card.className = "schedule-start-card assignment-card";
          node("strong", shift.role_name, card);
          node("span", assigned?.display_name ?? "Unassigned", card);
          card.classList.toggle("assignment-unassigned", !assigned);
          card.setAttribute("aria-label",
            `${shift.role_name}, ${day}, ${clockMinute(minute)}, ` +
            `${assigned?.display_name ?? "Unassigned"}`);
          card.addEventListener("click", () => choose(shift, card));
        }
      }
    }
  }
  async function load(current) {
    say("Loading assignment week…");
    const [week, roleList] = await Promise.all([
      request(endpoint(), {}, current),
      request("/api/v1/staff/locations/" + encodeURIComponent(location) + "/roles", {}, current)
    ]);
    if (current !== generation) return;
    if (week.week.state !== "draft") {
      throw new Error("Only draft weeks can be assigned.");
    }
    data = week;
    roles = roleList;
    dirty = needsRefresh = false;
    render();
    say("Choose a shift to assign an employee.");
  }
  async function open(targetLocation, targetWeek) {
    if (!root.hidden) return;
    reset();
    location = targetLocation;
    weekId = targetWeek;
    root.hidden = false;
    onOpen(true);
    const heading = node("h3", "Assign Shifts", root);
    heading.tabIndex = -1;
    status = node("p", "", root);
    status.className = "team-status";
    status.setAttribute("role", "status");
    controls = node("fieldset", "", root);
    node("legend", "Assignment controls", controls);
    action("Refresh assigner", controls, async () => {
      if ((await canClose())) task(load);
    });
    action("Close assigner", controls, async () => {
      if ((await canClose())) reset();
    });
    content = node("div", "", root);
    heading.focus();
    root.scrollIntoView({block: "start", behavior: "smooth"});
    await task(load);
  }

  const unload = event => {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", unload);
  window.addEventListener("resize", positionPopup);
  window.visualViewport?.addEventListener("resize", positionPopup);
  window.visualViewport?.addEventListener("scroll", positionPopup);
  return {
    open, reset,
    destroy() {
      window.removeEventListener("beforeunload", unload);
      window.removeEventListener("resize", positionPopup);
      window.visualViewport?.removeEventListener("resize", positionPopup);
      window.visualViewport?.removeEventListener("scroll", positionPopup);
      reset();
    }
  };
}
