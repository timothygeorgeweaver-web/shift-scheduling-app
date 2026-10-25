import {addDays, clock} from "/static/planner-grid.js?v=3";
import {calendarDateForCell, rolloverMinute} from "/static/business-day.js?v=1";

export function projectBusinessPlanner(data) {
  const rollover = rolloverMinute(data.profile.business_day_start_minute);
  const rows = new Map(data.calendar_days.map(row => [row.date, row]));
  const days = Array.from({length: 7}, (_, offset) => {
    const day = addDays(data.first_date, offset);
    const selected = [], exact = new Set();
    const states = Array.from({length: 96}, (_, cell) => {
      const minute = (rollover + cell * 15) % 1440;
      const actualDay = calendarDateForCell(day, minute, rollover);
      const row = rows.get(actualDay);
      if (!row) throw new Error("A calendar date is missing. Refresh the planner.");
      const blocked = row.blocked.some(([start, end]) => start < minute + 15 && minute < end);
      const partial = row.effective_cells === null && blocked;
      if (blocked) selected.push(cell);
      if (partial) {
        for (const [start, end] of row.blocked) {
          if (start < minute + 15 && minute < end) {
            exact.add(`${actualDay} ${clock(start)}–${clock(end)}`);
          }
        }
      }
      return {
        calendar_date: actualDay, calendar_cell: minute / 15,
        editable: row.editable && row.effective_cells !== null, partial
      };
    });
    return {
      date: day, editable: states.some(state => state.editable), blocked: [],
      effective_cells: selected, cell_states: states,
      exact_locked_times: [...exact]
    };
  });
  return {...data, days};
}

export function businessPlannerChanges(data, draft) {
  const rows = new Map(data.calendar_days.map(row => [row.date, row]));
  const after = new Map();
  for (const day of data.days) {
    const wanted = draft[day.date] ?? day.effective_cells;
    if (!Array.isArray(wanted) || wanted.length > 96 || wanted.some(
      cell => !Number.isInteger(cell) || cell < 0 || cell >= 96
    )) throw new Error("Invalid planner cells. Refresh before saving.");
    const selected = new Set(wanted), before = new Set(day.effective_cells);
    for (let cell = 0; cell < 96; cell++) {
      if (selected.has(cell) === before.has(cell)) continue;
      const state = day.cell_states[cell];
      if (!state.editable) throw new Error("A changed cell is locked. Refresh the planner.");
      if (!after.has(state.calendar_date)) {
        after.set(state.calendar_date, new Set(rows.get(state.calendar_date).blocked_cells));
      }
      const cells = after.get(state.calendar_date);
      if (selected.has(cell)) cells.add(state.calendar_cell);
      else cells.delete(state.calendar_cell);
    }
  }
  return [...after.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([day, cells]) => ({
    date: day, expected_revision: rows.get(day).revision,
    cells: [...cells].sort((a, b) => a - b)
  })).filter(change => JSON.stringify(change.cells) !==
    JSON.stringify(rows.get(change.date).blocked_cells));
}
