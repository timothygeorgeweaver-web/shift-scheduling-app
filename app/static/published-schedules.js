import {node, action, addDays} from "/static/planner-grid.js?v=3";
import {renderPublishedRoster} from "/static/published-roster.js?v=1";

export function createPublishedScheduleManager({api, expired}) {
  let root = null, epoch = 0, events = null;

  function reset() {
    epoch++;
    events?.abort();
    events = null;
    root?.remove();
    root = null;
  }

  async function start() {
    reset();
    const generation = epoch;
    const active = () => generation === epoch && root?.isConnected;
    const panel = node("section");
    root = panel;
    panel.className = "team published-schedules";
    panel.setAttribute("aria-label", "Published Schedules");
    document.getElementById("account-view").append(panel);
    node("h2", "Published Schedules", panel);
    node("p", "Official team schedules. Draft changes appear here only after republishing.", panel);
    const status = node("p", "Loading published schedules…", panel);
    status.setAttribute("role", "status");
    const controls = node("fieldset", "", panel);
    node("legend", "Workplace and month", controls);
    const select = node("select", "", node("label", "Workplace", controls));
    const navigation = node("div", "", controls);
    navigation.className = "planner-navigation";
    const previous = action("← Previous month", navigation, () => moveMonth(-1));
    const heading = node("h3", "", navigation);
    const next = action("Next month →", navigation, () => moveMonth(1));
    action("Refresh published schedules", controls, () => task(loadMonth));
    const choices = node("fieldset", "", panel);
    node("legend", "Published weeks", choices);
    const weekButtons = node("div", "", choices);
    weekButtons.className = "published-week-buttons";
    const view = node("div", "", panel);

    let profiles = [], weeks = [], selected = "", busy = false, pending = false;
    let month = new Date().toISOString().slice(0, 7);
    const say = text => { if (active()) status.textContent = text; };
    const base = () => "/api/v1/me/published-schedules/" + encodeURIComponent(select.value);

    async function request(path) {
      const response = await api(path);
      if (!active()) throw new Error("Account changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      const result = await response.json().catch(() => null);
      if (!active()) throw new Error("Account changed.");
      if (!response.ok) throw new Error(
        response.status === 404 ? "This published schedule is unavailable." :
        "Published schedules could not be loaded. Try refreshing."
      );
      return result;
    }

    async function task(operation) {
      if (busy || !active()) return;
      busy = true;
      controls.disabled = choices.disabled = true;
      panel.setAttribute("aria-busy", "true");
      try {
        await operation();
      } catch (error) {
        if (active()) {
          view.replaceChildren();
          say(error.message);
        }
      } finally {
        if (active()) {
          busy = false;
          controls.disabled = choices.disabled = false;
          previous.disabled = month === "2000-01";
          next.disabled = month === "9998-12";
          panel.setAttribute("aria-busy", "false");
          if (pending) {
            pending = false;
            task(loadMonth);
          }
        }
      }
    }

    function adjacentMonth(value, count) {
      const date = new Date(value + "-01T12:00:00Z");
      date.setUTCMonth(date.getUTCMonth() + count);
      return date.toISOString().slice(0, 7);
    }

    function moveMonth(count) {
      if (busy) return;
      const candidate = adjacentMonth(month, count);
      if (candidate < "2000-01" || candidate > "9998-12") return;
      month = candidate;
      selected = "";
      task(loadMonth);
    }

    function markSelected() {
      for (const button of weekButtons.querySelectorAll("button")) {
        button.setAttribute("aria-pressed", String(button.dataset.week === selected));
      }
    }

    async function loadWeek(weekId) {
      view.replaceChildren();
      say("Loading official roster…");
      const data = await request(base() + "/" + encodeURIComponent(weekId));
      if (!active()) return;
      selected = weekId;
      renderPublishedRoster(view, data, {
        employeeId: profiles.find(profile =>
          profile.location_id === select.value
        )?.employee_id ?? null
      });
      markSelected();
      say(`Official published version ${data.publication.publication_number}.`);
    }

    async function loadMonth() {
      view.replaceChildren();
      weekButtons.replaceChildren();
      heading.textContent = new Intl.DateTimeFormat("en-US", {
        month: "long", year: "numeric", timeZone: "UTC"
      }).format(new Date(month + "-01T12:00:00Z"));
      say("Loading published weeks…");
      const first = month + "-01";
      const start = addDays(first, -6) < "2000-01-01"
        ? "2000-01-01" : addDays(first, -6);
      const end = addDays(adjacentMonth(month, 1) + "-01", -1);
      weeks = await request(base() + "?start=" + start + "&end=" + end);
      if (!active()) return;
      if (!weeks.length) {
        selected = "";
        node("p", "No published schedules for this month.", weekButtons);
        say("No published schedules for this month.");
        return;
      }
      for (const week of weeks) {
        const button = action(
          `Week of ${week.week_start_date} · Version ${week.publication_number}`,
          weekButtons, () => task(() => loadWeek(week.week_id))
        );
        button.dataset.week = week.week_id;
      }
      const today = new Date().toISOString().slice(0, 10);
      const chosen = weeks.find(week => week.week_id === selected) ??
        weeks.find(week => week.week_start_date <= today &&
          addDays(week.week_start_date, 6) >= today) ??
        weeks.find(week => week.week_start_date >= today) ??
        weeks[weeks.length - 1];
      await loadWeek(chosen.week_id);
    }

    select.addEventListener("change", () => {
      selected = "";
      task(loadMonth);
    });
    events = new AbortController();
    window.addEventListener("schedule-published", event => {
      if (!active() || event.detail?.locationId !== select.value) return;
      if (busy) pending = true;
      else task(loadMonth);
    }, {signal: events.signal});

    await task(async () => {
      profiles = await request("/api/v1/me/published-schedules/profiles");
      if (!active()) return;
      if (!profiles.length) {
        panel.hidden = true;
        return;
      }
      for (const profile of profiles) {
        node("option", `${profile.business_name} / ${profile.location_name}`, select)
          .value = profile.location_id;
      }
      await loadMonth();
    });
  }

  return {start, reset};
}
