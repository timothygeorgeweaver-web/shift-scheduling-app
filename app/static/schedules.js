import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";

export function createScheduleManager({api, expired}) {
  let root = null, epoch = 0;

  function reset() {
    epoch++;
    root?.remove();
    root = null;
  }

  async function start(identity) {
    reset();
    const permitted = identity.memberships.some(m =>
      m.permissions.some(p => p === "administrator" || p === "scheduler")
    );
    if (!permitted) return;
    const generation = epoch;
    const active = () => generation === epoch;

    const panel = node("section");
    root = panel;
    panel.className = "team schedules";
    panel.setAttribute("aria-label", "Schedules");
    document.getElementById("account-view").append(panel);
    node("h2", "Schedules", panel);
    node("p", "Browse schedule weeks or create a blank draft.", panel);

    const status = node("p", "Loading workplaces…", panel);
    status.className = "team-status";
    status.setAttribute("role", "status");
    const controls = node("fieldset", "", panel);
    node("legend", "Schedule list", controls);
    const workplace = node("label", "Workplace", controls);
    const select = node("select", "", workplace);
    const rangeLabel = node("label", "List weeks starting from", controls);
    const rangeStart = node("input", "", rangeLabel);
    rangeStart.type = "date";
    rangeStart.min = "2000-01-01";
    rangeStart.max = "9998-09-01";
    rangeStart.required = true;
    rangeStart.value = addDays(new Date().toISOString().slice(0, 10), -14);

    const list = node("div", "", panel);
    const form = node("form", "", panel);
    const createFields = node("fieldset", "", form);
    node("legend", "Create blank week", createFields);
    const policy = node("p", "", createFields);
    const dateLabel = node("label", "Week start date", createFields);
    const dateInput = node("input", "", dateLabel);
    dateInput.type = "date";
    dateInput.min = "2000-01-01";
    dateInput.max = "9998-12-25";
    dateInput.required = true;
    const create = node("button", "Create blank week", createFields);
    create.type = "submit";
    create.className = "button secondary";

    let busy = false, ready = false, state = null;
    const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    const say = text => { if (active()) status.textContent = text; };
    const url = () => "/api/v1/staff/locations/" +
      encodeURIComponent(select.value) + "/schedule-weeks";

    async function request(path, options = {}) {
      const response = await api(path, options);
      if (!active()) throw new Error("Account changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      if (!response.ok) {
        throw new Error(
          response.status === 409
            ? "A schedule already covers those dates, or workplace settings changed. Refresh the list."
            : response.status === 422
            ? "Check the dates and choose the configured week-start day."
            : response.status === 403 || response.status === 404
            ? "Schedule access is unavailable for this workplace."
            : "The request was not confirmed. Refresh the list before trying again."
        );
      }
      const result = await response.json();
      if (!active()) throw new Error("Account changed.");
      return result;
    }

    async function task(operation) {
      if (busy || !active()) return;
      busy = true;
      controls.disabled = true;
      createFields.disabled = true;
      try {
        await operation();
      } catch (error) {
        ready = false;
        say(error.message);
      } finally {
        busy = false;
        if (active()) {
          controls.disabled = false;
          createFields.disabled = !ready;
        }
      }
    }

    function localToday(zone) {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit"
      }).formatToParts(new Date());
      const get = key => parts.find(p => p.type === key).value;
      return `${get("year")}-${get("month")}-${get("day")}`;
    }

    async function load() {
      if (!rangeStart.reportValidity()) return;
      ready = false;
      list.replaceChildren();
      say("Loading schedule weeks…");
      const result = await request(
        url() + "?start=" + rangeStart.value +
        "&end=" + addDays(rangeStart.value, 90)
      );
      state = result;
      const location = state.location;
      policy.textContent = `Weeks begin ${names[location.week_start_day]}. Times use ${location.timezone}.`;
      if (!dateInput.value) {
        const today = localToday(location.timezone);
        dateInput.value = addDays(
          today, (location.week_start_day - weekday(today) + 7) % 7
        );
      }

      node("h3", "Schedule weeks", list);
      if (!state.weeks.length) {
        node("p", "No schedule weeks in this date range.", list);
      } else {
        const entries = node("ul", "", list);
        for (const week of state.weeks) {
          const item = node("li", "", entries);
          node("strong",
            `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, item);
          node("span", ` · ${week.state.charAt(0).toUpperCase() + week.state.slice(1)}`, item);
        }
      }
      ready = true;
      say(`${state.weeks.length} schedule weeks shown.`);
    }

    action("Refresh list", controls, () => {
      if (rangeStart.reportValidity()) task(load);
    });
    select.addEventListener("change", () => {
      dateInput.value = "";
      task(load);
    });
    rangeStart.addEventListener("change", () => {
      if (rangeStart.reportValidity()) task(load);
    });

    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!ready || busy || !form.reportValidity()) return;
      if (weekday(dateInput.value) !== state.location.week_start_day) {
        say(`Choose a ${names[state.location.week_start_day]} as the week start.`);
        return;
      }
      const selectedDate = dateInput.value;
      const payload = {
        week_start_date: selectedDate,
        expected_location_revision: state.location.revision
      };
      task(async () => {
        ready = false;
        const created = await request(url(), {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": identity.csrf_token
          },
          body: JSON.stringify(payload)
        });
        rangeStart.value = selectedDate > rangeStart.max
          ? rangeStart.max : selectedDate;
        await load();
        say(`Created draft week ${created.week_start_date} – ${addDays(created.week_start_date, 6)}.`);
      });
    });

    controls.disabled = createFields.disabled = true;
    await task(async () => {
      const locations = await request("/api/v1/staff/locations");
      if (!locations.length) {
        controls.hidden = form.hidden = true;
        say("No workplaces are available for scheduling.");
        return;
      }
      for (const location of locations) {
        const option = node("option",
          `${location.organization_name} / ${location.name}`, select);
        option.value = location.id;
      }
      await load();
    });
  }

  return {start, reset};
}
