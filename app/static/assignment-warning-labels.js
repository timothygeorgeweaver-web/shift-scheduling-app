export function assignmentWarningLabels(assignment) {
  if (!assignment || assignment.review_status === "clear") return [];
  const hard = new Set(assignment.review_hard_blocks ?? []);
  const warnings = new Set(assignment.review_warnings ?? []);
  const reasons = new Set(assignment.review_reasons ?? []);
  const labels = [];
  if (hard.has("rest_before") || hard.has("rest_after")) {
    labels.push("Violates Rest Policy");
  }
  if (warnings.has("daily_hours")) labels.push("Violates Daily Hours Policy");
  if (warnings.has("unavailability")) labels.push("Availability Review Required");
  if (hard.has("overlap")) labels.push("Overlapping Assignment");
  if (hard.has("not_qualified")) labels.push("Qualification Required");
  if (hard.has("inactive_employee")) labels.push("Employee Inactive");
  if (hard.has("inactive_role")) labels.push("Job Type Inactive");
  if (reasons.has("saved_business_day_policy_mismatch")) {
    labels.push("Business-day Policy Conflict");
  }
  return labels.length ? labels : ["Assignment Check Required"];
}
