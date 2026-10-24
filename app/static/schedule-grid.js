import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";

const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function clockMinute(minute) {
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}

export function compareShifts(a, b, roles) {
  const left = roles.get(a.role_id);
  const right = roles.get(b.role_id);
  const order = (left?.display_order ?? 0) - (right?.display_order ?? 0);
  if (order) return order;
  const job = (left?.name ?? a.role_name).localeCompare(
    right?.name ?? b.role_name, "en", {sensitivity: "base"}
  );
  if (job) return job;
  const role = String(a.role_id).localeCompare(String(b.role_id));
  if (role) return role;
  const finish = Date.parse(a.end_at) - Date.parse(b.end_at);
  if (finish) return finish;
  return String(a.id).localeCompare(String(b.id));
}

export function createScheduleGrid(parent, data, roles, choose, scrollTop = 1408) {
  const roleMap = new Map(roles.map(role => [role.id, role]));
  const grouped = new Map();

  for (const shift of data.slots) {
    const start = shift.local_start;
    const minute = Number(start.slice(11, 13)) * 60 + Number(start.slice(14, 16));
    const cell = Math.floor(minute / 15);
    const key = `${start.slice(0, 10)}/${cell}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(shift);
  }
  for (const shifts of grouped.values()) {
    shifts.sort((a, b) => compareShifts(a, b, roleMap));
  }

  const scroll = node("div", "", parent);
  scroll.className = "schedule-grid-scroll";
  scroll.tabIndex = 0;
  scroll.setAttribute("aria-label", "Seven-day shift planner; scroll for more times");

  const grid = node("div", "", scroll);
  grid.className = "schedule-time-grid";
  node("strong", "Time", grid).className = "schedule-grid-corner";
  for (let offset = 0; offset < 7; offset++) {
    const day = addDays(data.week.week_start_date, offset);
    const header = node("div", "", grid);
    header.className = "schedule-grid-heading";
    node("strong", names[weekday(day)], header);
    node("span", day, header);
  }

  for (let cell = 0; cell < 96; cell++) {
    const time = clockMinute(cell * 15);
    const label = node("div", time, grid);
    label.className = "schedule-grid-time";
    for (let offset = 0; offset < 7; offset++) {
      const day = addDays(data.week.week_start_date, offset);
      const box = node("div", "", grid);
      box.className = "schedule-start-cell";
      for (const shift of grouped.get(`${day}/${cell}`) ?? []) {
        const card = node("article", "", box);
        card.className = "schedule-start-card";
        node("strong", shift.role_name, card);
        const overnight = shift.local_end.slice(0, 10) !== day;
        node("span",
          `${shift.local_start.slice(11, 16)}–${shift.local_end.slice(11, 16)}${overnight ? " (+1 day)" : ""}`,
          card);
        node("small", "Unassigned", card);
        const hours = Math.floor(shift.estimated_work_minutes / 60);
        const minutes = shift.estimated_work_minutes % 60;
        node("small", `Estimated work: ${hours}h${minutes ? ` ${minutes}m` : ""}`, card);
        if (shift.meal_estimate_minutes) {
          node("small", "Includes 30-minute meal estimate", card);
        }
        if (shift.note) node("small", shift.note, card);
      }
      if (data.week.state === "draft") {
        const add = action("+ Add shift", box, () => {
          choose(day, cell * 15, scroll.scrollTop);
        });
        add.className = "schedule-cell-add";
        add.setAttribute("aria-label", `Add shift starting ${day} at ${time}`);
      }
    }
  }
  scroll.scrollTop = scrollTop;
  return scroll;
}
