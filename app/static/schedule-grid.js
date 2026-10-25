import {node, action, addDays, weekday} from "/static/planner-grid.js?v=2";

import {businessDateForLocal, calendarDateForCell, rolloverMinute} from "/static/business-day.js?v=1";

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

export function createScheduleGrid(parent, data, roles, choose, scrollTop = 1408, openShift = () => {}, {genericWeek = false} = {}) {
  const rollover = rolloverMinute(data.week.business_day_start_minute ?? 0);
  const roleMap = new Map(roles.map(role => [role.id, role]));
  const grouped = new Map();

  for (const shift of data.slots) {
    const start = shift.local_start;
    const minute = Number(start.slice(11, 13)) * 60 + Number(start.slice(14, 16));
    const cell = Math.floor(((minute - rollover + 1440) % 1440) / 15);
    const key = `${businessDateForLocal(start, rollover)}/${cell}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(shift);
  }
  for (const shifts of grouped.values()) {
    shifts.sort((a, b) => compareShifts(a, b, roleMap));
  }

  const scroll = node("div", "", parent);
  scroll.className = "schedule-grid-scroll";
  scroll.setAttribute("aria-label", "Seven-day shift planner");

  const grid = node("div", "", scroll);
  grid.className = "schedule-time-grid";
  node("strong", "Time", grid).className = "schedule-grid-corner";
  for (let offset = 0; offset < 7; offset++) {
    const day = addDays(data.week.week_start_date, offset);
    const header = node("div", "", grid);
    header.className = "schedule-grid-heading";
    node("strong", names[weekday(day)], header);
    if (!genericWeek) node("span", day, header);
  }

  for (let cell = 0; cell < 96; cell++) {
    const minute = (rollover + cell * 15) % 1440;
    const time = clockMinute(minute);
    const nextCalendarDay = minute < rollover;
    const label = node("div", time, grid);
    if (nextCalendarDay) node("small", "Next day", label);
    label.className = "schedule-grid-time";
    for (let offset = 0; offset < 7; offset++) {
      const day = addDays(data.week.week_start_date, offset);
      const box = node("div", "", grid);
      box.className = "schedule-start-cell";
      for (const shift of grouped.get(`${day}/${cell}`) ?? []) {
        const editable = data.week.state === "draft";
        const card = node(editable ? "button" : "article", "", box);
        card.className = "schedule-start-card";
        node("strong", shift.role_name, card);
        if (editable) {
          card.type = "button";
          card.setAttribute("aria-label",
            genericWeek
              ? `Edit ${shift.role_name}, ${names[weekday(day)]}, ` +
                `${shift.local_start.slice(11, 16)} to ${shift.local_end.slice(11, 16)}`
              : `Edit ${shift.role_name}, ${shift.local_start.slice(0, 10)}, ` +
                `${shift.local_start.slice(11, 16)} to ${shift.local_end.slice(0, 16)}`);
          card.addEventListener("click", () => openShift(shift, card));
        }

      }
      if (data.week.state === "draft") {
        const add = action("+ Add shift", box, () => {
          choose(calendarDateForCell(day, minute, rollover), minute, scroll.scrollTop, add);
        });
        add.className = "schedule-cell-add";
        add.setAttribute("aria-label",
          `Add shift starting ${genericWeek ? names[weekday(day)] : day} at ${time}${nextCalendarDay ? ", next calendar day" : ""}`);
      }
    }
  }
  return scroll;
}
