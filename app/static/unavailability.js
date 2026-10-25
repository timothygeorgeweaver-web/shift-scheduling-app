import {confirmAction} from "/static/confirm-dialog.js?v=1";
import {
  node, action, addDays, weekday, monthHeading, createPlannerGrid
} from "/static/planner-grid.js?v=3";
import {projectBusinessPlanner, businessPlannerChanges} from "/static/unavailability-business-day.js?v=2";

export function createUnavailabilityManager({api, expired}) {
  let root = null, epoch = 0, cleanup = () => {};
  const minDate = "2000-01-01", maxFirst = "9998-12-24";

  function reset() {
    epoch++;
    cleanup();
    cleanup = () => {};
    root?.remove();
    root = null;
  }

  async function start(identity) {
    reset();
    const generation = epoch;
    const alive = () => generation === epoch;
    const panel = node("section");
    root = panel;
    panel.className = "team unavailability";
    panel.setAttribute("aria-label", "My Unavailability");
    document.getElementById("account-view").append(panel);
    node("h2", "My Unavailability", panel);
    node("p", "Click a 15-minute cell to block it; click again to clear it. Drag to paint longer blocks. Use arrow keys to move and Space or Enter to toggle.", panel);
    node("p", "White = available. × = unavailable. Stripes = locked. Changes are saved only when you submit.", panel);

    const status = node("p", "Loading your workplace…", panel);
    status.className = "team-status";
    status.setAttribute("role", "status");
    const work = node("fieldset", "", panel);
    node("legend", "Employee planner", work);
    const locationLabel = node("label", "Workplace", work);
    const select = node("select", "", locationLabel);
    const view = node("div", "", work);

    let profiles = [], location = "", first = "", data = null;
    let draft = {}, busy = false, refreshRequired = false;
    let mode = "main", grid = null, preview = null, previewPayload = null;
    let bulk = null;
    const scrollPositions = {main: 0, bulk: 0};
    const hiddenPlanners = {main: false, bulk: false};
    const say = text => { if (alive()) status.textContent = text; };
    const profile = () => profiles.find(p => p.location_id === location);
    const endpoint = () => "/api/v1/me/business-planner/" + encodeURIComponent(location);
    const bulkEndpoint = endpoint;

    function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
    function changedDates() {
      return data ? data.days.filter(day =>
        day.editable && !same(draft[day.date], day.effective_cells)
      ) : [];
    }
    function hasTemplate() {
      return bulk && Object.values(bulk.cells).some(cells => cells.length);
    }
    function dirty() {
      return mode === "bulk" ? hasTemplate() : changedDates().length > 0;
    }
    async function discardOK() {
      return !dirty() || await confirmAction("Discard the unsaved planner changes?");
    }
    function clearGrid() {
      if (grid) {
        scrollPositions[mode] = grid.scroll.scrollTop;
        grid.destroy();
        grid = null;
      }
    }
    const unload = event => {
      if (dirty()) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", unload);
    cleanup = () => {
      grid?.destroy();
      window.removeEventListener("beforeunload", unload);
    };

    async function request(path, options = {}) {
      const response = await api(path, options);
      if (!alive()) throw new Error("Account view changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      if (!response.ok) {
        const failure = await response.json().catch(() => null);
        const error = new Error(
          response.status === 409 ? "Data or policy changed. Return to the main calendar and refresh before previewing again." :
          response.status === 423 ? "A date is now locked. Refresh the calendar." :
          response.status === 404 ? "No active employee profile is available for this workplace." :
          response.status === 422 ? (typeof failure?.detail === "string" ? failure.detail :
            "Check your dates and selections. Use at most 366 dates per mass edit.") :
          "The request was not confirmed. Refresh before trying again."
        );
        error.status = response.status;
        throw error;
      }
      const result = await response.json();
      if (!alive()) throw new Error("Account view changed.");
      return result;
    }

    function postOptions(body) {
      return {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": identity.csrf_token},
        body: JSON.stringify(body)
      };
    }

    async function task(fn) {
      if (busy || !alive()) return;
      busy = true;
      work.disabled = true;
      grid?.setEnabled(false);
      try { await fn(); }
      catch (error) { say(error.message); }
      finally {
        busy = false;
        if (alive()) {
          work.disabled = false;
          grid?.setEnabled(!refreshRequired);
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

    function weekStart(day) {
      return addDays(day, -(weekday(day) - profile().week_start + 7) % 7);
    }

    async function loadCalendar() {
      clearGrid();
      data = null;
      draft = {};
      view.replaceChildren();
      say("Loading calendar…");
      const loaded = await request(endpoint() + "?start=" + first);
      data = projectBusinessPlanner(loaded);
      profiles = profiles.map(p => p.location_id === location ? data.profile : p);
      for (const day of data.days) draft[day.date] = [...(day.effective_cells || [])];
      refreshRequired = false;
      render();
      say(`Times shown in ${data.profile.timezone}. No blocked cells means available all day.`);
    }

    function navigation(parent, anchor, move) {
      const bar = node("div", "", parent);
      bar.className = "planner-navigation";
      const previous = action("←", bar, () => move(-7));
      previous.setAttribute("aria-label", "Previous seven days");
      previous.disabled = addDays(anchor, -7) < minDate;
      node("h3", monthHeading(anchor), bar);
      const next = action("→", bar, () => move(7));
      next.setAttribute("aria-label", "Next seven days");
      next.disabled = addDays(anchor, 7) > maxFirst;
    }

    function render() {
      clearGrid();
      view.replaceChildren();
      select.value = location;
      select.disabled = mode === "bulk";
      if (mode === "bulk") renderBulk();
      else renderMain();
    }

    function renderMain() {
      if (!data) return;
      const toolbar = node("div", "", view);
      toolbar.className = "team-controls";
      action("Refresh calendar", toolbar, async () => {
        if ((await discardOK())) task(loadCalendar);
      });
      const open = action("Set Recurring Unavailability", toolbar, async () => {
        if (!(await discardOK())) return;
        clearGrid();
        mode = "bulk";
        bulk = {
          first: first, last: addDays(first, 27), anchor: first,
          cells: Object.fromEntries(Array.from({length: 7}, (_, i) => [String(i), []]))
        };
        preview = previewPayload = null;
        refreshRequired = false;
        render();
        say("Blank weekly template. Selected cells add unavailability; blank cells leave saved times unchanged.");
      });
      open.disabled = refreshRequired;

      navigation(view, first, async count => {
        if (!(await discardOK())) return;
        const candidate = addDays(first, count);
        if (candidate < minDate || candidate > maxFirst) return;
        first = candidate;
        task(loadCalendar);
      });

      const days = data.days.map(day => ({
        ...day,
        effective_cells: day.effective_cells === null ? null : draft[day.date]
      }));
      const footer = node("div");
      footer.className = "team-controls planner-footer";
      const save = action("Save changes", footer, () => {
        let changes;
        try { changes = businessPlannerChanges(data, draft); }
        catch (error) { say(error.message); return; }
        if (!changes.length || refreshRequired) return;
        task(async () => {
          try {
            const result = await request(endpoint(), {
              method: "PUT",
              headers: {"Content-Type": "application/json", "X-CSRF-Token": identity.csrf_token},
              body: JSON.stringify({
                first_date: first,
                expected_location_revision: data.profile.location_revision,
                changes
              })
            });
            data = projectBusinessPlanner(result);
            draft = Object.fromEntries(data.days.map(day => [
              day.date, [...(day.effective_cells || [])]
            ]));
            render();
            say("Saved. These changes apply only to the edited dates.");
          } catch (error) {
            refreshRequired = true;
            save.disabled = true;
            throw error;
          }
        });
      });
      save.disabled = refreshRequired || changedDates().length === 0;
      action("Discard changes", footer, async () => {
        if (!(await discardOK())) return;
        task(loadCalendar);
      });

      grid = createPlannerGrid(plannerArea(view), days, (index, cells) => {
        draft[data.days[index].date] = cells;
        save.disabled = refreshRequired || changedDates().length === 0;
        say("Unsaved changes.");
      }, scrollPositions.main, {rolloverMinute: data.profile.business_day_start_minute});
      grid.setEnabled(!refreshRequired);
      view.append(footer);
    }

    function plannerArea(parent) {
      const plannerMode = mode;
      const controls = node("div", "", parent);
      controls.className = "planner-visibility";
      const area = node("div", "", parent);
      area.className = "unavailability-planner-area";
      area.id = `unavailability-grid-${generation}-${plannerMode}`;
      const toggle = action("", controls, () => {
        if (busy) return;
        hiddenPlanners[plannerMode] = !hiddenPlanners[plannerMode];
        update();
      });
      toggle.setAttribute("aria-controls", area.id);
      function update() {
        area.hidden = hiddenPlanners[plannerMode];
        toggle.textContent = area.hidden ? "Show planner" : "Hide planner";
        toggle.setAttribute("aria-expanded", String(!area.hidden));
      }
      update();
      return area;
    }

    function dateInput(text, parent, value) {
      const label = node("label", text, parent);
      const input = node("input", "", label);
      input.type = "date";
      input.min = minDate;
      input.max = "9998-12-31";
      input.required = true;
      input.value = value;
      return input;
    }

    function renderBulk() {
      node("h3", "Set Recurring Unavailability", view);
      node("p", "Choose an inclusive date range and mark a weekly pattern. Submission adds blocks to matching dates. It never clears previously saved blocks.", view);
      const dates = node("div", "", view);
      dates.className = "planner-range";
      const from = dateInput("Start business date", dates, bulk.first);
      const to = dateInput("End business date", dates, bulk.last);

      const review = node("section");
      review.className = "planner-review";
      let confirmButton = null;
      function invalidate() {
        preview = previewPayload = null;
        review.replaceChildren();
        confirmButton = null;
        say("Template changed. Create a new preview before submitting.");
      }
      from.addEventListener("change", () => { bulk.first = from.value; invalidate(); });
      to.addEventListener("change", () => { bulk.last = to.value; invalidate(); });

      node("h3", "Weekly pattern", view);
      node("p", "Mark times for each business weekday. Early-morning Next Day cells belong to the weekday above. The date range includes the whole final business day.", view);

      const days = Array.from({length: 7}, (_, i) => {
        const day = addDays(bulk.anchor, i);
        return {
          date: day, editable: true, blocked: [],
          effective_cells: bulk.cells[String(weekday(day))]
        };
      });
      grid = createPlannerGrid(plannerArea(view), days, (index, cells) => {
        bulk.cells[String(weekday(days[index].date))] = cells;
        invalidate();
      }, scrollPositions.bulk, {genericWeek: true,
        rolloverMinute: data.profile.business_day_start_minute});

      const controls = node("div", "", view);
      controls.className = "team-controls planner-footer";
      action("Preview additions", controls, () => {
        if (!from.reportValidity() || !to.reportValidity()) return;
        const span = (Date.parse(to.value + "T12:00:00Z") -
          Date.parse(from.value + "T12:00:00Z")) / 86400000;
        if (span < 0 || span >= 366) {
          say("Choose an ordered inclusive range of at most 366 dates.");
          return;
        }
        if (!hasTemplate()) { say("Select at least one unavailable cell."); return; }
        const payload = {
          first_date: from.value, last_date: to.value,
          weekly_cells: JSON.parse(JSON.stringify(bulk.cells)),
          expected_location_revision: data.profile.location_revision
        };
        task(async () => {
          preview = previewPayload = null;
          review.replaceChildren();
          const result = await request(bulkEndpoint() + "/bulk/preview", postOptions(payload));
          preview = result;
          previewPayload = payload;
          showPreview();
          say("Preview ready. Review the changed and skipped dates before submitting.");
        });
      });
      action("Return to main calendar", controls, async () => {
        if (!(await discardOK())) return;
        clearGrid();
        mode = "main";
        preview = previewPayload = null;
        bulk = null;
        task(loadCalendar);
      });
      view.append(review);

      function showPreview() {
        review.replaceChildren();
        node("h3", "Review additions", review);
        node("p", `${preview.changed_dates.length} calendar dates will change; ${preview.added_cell_count * 15} minutes of unavailability will be added.`, review);
        const changed = node("details", "", review);
        node("summary", "Calendar dates receiving new blocks", changed);
        const changes = node("ul", "", changed);
        for (const day of preview.changed_dates) {
          node("li", `${day.date} (business day ${day.business_dates.join(", ")}): add ${day.added_cells * 15} minutes`, changes);
        }
        node("p", `${preview.skipped_locked_dates.length} locked dates will be skipped.`, review);
        const skipped = node("ul", "", review);
        for (const day of preview.skipped_locked_dates) {
          node("li", `${day.date} (business day ${day.business_dates.join(", ")}): ${day.reason.replaceAll("_", " ")}`, skipped);
        }
        if (!preview.changed_dates.length) {
          node("p", "There are no new blocks to save.", review);
          return;
        }
        confirmButton = action("Confirm and add unavailability", review, () => {
          if (!preview || !previewPayload) return;
          const payload = {...previewPayload, expected_preview: preview.preview_id};
          const showDate = preview.changed_dates[0].business_dates[0];
          task(async () => {
            try {
              const result = await request(bulkEndpoint() + "/bulk/apply", postOptions(payload));
              clearGrid();
              mode = "main";
              first = weekStart(showDate);
              bulk = preview = previewPayload = null;
              await loadCalendar();
              say(`Saved additions on ${result.changed_date_count} dates. Locked dates were preserved.`);
            } catch (error) {
              preview = previewPayload = null;
              review.replaceChildren();
              node("p", "Submission was not confirmed. Create a fresh preview before trying again.", review);
              throw error;
            }
          });
        });
      }
      if (preview && previewPayload) showPreview();
    }

    select.addEventListener("change", async () => {
      const chosen = select.value;
      select.value = location;
      if (!(await discardOK())) return;
      location = chosen;
      const p = profile();
      first = weekStart(addDays(localToday(p.timezone), Math.max(14, p.cutoff_days_before + 7)));
      task(loadCalendar);
    });

    await task(async () => {
      profiles = await request("/api/v1/me/planner/profiles");
      if (!profiles.length) {
        work.hidden = true;
        say("No active employee profile is linked to this account. Sign in with the linked employee account.");
        return;
      }
      for (const p of profiles) {
        const option = node("option", `${p.business_name} / ${p.location_name} — ${p.display_name}`, select);
        option.value = p.location_id;
      }
      location = profiles[0].location_id;
      const p = profile();
      first = weekStart(addDays(localToday(p.timezone), Math.max(14, p.cutoff_days_before + 7)));
      await loadCalendar();
    });
  }
  return {start, reset};
}
