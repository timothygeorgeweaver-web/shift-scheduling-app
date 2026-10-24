import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {createShiftEditor} from "/static/shift-editor.js?v=9";
import {askTemplateName} from "/static/template-name-dialog.js?v=1";
import {createTemplateEditor} from "/static/template-editor.js?v=1";

export function createScheduleManager({api, expired}) {
  let root = null, epoch = 0, closeShifts = () => {};

  function reset() {
    epoch++;
    closeShifts();
    closeShifts = () => {};
    root?.remove();
    root = null;
  }

  async function start(identity) {
    reset();
    if (!identity.memberships.some(m =>
      m.permissions.some(p => p === "administrator" || p === "scheduler")
    )) return;

    const generation = epoch;
    const active = () => generation === epoch;
    const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    const panel = node("section");
    root = panel;
    panel.className = "team schedules";
    panel.setAttribute("aria-label", "Schedules");
    document.getElementById("account-view").append(panel);
    node("h2", "Schedules", panel);
    node("p", "Select an available week-start date, then create a blank draft.", panel);

    const status = node("p", "Loading workplaces…", panel);
    status.className = "team-status";
    status.setAttribute("role", "status");

    const controls = node("fieldset", "", panel);
    node("legend", "Workplace", controls);
    const workplace = node("label", "Workplace", controls);
    const select = node("select", "", workplace);

    const form = node("form", "", panel);
    const fields = node("fieldset", "", form);
    const formTitle = node("legend", "Create blank week", fields);
    const copyNotice = node("p", "", fields);
    copyNotice.setAttribute("role", "status");
    copyNotice.hidden = true;
    const cancelCopy = action("Cancel copying", fields, () => {
      if (busy) return;
      copySource = null;
      updateCopyMode();
      if (ready) renderCalendar();
      say("Selection cancelled. You can create a blank week.");
    });
    cancelCopy.hidden = true;
    const policy = node("p", "", fields);
    const navigation = node("div", "", fields);
    navigation.className = "planner-navigation";
    const calendar = node("div", "", fields);
    const selection = node("p", "Choose an available week-start date.", fields);
    selection.setAttribute("role", "status");
    const create = node("button", "Create blank week", fields);
    create.type = "submit";
    create.className = "button secondary";
    create.disabled = true;
    const templatesView = node("div", "", panel);
    const list = node("div", "", panel);

    let month = new Date().toISOString().slice(0, 7);
    let state = null, selected = "", busy = false, ready = false;
    let copySource = null;
    function lockOuter(open) {
      if (!active()) return;
      controls.disabled = open || busy;
      fields.disabled = open || busy || !ready;
      [...list.querySelectorAll("button"),
       ...templatesView.querySelectorAll("button")].forEach(button => {
        button.disabled = open || busy;
      });
    }
    const shifts = createShiftEditor({
      api, expired, identity, parent: panel, onOpen: lockOuter
    });
    const templateEditor = createTemplateEditor({
      api, expired, identity, parent: panel, onOpen: lockOuter,
      onSaved: () => task(async () => {
        copySource = null;
        await load();
        say("Template saved.");
      })
    });
    closeShifts = () => {
      shifts.destroy();
      templateEditor.destroy();
    };
    const say = text => { if (active()) status.textContent = text; };
    const url = () => "/api/v1/staff/locations/" +
      encodeURIComponent(select.value) + "/schedule-weeks";

    const templateUrl = () => "/api/v1/staff/locations/" +
      encodeURIComponent(select.value) + "/schedule-templates";

    function updateCopyMode() {
      const template = copySource?.kind === "template";
      formTitle.textContent = template ? "Create week from template" :
        copySource ? "Copy schedule week" : "Create blank week";
      create.textContent = template ? "Create from template" :
        copySource ? "Copy week" : "Create blank week";
      cancelCopy.textContent = template ? "Cancel template selection" : "Cancel copying";
      copyNotice.hidden = cancelCopy.hidden = !copySource;
      copyNotice.textContent = template
        ? `Using template: ${copySource.name} (${copySource.shift_count} shifts). ` +
          "Choose an unused destination week. All shifts will be unassigned."
        : copySource
        ? `Copying ${copySource.week_start_date} – ${addDays(copySource.week_start_date, 6)}. ` +
          "Choose an unused destination week. All copied shifts will be unassigned."
        : "";
    }

    function renderTemplates(templates) {
      templatesView.replaceChildren();
      node("h3", "Schedule templates", templatesView);
      action("Create New Template", templatesView, () => editTemplate());
      if (!templates.length) {
        node("p", "No templates yet. Use Save as template beside an existing week.",
          templatesView);
        return;
      }
      const entries = node("ul", "", templatesView);
      for (const template of templates) {
        const item = node("li", "", entries);
        node("strong", template.name, item);
        node("span", ` · ${template.shift_count} shifts`, item);
        action("Edit Template", item, () => editTemplate(template));
        action("Archive template", item, () => archiveTemplate(template));
        action("Use template", item, () => {
          if (busy || !ready) return;
          copySource = {...template, kind: "template"};
          renderCalendar();
          updateCopyMode();
          say("Choose an unused destination date, then click Create from template.");
          fields.scrollIntoView({block: "start", behavior: "smooth"});
        });
      }
    }

    function editTemplate(template = null) {
      if (busy || !ready) return;
      copySource = null;
      updateCopyMode();
      shifts.reset();
      templateEditor.open({
        locationId: select.value,
        locationRevision: state.location.revision,
        templateId: template?.id ?? null
      });
    }

    function archiveTemplate(template) {
      if (busy || !ready || !window.confirm(
        `Archive "${template.name}"? Existing schedule weeks stay unchanged.`
      )) return;
      task(async () => {
        await request(templateUrl() + "/" + encodeURIComponent(template.id), {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": identity.csrf_token
          },
          body: JSON.stringify({
            name: template.name,
            active: false,
            expected_template_revision: template.revision
          })
        });
        copySource = null;
        await load();
        say("Template archived.");
      });
    }

    async function saveAsTemplate(week) {
      if (busy || !ready) return;
      const name = await askTemplateName(panel, week);
      if (name === null || !active()) return;
      task(async () => {
        try {
          await request(url() + "/" + encodeURIComponent(week.id) + "/templates", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": identity.csrf_token
            },
            body: JSON.stringify({
              name, expected_source_revision: week.revision
            })
          });
          await load();
          say(`Saved template: ${name}.`);
        } catch (error) {
          if (error.status === 409) {
            await load();
            say(error.message + " Refresh complete; choose another name or reselect the week.");
            return;
          }
          throw error;
        }
      });
    }

    function adjacentMonth(value, count) {
      const date = new Date(value + "-01T12:00:00Z");
      date.setUTCMonth(date.getUTCMonth() + count);
      return date.toISOString().slice(0, 7);
    }

    function boundaries() {
      return {
        first: month + "-01",
        last: addDays(adjacentMonth(month, 1) + "-01", -1)
      };
    }

    function coveringWeek(day) {
      return state.weeks.find(week =>
        week.week_start_date <= addDays(day, 6) &&
        addDays(week.week_start_date, 6) >= day
      );
    }

    function validSelection() {
      return ready && selected &&
        weekday(selected) === state.location.week_start_day &&
        !coveringWeek(selected);
    }

    async function request(path, options = {}) {
      const response = await api(path, options);
      if (!active()) throw new Error("Account changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      if (!response.ok) {
        const failure = await response.json().catch(() => null);
        const error = new Error(
          response.status === 409
            ? (typeof failure?.detail === "string" ? failure.detail :
               "The source, destination or workplace settings changed.")
            : response.status === 422
            ? (typeof failure?.detail === "string" ? failure.detail :
               "Check the selected week and workplace settings.")
            : response.status === 403 || response.status === 404
            ? "Schedule access is unavailable for this workplace."
            : "The request was not confirmed. Refresh the calendar before trying again."
        );
        error.status = response.status;
        throw error;
      }
      const result = await response.json();
      if (!active()) throw new Error("Account changed.");
      return result;
    }

    async function task(operation) {
      if (busy || !active()) return;
      busy = true;
      panel.setAttribute("aria-busy", "true");
      controls.disabled = fields.disabled = true;
      try {
        await operation();
      } catch (error) {
        ready = false;
        selected = "";
        selection.textContent = "Refresh the calendar before choosing a week.";
        say(error.message);
      } finally {
        busy = false;
        if (active()) {
          panel.setAttribute("aria-busy", "false");
          controls.disabled = false;
          fields.disabled = !ready;
          create.disabled = !validSelection();
        }
      }
    }

    function renderCalendar() {
      calendar.replaceChildren();
      selected = "";
      create.disabled = true;
      selection.textContent = "Choose an available week-start date.";
      const {first, last} = boundaries();
      const grid = node("div", "", calendar);
      grid.className = "week-picker";
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", "Choose a schedule week");

      for (const name of names) {
        node("strong", name.slice(0, 3), grid).className = "week-picker-heading";
      }
      for (let n = 0; n < weekday(first); n++) {
        node("span", "", grid).setAttribute("aria-hidden", "true");
      }

      const buttons = [];
      for (let day = first; day <= last; day = addDays(day, 1)) {
        const button = node("button", "", grid);
        button.type = "button";
        button.className = "week-picker-day";
        node("span", String(Number(day.slice(-2))), button);

        const correctDay = weekday(day) === state.location.week_start_day;
        const covered = correctDay ? coveringWeek(day) : null;
        const supported = day <= "9998-12-25";
        button.disabled = !correctDay || !!covered || !supported;
        button.setAttribute("aria-pressed", "false");

        let explanation;
        if (!supported) explanation = "Outside supported schedule dates";
        else if (!correctDay) explanation = `Weeks begin ${names[state.location.week_start_day]}`;
        else if (covered) {
          explanation = covered.state === "draft" ? "Draft exists" : "Schedule exists";
          node("small", explanation, button);
          button.classList.add("week-picker-covered");
        } else explanation = "Available week";

        button.title = `${day}: ${explanation}`;
        button.setAttribute("aria-label", `${day}: ${explanation}`);
        button.addEventListener("click", () => {
          if (!ready || busy || button.disabled) return;
          selected = day;
          for (const other of buttons) other.setAttribute("aria-pressed", "false");
          button.setAttribute("aria-pressed", "true");
          selection.textContent = `Selected week: ${day} – ${addDays(day, 6)}`;
          create.disabled = !validSelection();
        });
        buttons.push(button);
      }
    }

    function renderNavigation() {
      navigation.replaceChildren();
      const previous = action("← Previous month", navigation, () => moveMonth(-1));
      previous.setAttribute("aria-label", "Previous month");
      previous.disabled = month === "2000-01";
      node("h3", new Intl.DateTimeFormat("en-US", {
        month: "long", year: "numeric", timeZone: "UTC"
      }).format(new Date(month + "-01T12:00:00Z")), navigation);
      const next = action("Next month →", navigation, () => moveMonth(1));
      next.setAttribute("aria-label", "Next month");
      next.disabled = month === "9998-12";
    }

    function moveMonth(count) {
      const candidate = adjacentMonth(month, count);
      if (candidate < "2000-01" || candidate > "9998-12") return;
      month = candidate;
      task(load);
    }

    async function load() {
      shifts.reset();
      templateEditor.reset();
      ready = false;
      selected = "";
      create.disabled = true;
      calendar.replaceChildren();
      list.replaceChildren();
      templatesView.replaceChildren();
      say("Loading schedule calendar…");
      renderNavigation();

      const {first, last} = boundaries();
      const rangeStart = addDays(first, -6) < "2000-01-01"
        ? "2000-01-01" : addDays(first, -6);
      const rangeEnd = addDays(last, 6) > "9998-12-31"
        ? "9998-12-31" : addDays(last, 6);
      const [scheduleState, templates] = await Promise.all([
        request(url() + "?start=" + rangeStart + "&end=" + rangeEnd),
        request(templateUrl())
      ]);
      state = scheduleState;
      renderTemplates(templates);

      policy.textContent = `Weeks begin ${names[state.location.week_start_day]}. Times use ${state.location.timezone}. Dates with an existing schedule cannot be selected.`;
      ready = true;
      renderCalendar();
      updateCopyMode();

      const visible = state.weeks.filter(week =>
        week.week_start_date <= last && addDays(week.week_start_date, 6) >= first
      );
      node("h3", "Schedule weeks", list);
      if (!visible.length) {
        node("p", "No schedule weeks overlap this month.", list);
      } else {
        const entries = node("ul", "", list);
        for (const week of visible) {
          const item = node("li", "", entries);
          node("strong", `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, item);
          node("span", ` · ${week.state.charAt(0).toUpperCase() + week.state.slice(1)}`, item);
          action("Save as template", item, () => saveAsTemplate(week));
          action("Open week", item, () => {
            if (!busy) shifts.open(select.value, week.id);
          });
          action("Copy this week", item, () => {
            if (busy || !ready) return;
            copySource = {...week};
            renderCalendar();
            updateCopyMode();
            say("Choose an available destination date, then click Copy week.");
            fields.scrollIntoView({block: "start", behavior: "smooth"});
          });
        }
      }
      say("Choose an available week-start date.");
    }

    action("Refresh calendar", controls, () => task(load));
    select.addEventListener("change", () => {
      copySource = null;
      updateCopyMode();
      task(load);
    });

    form.addEventListener("submit", event => {
      event.preventDefault();
      if (busy || !validSelection()) return;
      const chosen = selected;
      const selectedSource = copySource ? {...copySource} : null;
      const isTemplate = selectedSource?.kind === "template";
      const description = isTemplate ? `template "${selectedSource.name}"` :
        selectedSource ? `week ${selectedSource.week_start_date} – ` +
          addDays(selectedSource.week_start_date, 6) : "";
      if (selectedSource && !window.confirm(
        `Create ${chosen} – ${addDays(chosen, 6)} from ${description}?\n` +
        "This creates a new draft with unassigned shifts. The source stays unchanged."
      )) return;

      const payload = {
        week_start_date: chosen,
        expected_location_revision: state.location.revision,
        ...(isTemplate ? {expected_template_revision: selectedSource.revision} :
          selectedSource ? {expected_source_revision: selectedSource.revision} : {})
      };
      const destination = isTemplate
        ? templateUrl() + "/" + encodeURIComponent(selectedSource.id) + "/apply"
        : url() + (selectedSource
          ? "/" + encodeURIComponent(selectedSource.id) + "/copy" : "");

      task(async () => {
        try {
          const result = await request(destination, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": identity.csrf_token
            },
            body: JSON.stringify(payload)
          });
          const created = result.week ?? result;
          copySource = null;
          await load();
          say(`Created draft week ${created.week_start_date} – ` +
            `${addDays(created.week_start_date, 6)}${isTemplate ? " from template" : ""}.`);
        } catch (error) {
          if (error.status === 409) {
            copySource = null;
            await load();
            say("The source, destination or workplace settings changed. " +
              "The calendar was refreshed. Choose the source and destination again.");
            return;
          }
          throw error;
        }
      });
    });

    controls.disabled = fields.disabled = true;
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
