export function createUnavailabilityManager({api, expired}) {
  let root = null, epoch = 0;
  const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  function el(tag, text = "", parent) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (parent) parent.append(node);
    return node;
  }

  function button(text, parent, run) {
    const node = el("button", text, parent);
    node.type = "button";
    node.className = "button secondary";
    node.addEventListener("click", run);
    return node;
  }

  function field(text, type, parent, value = "") {
    const label = el("label", text, parent);
    const input = el("input", "", label);
    input.type = type;
    input.value = value;
    input.required = true;
    return input;
  }

  function addDays(value, count) {
    const d = new Date(value + "T12:00:00Z");
    d.setUTCDate(d.getUTCDate() + count);
    return d.toISOString().slice(0, 10);
  }

  function localDate(zone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit"
    }).formatToParts(new Date());
    const value = key => parts.find(p => p.type === key).value;
    return `${value("year")}-${value("month")}-${value("day")}`;
  }

  function clock(minute) {
    return `${Math.floor(minute / 60).toString().padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`;
  }

  function ranges(items) {
    return items.map(([start, end]) => `${clock(start)}–${clock(end)}`).join(", ");
  }

  function reset() {
    epoch++;
    root?.remove();
    root = null;
  }

  async function start(identity) {
    reset();
    const generation = epoch;
    const active = () => generation === epoch;
    const panel = el("section");
    root = panel;
    panel.className = "team unavailability";
    panel.setAttribute("aria-label", "My Unavailability");
    document.getElementById("account-view").append(panel);
    el("h2", "My Unavailability", panel);
    el("p", "Mark times you cannot work. No blocked times means available all day. All times use the selected location’s timezone.", panel);

    const status = el("p", "Loading your employee profiles…", panel);
    status.className = "team-status";
    status.setAttribute("role", "status");

    const controls = el("fieldset", "", panel);
    el("legend", "Calendar", controls);
    const locationLabel = el("label", "Workplace", controls);
    const select = el("select", "", locationLabel);
    const startDate = field("First date", "date", controls);
    startDate.min = "2000-01-08";
    startDate.max = "9998-12-24";
    const actions = el("div", "", controls);
    actions.className = "team-controls";
    const calendar = el("div", "", panel);
    const editor = el("div", "", panel);

    let profiles = [], data = null, editing = false, busy = false;
    const say = text => { if (active()) status.textContent = text; };
    const profile = () => profiles.find(p => p.location_id === select.value);
    const url = location => "/api/v1/me/unavailability/" + encodeURIComponent(location);

    function clearEditor() {
      editing = false;
      editor.replaceChildren();
      controls.disabled = busy;
      calendar.querySelectorAll("button").forEach(b => { b.disabled = busy; });
      if (data && !data.effective_days[0].editable) {
        const patternButton = calendar.querySelector("button");
        if (patternButton) patternButton.disabled = true;
      }
    }

    async function request(path, options) {
      const response = await api(path, options);
      if (!active()) throw new Error("View changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      return response;
    }

    function weekDefault() {
      const p = profile();
      const day = addDays(localDate(p.timezone), Math.max(14, p.cutoff_days_before + 7));
      const weekday = (new Date(day + "T12:00:00Z").getUTCDay() + 6) % 7;
      startDate.value = addDays(day, -(weekday - p.week_start + 7) % 7);
    }

    async function load() {
      if (busy || editing || !startDate.reportValidity()) return;
      busy = true;
      controls.disabled = true;
      calendar.replaceChildren();
      data = null;
      say("Loading calendar…");
      try {
        const response = await request(`${url(select.value)}?start=${startDate.value}&days=7`);
        if (!response.ok) throw new Error("Calendar unavailable. Refresh or sign in again.");
        const loaded = await response.json();
        if (!active()) return;
        data = loaded;
        render();
        say(`Times shown in ${data.profile.timezone}.`);
      } catch (error) {
        say(error.message);
      } finally {
        busy = false;
        if (active()) controls.disabled = editing;
      }
    }

    function rows(parent, initial) {
      const list = el("div", "", parent);

      function add(window = {start: 540, end: 1020, ends_next_day: false}) {
        if (list.children.length >= 32) {
          say("Use at most 32 intervals per day.");
          return;
        }
        const row = el("div", "", list);
        row.className = "unavailability-row";
        const start = field("Start", "time", row, clock(window.start));
        start.step = "60";
        const end = field("End (HH:MM; 24:00 = midnight)", "text", row, clock(window.end));
        end.pattern = "(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00";
        end.maxLength = 5;
        const label = el("label", "Ends next day", row);
        const overnight = el("input", "", label);
        overnight.type = "checkbox";
        overnight.checked = !!window.ends_next_day;
        button("Remove interval", row, () => row.remove());

        row.read = () => {
          const minute = text => Number(text.slice(0, 2)) * 60 + Number(text.slice(3));
          const beginning = minute(start.value), ending = minute(end.value);
          const duration = ending + (overnight.checked ? 1440 : 0) - beginning;
          if (duration <= 0 || duration > 1440 || (overnight.checked && ending === 1440)) {
            throw new Error("Check interval times. Overnight blocks need Ends next day and cannot exceed 24 hours.");
          }
          return {start: beginning, end: ending, ends_next_day: overnight.checked};
        };
      }

      for (const window of initial) add(window);
      button("Add blocked interval", parent, () => add());
      return () => Array.from(list.children, row => row.read());
    }

    function begin(title) {
      clearEditor();
      editing = true;
      controls.disabled = true;
      calendar.querySelectorAll("button").forEach(b => { b.disabled = true; });
      const form = el("form", "", editor);
      el("h3", title, form);
      el("p", "Use 24-hour times. Leave no intervals to mark all-day availability. Frozen dates keep their saved times.", form);
      const fields = el("fieldset", "", form);
      el("legend", "Blocked times", fields);
      const error = el("p", "", form);
      error.setAttribute("role", "alert");
      error.className = "message";
      const buttons = el("div", "", form);
      buttons.className = "team-controls";
      const submit = el("button", "Save", buttons);
      submit.type = "submit";
      submit.className = "button secondary";
      const cancel = button("Close editor", buttons, () => {
        clearEditor();
        say("Editor closed. Refresh the calendar to see current saved values.");
      });
      form.tabIndex = -1;
      form.focus();
      return {form, fields, error, submit, cancel};
    }

    function connectSave(view, location, readBody) {
      view.form.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || !active()) return;
        let body;
        try {
          body = readBody();
        } catch (error) {
          view.error.textContent = error.message;
          return;
        }

        busy = true;
        view.fields.disabled = true;
        view.submit.disabled = true;
        view.cancel.disabled = true;
        view.error.textContent = "Saving…";
        let saved = false, retry = false;
        try {
          const response = await request(url(location), {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": identity.csrf_token
            },
            body: JSON.stringify(body)
          });
          if (response.ok) {
            saved = true;
          } else {
            retry = response.status === 422;
            view.error.textContent = response.status === 409
              ? "Data or policy changed. Close the editor and refresh before trying again."
              : response.status === 423
              ? "This date is now locked. Close the editor and refresh."
              : response.status === 422
              ? "Check the intervals and date. The cutoff policy may also need administrator review."
              : "Save was not confirmed. Close the editor and refresh before trying again.";
          }
        } catch {
          if (active()) {
            view.error.textContent = "Save was not confirmed. Close the editor and refresh before trying again.";
          }
        } finally {
          busy = false;
          if (active()) {
            view.fields.disabled = false;
            view.cancel.disabled = false;
            view.submit.disabled = !retry;
          }
        }
        if (saved && active()) {
          clearEditor();
          await load();
          if (active() && data) {
            say("Saved. The calendar shows the current effective blocked times.");
          }
        }
      });
    }

    function editDate(day) {
      if (!day.editable || editing || busy) return;
      const snapshot = data;
      const exception = snapshot.exceptions.find(x => x.date === day.date);
      const view = begin(`Date replacement: ${day.date}`);
      const modeLabel = el("label", "For this date", view.fields);
      const mode = el("select", "", modeLabel);

      for (const [value, text] of [
        ["custom", "Custom blocked times"],
        ["available", "Available all day"],
        ["unavailable", "Unavailable all day"],
        ["delete", "Use recurring pattern (remove replacement)"]
      ]) {
        const option = el("option", text, mode);
        option.value = value;
      }
      mode.value = exception?.present
        ? (exception.windows.length ? "custom" : "available") : "custom";

      const intervalFields = el("fieldset", "", view.fields);
      el("legend", "Custom blocked intervals", intervalFields);
      const read = rows(intervalFields, exception?.present ? exception.windows : []);
      const toggle = () => {
        intervalFields.hidden = intervalFields.disabled = mode.value !== "custom";
      };
      mode.addEventListener("change", toggle);
      toggle();

      connectSave(view, snapshot.profile.location_id, () => ({
        operation: mode.value === "delete" ? "delete" : "exception",
        date: day.date,
        expected_revision: exception?.revision || 0,
        expected_location_revision: snapshot.profile.location_revision,
        windows: mode.value === "custom" ? read()
          : mode.value === "unavailable" ? [{start: 0, end: 1440}] : []
      }));
    }

    function editPattern() {
      if (!data || editing || busy) return;
      const snapshot = data;
      const first = snapshot.effective_days[0];
      if (!first.editable) {
        say("Choose a calendar first date that is still editable.");
        return;
      }
      const origins = snapshot.recurring.filter(r => r.origin_date >= first.date);
      if (origins.length !== 7 || new Set(origins.map(r => r.revision)).size !== 1) {
        say("A scheduled pattern change falls inside these seven days. Choose seven days within one recurring version before editing.");
        return;
      }

      const view = begin(`Recurring pattern from ${first.date}`);
      el("p", "This replaces the weekly pattern from this date. Later effective-dated versions still apply on their scheduled dates.", view.fields);
      const readers = {};
      weekdays.forEach((name, weekday) => {
        const group = el("fieldset", "", view.fields);
        el("legend", name, group);
        const origin = origins.find(r =>
          (new Date(r.origin_date + "T12:00:00Z").getUTCDay() + 6) % 7 === weekday
        );
        readers[weekday] = rows(group, origin.windows);
      });

      connectSave(view, snapshot.profile.location_id, () => ({
        operation: "recurring",
        date: first.date,
        expected_revision: snapshot.pattern_revision,
        expected_location_revision: snapshot.profile.location_revision,
        weekly_windows: Object.fromEntries(
          Object.entries(readers).map(([key, read]) => [key, read()])
        )
      }));
    }

    function render() {
      const edit = button("Edit recurring pattern from first date", calendar, editPattern);
      edit.disabled = !data.effective_days[0].editable;
      for (const day of data.effective_days) {
        const card = el("section", "", calendar);
        card.className = "unavailability-day";
        el("h3", day.date, card);
        el("p", day.blocked.length
          ? "Cannot work: " + ranges(day.blocked) : "Available all day", card);
        if (day.blocked.length) {
          el("p", day.available.length
            ? "Available: " + ranges(day.available) : "Unavailable all day", card);
        }
        const labels = {
          frozen: "Frozen snapshot",
          exception: "Date replacement",
          recurring: "Recurring pattern"
        };
        el("p", labels[day.source], card);
        el("p", day.editable
          ? "Editable until " + new Intl.DateTimeFormat("en-US", {
              timeZone: data.profile.timezone,
              dateStyle: "medium", timeStyle: "short"
            }).format(new Date(day.cutoff))
          : "Locked: " + (day.lock_reason || "not editable").replaceAll("_", " "),
          card
        );
        const editDay = button("Edit date replacement", card, () => editDate(day));
        editDay.disabled = !day.editable;
      }
    }

    function move(count) {
      if (!startDate.reportValidity()) return;
      const candidate = addDays(startDate.value, count);
      if (candidate < startDate.min || candidate > startDate.max) {
        say("Choose a date within the supported calendar range.");
        return;
      }
      startDate.value = candidate;
      load();
    }

    button("Previous 7 days", actions, () => move(-7));
    button("Next 7 days", actions, () => move(7));
    button("Refresh calendar", actions, load);
    select.addEventListener("change", () => { weekDefault(); load(); });
    startDate.addEventListener("change", load);

    try {
      controls.disabled = true;
      const response = await request("/api/v1/me/unavailability/profiles");
      if (!response.ok) {
        throw new Error("Employee profiles unavailable. Sign in again or refresh.");
      }
      profiles = await response.json();
      if (!active()) return;
      if (!profiles.length) {
        controls.hidden = true;
        say("No active employee profile is linked to this account. Ask your administrator to check the account link.");
        return;
      }
      for (const p of profiles) {
        const option = el("option",
          `${p.business_name} / ${p.location_name} — ${p.display_name}`, select);
        option.value = p.location_id;
      }
      controls.disabled = false;
      weekDefault();
      await load();
    } catch (error) {
      say(error.message);
    }
  }

  return {start, reset};
}
