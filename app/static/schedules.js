import {confirmAction} from "/static/confirm-dialog.js?v=1";
import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";
import {createShiftEditor} from "/static/shift-editor.js?v=13";
import {createAssignmentEditor} from "/static/assignment-editor.js?v=5";
import {askTemplateName} from "/static/template-name-dialog.js?v=2";
import {createTemplateEditor} from "/static/template-editor.js?v=5";
import {askTemplateApplication} from "/static/template-apply-dialog.js?v=1";

export function createScheduleManager({api, expired, onEditorOpen = () => {}}) {
  let root = null, epoch = 0, dispose = () => {}, policyRefresh = () => {};
  function reset() {
    epoch++;
    dispose();
    dispose = () => {};
    policyRefresh = () => {};
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
    const abort = new AbortController();
    const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    let month = new Date().toISOString().slice(0, 7);
    let state = null, templates = [], selected = "", copySource = null;
    let busy = false, ready = false, editorOpen = false, pendingRefresh = false;

    const panel = node("section");
    root = panel;
    panel.className = "team schedules";
    panel.setAttribute("aria-label", "Schedules");
    document.getElementById("account-view").append(panel);
    node("h2", "Schedules", panel);
    node("p", "Select a week-start date, then choose an action below the calendar.", panel);
    const status = node("p", "Loading workplaces…", panel);
    status.className = "team-status";
    status.setAttribute("role", "status");
    const controls = node("fieldset", "", panel);
    node("legend", "Workplace", controls);
    const select = node("select", "", node("label", "Workplace", controls));
    const picker = node("fieldset", "", panel);
    picker.className = "schedule-calendar";
    node("legend", "Select a week", picker);
    const policy = node("p", "", picker);
    const navigation = node("div", "", picker);
    navigation.className = "planner-navigation";
    const calendar = node("div", "", picker);
    const actions = node("fieldset", "", panel);
    actions.className = "selected-week-actions";
    const actionTitle = node("legend", "Week actions", actions);
    const selection = node("p", "Choose a week-start date above.", actions);
    selection.setAttribute("role", "status");
    const copyNotice = node("p", "", actions);
    const buttons = node("div", "", actions);
    buttons.className = "selected-week-buttons";
    const create = action("Create blank week", buttons, createBlank);
    const open = action("Schedule Planner", buttons, openWeek);
    const assign = action("Assign Shifts", buttons, openAssigner);
    const clearAssignments = action("Clear All Assignments", buttons, clearWeekAssignments);
    const save = action("Save as Template", buttons, saveTemplate);
    const copy = action("Copy this week", buttons, copyWeek);
    const remove = action("Delete week", buttons, deleteWeek);
    const apply = action("Apply Template", buttons, applyTemplate);
    const cancelCopy = action("Cancel copying", actions, () => {
      copySource = null;
      renderActions();
      say("Copying cancelled.");
    });
    const templatesView = node("div", "", panel);

    const shifts = createShiftEditor({
      api, expired, identity, parent: panel, onOpen: value => {
        const wasOpen = editorOpen;
        lockOuter(value);
        if (wasOpen && !value && !busy && active()) task(() => load(selected));
      }
    });
    const assigner = createAssignmentEditor({
      api, expired, identity, parent: panel, onOpen: value => {
        const wasOpen = editorOpen;
        lockOuter(value);
        if (wasOpen && !value && !busy && active()) task(() => load(selected));
      }
    });
    const templateEditor = createTemplateEditor({
      api, expired, identity, parent: panel, onOpen: lockOuter,
      onSaved: () => task(async () => {
        await load(selected);
        say("Template saved.");
      })
    });
    dispose = () => {
      abort.abort();
      shifts.destroy();
      assigner.destroy();
      templateEditor.destroy();
    };
    const say = text => { if (active()) status.textContent = text; };
    const base = () => "/api/v1/staff/locations/" + encodeURIComponent(select.value);
    const weeksUrl = () => base() + "/schedule-weeks";
    const templatesUrl = () => base() + "/schedule-templates";
    const selectedWeek = () => state?.weeks.find(week => week.week_start_date === selected);
    const covered = day => state?.weeks.find(week =>
      week.week_start_date <= addDays(day, 6) && addDays(week.week_start_date, 6) >= day
    );
    function validSelection() {
      return ready && !!selected && weekday(selected) === state.location.week_start_day &&
        (!covered(selected) || !!selectedWeek());
    }
    function lockOuter(value) {
      editorOpen = value;
      onEditorOpen(value);
      updateLocks();
      if (!value) flushPolicyRefresh();
    }
    function flushPolicyRefresh() {
      if (!pendingRefresh || busy || editorOpen || !active()) return;
      pendingRefresh = false;
      copySource = null;
      task(async () => {
        await load(selected);
        say("Workplace policy updated. Calendar refreshed.");
      });
    }
    function updateLocks() {
      if (!active()) return;
      controls.disabled = busy || editorOpen;
      retry.hidden = ready || busy || editorOpen;
      picker.disabled = actions.disabled = busy || editorOpen || !ready;
      for (const button of templatesView.querySelectorAll("button")) {
        button.disabled = busy || editorOpen || !ready;
      }
    }
    function renderActions() {
      const valid = validSelection(), week = selectedWeek();
      const draft = week?.state === "draft";
      actionTitle.textContent = selected ? `Week of ${selected}` : "Week actions";
      selection.textContent = valid
        ? `${selected} – ${addDays(selected, 6)} · ${week ? week.state : "Not created yet"}`
        : "Choose a week-start date above.";
      create.disabled = !valid || !!week;
      open.disabled = save.disabled = !valid || !week;
      remove.disabled = !valid || !draft;
      assign.disabled = clearAssignments.disabled = !valid || !draft;
      apply.disabled = !valid || (!!week && !draft) || !templates.length;
      copy.textContent = copySource ? "Copy week here" : "Copy this week";
      copy.disabled = !valid || (copySource ? !!covered(selected) : !week);
      cancelCopy.hidden = copyNotice.hidden = !copySource;
      copyNotice.textContent = copySource
        ? `Copying week ${copySource.week_start_date}. Select an unused week-start date, then choose Copy week here.`
        : "";
      apply.title = templates.length ? "" : "Create or save a template first.";
      updateLocks();
    }
    function mutation(body, method = "POST") {
      return {
        method,
        headers: {"Content-Type": "application/json", "X-CSRF-Token": identity.csrf_token},
        body: JSON.stringify(body)
      };
    }
    async function request(path, options = {}) {
      const response = await api(path, options);
      if (!active()) throw new Error("Account changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      const result = await response.json().catch(() => null);
      if (!active()) throw new Error("Account changed.");
      if (!response.ok) {
        const error = new Error(typeof result?.detail === "string" ? result.detail :
          "The request was not confirmed. Refresh the calendar before trying again.");
        error.status = response.status;
        throw error;
      }
      return result;
    }
    async function task(operation) {
      if (busy || !active()) return;
      busy = true;
      panel.setAttribute("aria-busy", "true");
      updateLocks();
      try {
        await operation();
      } catch (error) {
        if (!active()) return;
        if (error.status === 409) {
          copySource = null;
          try {
            await load(selected);
            say(error.message + " The calendar was refreshed; check the selected week.");
          } catch {
            ready = false;
            say("Refresh the calendar before trying again.");
          }
        } else {
          if (error.status !== 422) ready = false;
          say(error.message);
        }
      } finally {
        busy = false;
        if (active()) {
          panel.setAttribute("aria-busy", "false");
          renderActions();
          flushPolicyRefresh();
        }
      }
    }

    function adjacentMonth(value, count) {
      const day = new Date(value + "-01T12:00:00Z");
      day.setUTCMonth(day.getUTCMonth() + count);
      return day.toISOString().slice(0, 7);
    }
    function boundaries() {
      return {first: month + "-01", last: addDays(adjacentMonth(month, 1) + "-01", -1)};
    }
    function renderNavigation() {
      navigation.replaceChildren();
      const previous = action("← Previous month", navigation, () => moveMonth(-1));
      previous.disabled = month === "2000-01";
      node("h3", new Intl.DateTimeFormat("en-US", {
        month: "long", year: "numeric", timeZone: "UTC"
      }).format(new Date(month + "-01T12:00:00Z")), navigation);
      const next = action("Next month →", navigation, () => moveMonth(1));
      next.disabled = month === "9998-12";
    }
    function moveMonth(count) {
      if (busy || editorOpen) return;
      const candidate = adjacentMonth(month, count);
      if (candidate < "2000-01" || candidate > "9998-12") return;
      month = candidate;
      task(() => load(""));
    }
    function renderCalendar() {
      calendar.replaceChildren();
      const {first, last} = boundaries();
      const grid = node("div", "", calendar);
      grid.className = "week-picker";
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", "Choose a schedule week");
      for (const name of names) node("strong", name.slice(0, 3), grid).className = "week-picker-heading";
      for (let n = 0; n < weekday(first); n++) node("span", "", grid).setAttribute("aria-hidden", "true");
      const dayButtons = [];
      for (let day = first; day <= last; day = addDays(day, 1)) {
        const button = node("button", "", grid);
        button.type = "button";
        button.className = "week-picker-day";
        node("span", String(Number(day.slice(-2))), button);
        const correctDay = weekday(day) === state.location.week_start_day;
        const existing = correctDay ? covered(day) : null;
        const exact = existing?.week_start_date === day;
        button.disabled = !correctDay || day > "9998-12-25" || (!!existing && !exact);
        const description = exact ? `${existing.state} week` : existing ? "Overlaps another schedule" :
          correctDay ? "Unused week" : `Weeks begin ${names[state.location.week_start_day]}`;
        if (existing) {
          node("small", exact ? existing.state : "Covered", button);
          button.classList.add("week-picker-covered");
        }
        button.title = `${day}: ${description}`;
        button.setAttribute("aria-label", button.title);
        button.setAttribute("aria-pressed", String(selected === day));
        button.addEventListener("click", () => {
          if (busy || editorOpen || !ready || button.disabled) return;
          selected = day;
          for (const other of dayButtons) other.setAttribute("aria-pressed", "false");
          button.setAttribute("aria-pressed", "true");
          renderActions();
        });
        dayButtons.push(button);
      }
      renderActions();
    }
    function renderTemplates() {
      templatesView.replaceChildren();
      node("h3", "Schedule templates", templatesView);
      action("Create New Template", templatesView, () => editTemplate());
      if (!templates.length) node("p", "No templates yet. Create one or save a selected week as a template.", templatesView);
      const list = node("ul", "", templatesView);
      for (const template of templates) {
        const item = node("li", "", list);
        node("strong", template.name, item);
        node("span", ` · ${template.shift_count} shifts`, item);
        action("Edit Template", item, () => editTemplate(template));
        action("Archive template", item, () => archiveTemplate(template));
      }
    }
    async function load(keep = selected) {
      shifts.reset();
      assigner.reset();
      templateEditor.reset();
      ready = false;
      renderActions();
      renderNavigation();
      say("Loading schedule calendar…");
      const {first, last} = boundaries();
      const start = addDays(first, -6) < "2000-01-01" ? "2000-01-01" : addDays(first, -6);
      const end = addDays(last, 6) > "9998-12-31" ? "9998-12-31" : addDays(last, 6);
      [state, templates] = await Promise.all([
        request(weeksUrl() + "?start=" + start + "&end=" + end), request(templatesUrl())
      ]);
      selected = keep >= first && keep <= last ? keep : "";
      policy.textContent = `Weeks begin ${names[state.location.week_start_day]}. Times use ${state.location.timezone}. Select an existing or unused week.`;
      ready = true;
      renderTemplates();
      renderCalendar();
      say("Select a week and choose an action.");
    }

    function createBlank() {
      if (!validSelection() || selectedWeek() || busy) return;
      const day = selected;
      task(async () => {
        await request(weeksUrl(), mutation({week_start_date: day, expected_location_revision: state.location.revision}));
        copySource = null;
        await load(day);
        say("Blank draft created. Choose Open week to add shifts.");
      });
    }
    function openWeek() {
      const week = selectedWeek();
      if (!busy && !editorOpen && ready && week) shifts.open(select.value, week.id);
    }
    function openAssigner() {
      const week = selectedWeek();
      if (busy || editorOpen || !ready || week?.state !== "draft") return;
      assigner.open(select.value, week.id);
    }
    async function clearWeekAssignments() {
      const week = selectedWeek();
      if (busy || editorOpen || !ready || week?.state !== "draft") return;
      if (!await confirmAction(
        `Clear ALL employee assignments for week ${week.week_start_date}?\n` +
        "Every shift will remain in the planner as Unassigned."
      )) return;
      task(async () => {
        const result = await request(
          weeksUrl() + "/" + encodeURIComponent(week.id) + "/assignments/clear",
          mutation({
            expected_week_revision: week.revision,
            expected_location_revision: state.location.revision
          })
        );
        await load(week.week_start_date);
        say(`${result.cleared_assignments} assignments cleared. All shifts were kept.`);
      });
    }
    async function saveTemplate() {
      const week = selectedWeek();
      if (!ready || busy || !week) return;
      const name = await askTemplateName(panel, week);
      if (name === null || !active()) return;
      task(async () => {
        await request(weeksUrl() + "/" + encodeURIComponent(week.id) + "/templates",
          mutation({name, expected_source_revision: week.revision}));
        await load(week.week_start_date);
        say(`Saved template: ${name}.`);
      });
    }
    async function copyWeek() {
      if (!validSelection() || busy) return;
      if (!copySource) {
        const week = selectedWeek();
        if (!week) return;
        copySource = {...week};
        selected = "";
        renderCalendar();
        say("Select an unused destination week, then choose Copy week here.");
        return;
      }
      if (covered(selected)) return;
      const day = selected, source = {...copySource};
      if (!await confirmAction(`Copy week ${source.week_start_date} to ${day}? The source stays unchanged.`)) return;
      task(async () => {
        await request(weeksUrl() + "/" + encodeURIComponent(source.id) + "/copy", mutation({
          week_start_date: day, expected_source_revision: source.revision,
          expected_location_revision: state.location.revision
        }));
        copySource = null;
        await load(day);
        say("Week copied into an independent draft.");
      });
    }
    async function deleteWeek() {
      const week = selectedWeek();
      if (busy || !ready || week?.state !== "draft") return;
      if (!await confirmAction(`Delete the whole draft week ${week.week_start_date} – ${addDays(week.week_start_date, 6)} and all its shifts?\nSaved templates stay unchanged. This date will become available for a fresh week.`)) return;
      task(async () => {
        await request(weeksUrl() + "/" + encodeURIComponent(week.id) + "/remove",
          mutation({expected_week_revision: week.revision}));
        copySource = null;
        await load(week.week_start_date);
        say("Week deleted. Create a blank week or apply a template to start fresh.");
      });
    }
    async function applyTemplate() {
      const week = selectedWeek();
      if (busy || !validSelection() || !templates.length || (week && week.state !== "draft")) return;
      const day = selected;
      const choice = await askTemplateApplication(panel, templates, day, week, abort.signal);
      if (!choice || !active()) return;
      if (week && choice.mode === "replace" && !await confirmAction(
        `Replace ALL shifts in week ${day} with template "${choice.template.name}"?`
      )) return;
      task(async () => {
        await request(templatesUrl() + "/" + encodeURIComponent(choice.template.id) + "/apply", mutation({
          week_start_date: day, mode: choice.mode,
          expected_template_revision: choice.template.revision,
          expected_location_revision: state.location.revision,
          ...(week ? {week_id: week.id, expected_week_revision: week.revision} : {})
        }));
        copySource = null;
        await load(day);
        say(choice.mode === "replace" ? "Template applied. Previous shifts were replaced." : "Template shifts appended.");
      });
    }
    function editTemplate(template = null) {
      if (busy || editorOpen || !ready) return;
      copySource = null;
      renderActions();
      shifts.reset();
      templateEditor.open({locationId: select.value, locationRevision: state.location.revision,
        rolloverMinute: state.location.business_day_start_minute, templateId: template?.id ?? null});
    }
    async function archiveTemplate(template) {
      if (busy || !ready || !await confirmAction(`Archive "${template.name}"? Existing weeks stay unchanged.`)) return;
      task(async () => {
        await request(templatesUrl() + "/" + encodeURIComponent(template.id), mutation({
          name: template.name, active: false, expected_template_revision: template.revision
        }, "PATCH"));
        await load(selected);
        say("Template archived.");
      });
    }

    policyRefresh = locationId => {
      if (!active() || locationId !== select.value) return;
      pendingRefresh = true;
      flushPolicyRefresh();
    };
    const retry = action("Retry loading calendar", controls, () => task(() => load(selected)));
    select.addEventListener("change", () => {
      pendingRefresh = false;
      copySource = null;
      task(() => load(""));
    });
    updateLocks();
    renderActions();
    await task(async () => {
      const locations = await request("/api/v1/staff/locations");
      if (!locations.length) {
        controls.hidden = picker.hidden = actions.hidden = true;
        say("No workplaces are available for scheduling.");
        return;
      }
      for (const location of locations) {
        node("option", `${location.organization_name} / ${location.name}`, select).value = location.id;
      }
      await load("");
    });
  }
  return {start, reset, policySaved(locationId) { policyRefresh(locationId); }};
}
