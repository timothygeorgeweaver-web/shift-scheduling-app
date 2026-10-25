import {node, addDays, weekday} from "/static/planner-grid.js?v=3";

const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function renderPublishedRoster(parent, data, {employeeId = null} = {}) {
  const {publication, roster} = data;
  const week = roster.week;
  const section = node("section", "", parent);
  section.className = "published-roster";
  section.setAttribute("aria-label", `Published roster version ${publication.publication_number}`);
  node("h3", `${week.week_start_date} – ${addDays(week.week_start_date, 6)}`, section);
  node("p", `Published version ${publication.publication_number} · ${week.timezone}`, section);
  node("p", "Published " + new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium", timeStyle: "short", timeZone: week.timezone
  }).format(new Date(publication.published_at)), section);

  const shifts = roster.shifts;
  const unfilled = shifts.filter(shift => !shift.employee).length;
  node("p", `${shifts.length} shifts · ${unfilled} Unfilled` +
    (employeeId ? " · Your shifts are highlighted." : ""), section);
  if (!shifts.length) {
    node("p", "This published week contains no shifts.", section);
    return section;
  }

  const rollover = week.business_day_start_minute;
  const minuteOf = shift => Number(shift.local_start.slice(11, 13)) * 60 +
    Number(shift.local_start.slice(14, 16));
  const times = [...new Set(shifts.map(minuteOf))].sort((a, b) =>
    ((a - rollover + 1440) % 1440) - ((b - rollover + 1440) % 1440)
  );
  const grouped = new Map();
  for (const shift of shifts) {
    const key = `${shift.business_date}/${minuteOf(shift)}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(shift);
  }
  for (const rows of grouped.values()) {
    rows.sort((a, b) =>
      a.role_display_order - b.role_display_order ||
      a.role_name.localeCompare(b.role_name, "en", {sensitivity: "base"}) ||
      a.role_id.localeCompare(b.role_id) ||
      Date.parse(a.end_at) - Date.parse(b.end_at) ||
      a.id.localeCompare(b.id)
    );
  }

  const grid = node("div", "", section);
  grid.className = "schedule-time-grid published-roster-grid";
  node("strong", "Start", grid).className = "schedule-grid-corner";
  const days = Array.from({length: 7}, (_, offset) =>
    addDays(week.week_start_date, offset)
  );
  for (const day of days) {
    const heading = node("div", "", grid);
    heading.className = "schedule-grid-heading";
    node("strong", names[weekday(day)], heading);
    node("span", day, heading);
  }
  for (const minute of times) {
    const clock = `${String(Math.floor(minute / 60)).padStart(2, "0")}:` +
      String(minute % 60).padStart(2, "0");
    const label = node("div", clock, grid);
    label.className = "schedule-grid-time";
    if (minute < rollover) node("small", "Next Day", label);

    for (const day of days) {
      const cell = node("div", "", grid);
      cell.className = "schedule-start-cell";
      const rows = grouped.get(`${day}/${minute}`) ?? [];
      if (!rows.length) {
        node("span", "—", cell).className = "assignment-empty";
      }
      for (const shift of rows) {
        const card = node("article", "", cell);
        card.className = "schedule-start-card published-roster-card";
        node("strong", shift.role_name, card);
        node("span", shift.employee?.display_name ?? "Unfilled", card);
        node("small", `Ends ${shift.local_end.slice(11, 16)}`, card);
        if (shift.local_end.slice(0, 10) !== shift.local_start.slice(0, 10)) {
          node("small", "Next calendar day", card);
        }
        if (!shift.employee) card.classList.add("published-unfilled");
        if (employeeId && shift.employee?.id === employeeId) {
          card.classList.add("published-own-shift");
        }
        card.setAttribute("aria-label",
          `${shift.role_name}, ${shift.employee?.display_name ?? "Unfilled"}, ` +
          `${shift.local_start.slice(0, 16).replace("T", " ")} to ` +
          shift.local_end.slice(0, 16).replace("T", " "));
      }
    }
  }
  return section;
}
