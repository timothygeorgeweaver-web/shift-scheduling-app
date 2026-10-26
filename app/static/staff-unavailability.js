import {
  node, action, addDays, weekday, monthHeading, createPlannerGrid
} from "/static/planner-grid.js?v=3";
import {
  projectBusinessPlanner, businessPlannerChanges
} from "/static/unavailability-business-day.js?v=2";
import {confirmAction} from "/static/confirm-dialog.js?v=1";

export function openStaffUnavailability({
  parent, employee, locationId, api, identity, expired, onClose, isActive
}) {
  const panel = node("section", "", parent);
  panel.className = "team unavailability staff-unavailability";
  node("h2", "Edit Unavailability — " + employee.display_name, panel);
  node("p", "You are editing this employee’s saved unavailability. White means available; × means unavailable. Locked dates and published cells cannot be changed.", panel);
  const status = node("p", "Loading employee planner…", panel);
  status.className = "team-status";
  status.setAttribute("role", "status");
  const controls = node("fieldset", "", panel);
  const toolbar = node("div", "", controls);
  toolbar.className = "team-controls";
  const view = node("div", "", controls);
  const base = "/api/v1/staff/locations/" + encodeURIComponent(locationId) +
    "/employees/" + encodeURIComponent(employee.id) + "/business-planner";
  let data = null, draft = {}, first = "", grid = null;
  let busy = false, closed = false, blocked = false, hidden = false;
  const alive = () => !closed && isActive();
  const say = text => { if (alive()) status.textContent = text; };
  const dirty = () => data && data.days.some(day =>
    JSON.stringify(draft[day.date]) !== JSON.stringify(day.effective_cells)
  );
  async function request(url, options = {}) {
    const response = await api(url, options);
    if (!alive()) throw new Error("Employee view changed.");
    if (response.status === 401) {
      expired();
      throw new Error("Please sign in again.");
    }
    if (!response.ok) {
      const messages = {
        404: "This employee planner is not available to your account.",
        409: "Data or policy changed. Refresh the planner before saving.",
        423: "A changed date or cell is locked. Refresh the planner.",
        422: "Check the dates and selected cells."
      };
      throw new Error(messages[response.status] ||
        "Save was not confirmed. Refresh the planner before trying again.");
    }
    return response.json();
  }
  async function task(work) {
    if (busy || !alive()) return;
    busy = true;
    controls.disabled = true;
    close.disabled = true;
    grid?.setEnabled(false);
    try { await work(); }
    catch (error) { say(error.message); }
    finally {
      busy = false;
      if (alive()) {
        controls.disabled = false;
        close.disabled = false;
        grid?.setEnabled(!blocked);
      }
    }
  }
  function destroy() {
    if (closed) return;
    closed = true;
    grid?.destroy();
    window.removeEventListener("beforeunload", unload);
    panel.remove();
    onClose();
  }
  async function discardOK() {
    return !dirty() || await confirmAction("Discard unsaved employee unavailability?");
  }
  const close = action("Close employee planner", toolbar, async () => {
    if (!busy && await discardOK()) destroy();
  });
  action("Refresh planner", toolbar, async () => {
    if (await discardOK()) task(load);
  });
  const unload = event => {
    if (dirty()) { event.preventDefault(); event.returnValue = ""; }
  };
  window.addEventListener("beforeunload", unload);
  function setData(result) {
    data = projectBusinessPlanner(result);
    draft = Object.fromEntries(data.days.map(day => [
      day.date, [...day.effective_cells]
    ]));
    blocked = false;
  }
  async function load() {
    const result = await request(base + "?start=" + first);
    if (!alive()) return;
    setData(result);
    draw();
    say("Editing " + employee.display_name + " · " + data.profile.timezone);
  }
  function draw() {
    grid?.destroy();
    view.replaceChildren();
    const navigation = node("div", "", view);
    navigation.className = "planner-navigation";
    async function move(amount) {
      if (!await discardOK()) return;
      const candidate = addDays(first, amount);
      if (candidate < "2000-01-01" || candidate > "9998-12-24") return;
      first = candidate;
      task(load);
    }
    action("← Previous week", navigation, () => move(-7));
    node("h3", monthHeading(first), navigation);
    action("Next week →", navigation, () => move(7));
    const dateLabel = node("label", "Week containing", view);
    const date = node("input", "", dateLabel);
    date.type = "date";
    date.min = "2000-01-01";
    date.max = "9998-12-24";
    date.value = first;
    action("Go to week", view, async () => {
      if (!date.reportValidity() || !date.value || !await discardOK()) return;
      const day = addDays(date.value,
        -((weekday(date.value) - data.profile.week_start + 7) % 7));
      if (day < "2000-01-01" || day > "9998-12-24") return;
      first = day;
      task(load);
    });
    const area = node("div", "", view);
    area.className = "unavailability-planner-area";
    area.hidden = hidden;
    const toggle = action(hidden ? "Show planner" : "Hide planner", view, () => {
      hidden = !hidden;
      area.hidden = hidden;
      toggle.textContent = hidden ? "Show planner" : "Hide planner";
      toggle.setAttribute("aria-expanded", String(!hidden));
    });
    toggle.setAttribute("aria-expanded", String(!hidden));
    const save = action("Save employee unavailability", view, () => {
      if (busy || blocked) return;
      let changes;
      try { changes = businessPlannerChanges(data, draft); }
      catch (error) { say(error.message); return; }
      if (!changes.length) return;
      task(async () => {
        try {
          const result = await request(base, {
            method: "PUT",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": identity.csrf_token
            },
            body: JSON.stringify({
              first_date: first,
              expected_location_revision: data.profile.location_revision,
              changes
            })
          });
          if (!alive()) return;
          setData(result);
          draw();
          say("Unavailability saved for " + employee.display_name + ".");
        } catch (error) {
          blocked = true;
          save.disabled = true;
          throw error;
        }
      });
    });
    save.disabled = blocked || !dirty();
    const days = data.days.map(day => ({
      ...day, effective_cells: draft[day.date]
    }));
    grid = createPlannerGrid(area, days, (index, cells) => {
      draft[data.days[index].date] = cells;
      save.disabled = blocked || !dirty();
      say("Unsaved changes for " + employee.display_name + ".");
    }, 0, {rolloverMinute: data.profile.business_day_start_minute});
    grid.setEnabled(!blocked);
  }

  const today = new Date().toISOString().slice(0, 10);
  first = addDays(today, 21);
  task(async () => {
    const initial = await request(base + "?start=" + first);
    if (!alive()) return;
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: initial.profile.timezone,
      year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const value = type => parts.find(part => part.type === type).value;
    const local = `${value("year")}-${value("month")}-${value("day")}`;
    const candidate = addDays(local,
      Math.max(14, initial.profile.cutoff_days_before + 7));
    first = addDays(candidate,
      -((weekday(candidate) - initial.profile.week_start + 7) % 7));
    await load();
  });
  panel.scrollIntoView({block: "start"});
  return destroy;
}
