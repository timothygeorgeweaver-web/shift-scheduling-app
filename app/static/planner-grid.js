export function node(tag, text = "", parent) {
  const result = document.createElement(tag);
  result.textContent = text;
  if (parent) parent.append(result);
  return result;
}

export function action(text, parent, handler) {
  const button = node("button", text, parent);
  button.type = "button";
  button.className = "button secondary";
  button.addEventListener("click", handler);
  return button;
}

export function addDays(value, count) {
  const day = new Date(value + "T12:00:00Z");
  day.setUTCDate(day.getUTCDate() + count);
  return day.toISOString().slice(0, 10);
}

export function weekday(value) {
  return (new Date(value + "T12:00:00Z").getUTCDay() + 6) % 7;
}

export function monthHeading(first) {
  const last = addDays(first, 6);
  const month = value => new Intl.DateTimeFormat("en-US", {
    month: "long", timeZone: "UTC"
  }).format(new Date(value + "T12:00:00Z"));
  const firstYear = first.slice(0, 4), lastYear = last.slice(0, 4);
  if (first.slice(0, 7) === last.slice(0, 7)) {
    return `${month(first)} ${firstYear}`;
  }
  if (firstYear === lastYear) {
    return `${month(first)} / ${month(last)} ${firstYear}`;
  }
  return `${month(first)} ${firstYear} / ${month(last)} ${lastYear}`;
}

export function clock(minute) {
  return `${Math.floor(minute / 60).toString().padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`;
}

export function createPlannerGrid(parent, days, changed, initialScroll = 0) {
  const scroll = node("div", "", parent);
  scroll.className = "planner-scroll";
  scroll.tabIndex = -1;
  const table = node("table", "", scroll);
  table.className = "planner-table";
  table.setAttribute("aria-label", "Seven-day unavailability planner");
  const head = node("thead", "", table);
  const header = node("tr", "", head);
  node("th", "Time", header).scope = "col";
  const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const selected = days.map(day => new Set(day.effective_cells || []));
  const buttons = Array.from({length: 7}, () => []);
  let enabled = true, painting = false, paintValue = false;
  let previous = null, visited = new Set(), suppressUntil = 0, focused = null;

  days.forEach(day => {
    const cell = node("th", "", header);
    cell.scope = "col";
    node("strong", names[weekday(day.date)], cell);
    node("span", day.date, cell);
    node("small", day.editable ? "Editable" : "Locked", cell);
    if (day.effective_cells === null) {
      node("small", "Exact locked times: " + day.blocked.map(
        ([start, end]) => `${clock(start)}–${clock(end)}`
      ).join(", "), cell);
    }
  });

  function isBlocked(dayIndex, cell) {
    if (days[dayIndex].effective_cells !== null) {
      return selected[dayIndex].has(cell);
    }
    return days[dayIndex].blocked.some(
      ([start, end]) => start < (cell + 1) * 15 && cell * 15 < end
    );
  }

  function refresh(dayIndex, cell) {
    const button = buttons[dayIndex][cell];
    const blocked = isBlocked(dayIndex, cell);
    const locked = !days[dayIndex].editable;
    button.classList.toggle("is-blocked", blocked);
    button.classList.toggle("is-locked", locked);
    button.classList.toggle("is-partial", days[dayIndex].effective_cells === null && blocked);
    button.textContent = blocked ? (days[dayIndex].effective_cells === null ? "◐" : "×") : "";
    button.setAttribute("aria-pressed", String(blocked));
    button.setAttribute("aria-disabled", String(locked || !enabled));
    const description = `${days[dayIndex].date} ${clock(cell * 15)}–${clock((cell + 1) * 15)}: ` +
      (locked ? "locked, " : "") + (blocked ? "unavailable" : "available");
    button.setAttribute("aria-label", description);
    button.title = description;
  }

  const body = node("tbody", "", table);
  for (let cell = 0; cell < 96; cell++) {
    const row = node("tr", "", body);
    if (cell % 4 === 0) row.className = "planner-hour";
    const time = node("th", clock(cell * 15), row);
    time.scope = "row";
    for (let dayIndex = 0; dayIndex < 7; dayIndex++) {
      const td = node("td", "", row);
      const button = node("button", "", td);
      button.type = "button";
      button.className = "planner-cell";
      button.dataset.day = String(dayIndex);
      button.dataset.cell = String(cell);
      button.tabIndex = dayIndex === 0 && cell === 0 ? 0 : -1;
      buttons[dayIndex][cell] = button;
      refresh(dayIndex, cell);
    }
  }

  function target(event) {
    const button = event.target.closest?.(".planner-cell");
    return button && table.contains(button) ? button : null;
  }

  function setCell(dayIndex, cell, value) {
    if (!enabled || !days[dayIndex].editable) return;
    const key = `${dayIndex}:${cell}`;
    if (painting && visited.has(key)) return;
    visited.add(key);
    if (value) selected[dayIndex].add(cell);
    else selected[dayIndex].delete(cell);
    refresh(dayIndex, cell);
    changed(dayIndex, Array.from(selected[dayIndex]).sort((a, b) => a - b));
  }

  table.addEventListener("pointerdown", event => {
    if (event.pointerType === "touch" || event.button !== 0) return;
    const button = target(event);
    if (!button || !enabled) return;
    const d = Number(button.dataset.day), c = Number(button.dataset.cell);
    if (!days[d].editable) return;
    event.preventDefault();
    button.focus();
    painting = true;
    visited = new Set();
    paintValue = !selected[d].has(c);
    previous = [d, c];
    setCell(d, c, paintValue);
  });

  table.addEventListener("pointerover", event => {
    if (!painting || !(event.buttons & 1)) return;
    const button = target(event);
    if (!button) return;
    const d = Number(button.dataset.day), c = Number(button.dataset.cell);
    if (previous && previous[0] === d) {
      const low = Math.min(previous[1], c), high = Math.max(previous[1], c);
      for (let n = low; n <= high; n++) setCell(d, n, paintValue);
    } else {
      setCell(d, c, paintValue);
    }
    previous = [d, c];
  });

  function finish() {
    if (painting) suppressUntil = Date.now() + 250;
    painting = false;
    previous = null;
    visited = new Set();
  }
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);

  table.addEventListener("click", event => {
    const button = target(event);
    if (!button || (event.detail > 0 && Date.now() < suppressUntil)) return;
    const d = Number(button.dataset.day), c = Number(button.dataset.cell);
    setCell(d, c, !selected[d].has(c));
  });

  table.addEventListener("focusin", event => {
    const button = target(event);
    if (!button) return;
    if (focused) focused.tabIndex = -1;
    else buttons[0][0].tabIndex = -1;
    focused = button;
    button.tabIndex = 0;
  });

  table.addEventListener("keydown", event => {
    const button = target(event);
    if (!button) return;
    const movement = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0],
      ArrowUp: [0, -1], ArrowDown: [0, 1]
    }[event.key];
    if (!movement) return;
    event.preventDefault();
    const d = Math.max(0, Math.min(6, Number(button.dataset.day) + movement[0]));
    const c = Math.max(0, Math.min(95, Number(button.dataset.cell) + movement[1]));
    buttons[d][c].focus();
  });

  scroll.scrollTop = initialScroll;
  return {
    scroll,
    setEnabled(value) {
      enabled = value;
      if (!value) finish();
      for (let d = 0; d < 7; d++) {
        for (let c = 0; c < 96; c++) refresh(d, c);
      }
    },
    destroy() {
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    }
  };
}
