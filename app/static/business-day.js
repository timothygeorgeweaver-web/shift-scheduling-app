import {addDays} from "/static/planner-grid.js?v=2";

export function rolloverMinute(value = 0) {
  if (!Number.isInteger(value) || value < 0 || value >= 1440 || value % 15) {
    throw new Error("Invalid business-day rollover.");
  }
  return value;
}

export function businessDateForLocal(local, rollover = 0) {
  rolloverMinute(rollover);
  const minute = Number(local.slice(11, 13)) * 60 + Number(local.slice(14, 16));
  const day = local.slice(0, 10);
  return minute < rollover ? addDays(day, -1) : day;
}

export function calendarDateForCell(day, minute, rollover = 0) {
  rolloverMinute(rollover);
  if (!Number.isInteger(minute) || minute < 0 || minute >= 1440) {
    throw new Error("Invalid local clock minute.");
  }
  return minute < rollover ? addDays(day, 1) : day;
}
