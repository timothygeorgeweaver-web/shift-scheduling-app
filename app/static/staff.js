"use strict";

export function createStaffManager({api, expired}) {
  const root = document.createElement("section");
  root.className = "team";
  root.hidden = true;
  document.getElementById("account-view").append(root);
  let generation = 0;

  function element(tag, text = "", className = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }

  function reset() {
    generation += 1;
    root.replaceChildren();
    root.hidden = true;
  }

  function start(identity) {
    reset();
    if (!identity.memberships.some(m =>
      m.permissions.some(p => ["administrator", "scheduler"].includes(p)))) return;

    const current = generation;
    const alive = () => generation === current;
    const token = identity.csrf_token;
    root.hidden = false;

    root.append(element("h2", "Team"));
    root.append(element("p",
      "Manage employees, job roles, and role qualifications.",
      "description"));

    const status = element("p", "", "team-status");
    status.setAttribute("role", "status");
    root.append(status);

    const controls = element("fieldset", "", "team-controls");
    const legend = element("legend", "Location");
    controls.append(legend);
    const location = document.createElement("select");
    location.setAttribute("aria-label", "Location");
    const refresh = element("button", "Refresh", "button secondary");
    refresh.type = "button";
    controls.append(location, refresh);
    root.append(controls);

    const content = element("div");
    root.append(content);
    let busy = false;

    async function request(path, method = "GET", body) {
      const response = await api("/api/v1/staff/" + path, {
        method,
        ...(body === undefined ? {} : {
          headers: {"Content-Type": "application/json", "X-CSRF-Token": token},
          body: JSON.stringify(body),
        }),
      });
      if (!alive()) throw new Error("Session changed.");
      if (response.status === 401) {
        expired();
        throw new Error("Please sign in again.");
      }
      if (!response.ok) {
        const messages = {
          403: "You do not have permission for this action. Try signing in again.",
          404: "This record is no longer available. Refresh the Team screen.",
          409: "The record changed, account details conflict, or the last active administrator would be removed. Refresh and check the details.",
          422: "Check the form fields. Account passwords require 12–1,024 characters.",
        };
        throw new Error(messages[response.status] ||
          "The request could not be completed. Refresh before trying again.");
      }
      return response.json();
    }

    function lock(value) {
      busy = value;
      controls.disabled = value;
      content.querySelectorAll("fieldset").forEach(node => {
        node.disabled = value;
      });
    }

    async function run(work) {
      if (busy || !alive()) return;
      lock(true);
      status.textContent = "Working…";
      try {
        await work();
      } catch (error) {
        if (alive()) status.textContent =
          error instanceof TypeError || error.name === "AbortError"
            ? "Connection interrupted. Refresh to check whether your change saved."
            : error.message;
      } finally {
        if (alive()) lock(false);
      }
    }

    function base() {
      return "locations/" + encodeURIComponent(location.value) + "/";
    }

    function field(labelText, input) {
      const label = element("label", labelText);
      label.append(input);
      return label;
    }

    function textInput(value = "") {
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 200;
      input.required = true;
      input.value = value;
      return input;
    }

    function checkbox(value) {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = value;
      return input;
    }

    function submit(text) {
      const button = element("button", text, "button secondary");
      button.type = "submit";
      return button;
    }

    async function save(path, method, body) {
      const saved = await request(path, method, body);
      if (path.endsWith("/settings") && alive()) {
        location.selectedOptions[0].textContent =
          saved.business_name + " / " + saved.location_name;
      }
      // Clear stale revision controls as soon as a write is confirmed.
      content.replaceChildren();
      status.textContent = "Saved. Refreshing…";
      try {
        await load();
        if (alive()) status.textContent = "Saved.";
      } catch {
        if (alive()) status.textContent =
          "Saved, but the list could not refresh. Select Refresh to continue.";
      }
    }

    function addForm(kind, heading) {
      const form = document.createElement("form");
      const fields = document.createElement("fieldset");
      fields.append(element("legend", heading));
      const name = textInput();
      fields.append(field("Name", name), submit("Add"));
      form.append(fields);
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (!name.value.trim()) {
          name.setCustomValidity("Enter a name.");
          name.reportValidity();
          return;
        }
        run(() => save(base() + kind, "POST", {name: name.value.trim()}));
      });
      name.addEventListener("input", () => name.setCustomValidity(""));
      return form;
    }

    function editForm(record, kind) {
      const form = document.createElement("form");
      const fields = document.createElement("fieldset");
      fields.append(element("legend", "Edit details"));
      const name = textInput(record.display_name ?? record.name);
      const active = checkbox(record.active);
      fields.append(field("Name", name), field("Active", active),
        submit("Save details"));
      form.append(fields);
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (!name.value.trim()) {
          name.setCustomValidity("Enter a name.");
          name.reportValidity();
          return;
        }
        run(() => save(base() + kind + "/" + record.id, "PATCH", {
          expected_revision: record.revision,
          name: name.value.trim(),
          active: active.checked,
        }));
      });
      name.addEventListener("input", () => name.setCustomValidity(""));
      return form;
    }


    function accountForm(employee) {
      const form = document.createElement("form");
      const fields = document.createElement("fieldset");
      fields.append(element("legend", "Create employee sign-in"));
      fields.append(element("p",
        "Creates employee access only. Use a password of 12–1,024 characters.",
        "description"));
      const email = textInput();
      email.type = "email";
      email.maxLength = 254;
      email.autocomplete = "off";
      const password = textInput();
      password.type = "password";
      password.minLength = 12;
      password.maxLength = 1024;
      password.autocomplete = "new-password";
      const confirm = password.cloneNode();
      fields.append(field("Sign-in email", email),
        field("Password", password), field("Confirm password", confirm),
        submit("Create sign-in"));
      form.append(fields);
      confirm.addEventListener("input", () => confirm.setCustomValidity(""));
      password.addEventListener("input", () => confirm.setCustomValidity(""));
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (busy) return;
        if (password.value !== confirm.value) {
          confirm.setCustomValidity("Passwords must match.");
          confirm.reportValidity();
          return;
        }
        const body = {
          email: email.value.trim(),
          password: password.value,
          expected_revision: employee.revision,
        };
        password.value = "";
        confirm.value = "";
        run(() => save(
          base() + "employees/" + employee.id + "/account", "POST", body
        ));
      });
      return form;
    }


    function accessControls(employee) {
      const container = document.createElement("div");
      const openerFields = document.createElement("fieldset");
      const opener = element("button", "Manage sign-in access", "button secondary");
      opener.type = "button";
      openerFields.append(opener);
      container.append(openerFields);
      opener.addEventListener("click", () => run(async () => {
        const path = base() + "employees/" + employee.id + "/account";
        const account = await request(path);
        if (!alive()) return;
        const form = document.createElement("form");
        const fields = document.createElement("fieldset");
        fields.append(element("legend", "Sign-in access"));
        fields.append(element("p", account.email, "description"));
        fields.append(element("p",
          "Access applies to this business. Saving signs this account out of all sessions.",
          "description"));
        const active = checkbox(account.active);
        fields.append(field("Allow sign-in for this business", active));
        const options = ["employee", "scheduler", "administrator"].map(permission => {
          const input = checkbox(account.permissions.includes(permission));
          fields.append(field(
            permission[0].toUpperCase() + permission.slice(1), input));
          return {permission, input};
        });
        if (!account.user_active) {
          fields.append(element("p", "This account is globally disabled.", "description"));
        }
        fields.append(submit("Save access"));
        form.append(fields);
        form.addEventListener("submit", event => {
          event.preventDefault();
          const permissions = options.filter(item => item.input.checked)
            .map(item => item.permission);
          if (!permissions.length) {
            status.textContent = "Select at least one permission.";
            return;
          }
          run(() => save(path, "PATCH", {
            expected_revision: account.revision,
            active: active.checked,
            permissions,
          }));
        });
        container.replaceChildren(form);
        status.textContent = "";
      }));
      return container;
    }


    function settingsControls() {
      const panel = document.createElement("details");
      panel.append(element("summary", "Business and location settings"));
      const container = document.createElement("div");
      const openerFields = document.createElement("fieldset");
      const opener = element("button", "Edit settings", "button secondary");
      opener.type = "button";
      openerFields.append(opener);
      container.append(openerFields);
      panel.append(container);
      opener.addEventListener("click", () => run(async () => {
        const path = base() + "settings";
        const settings = await request(path);
        if (!alive()) return;
        const form = document.createElement("form");
        const fields = document.createElement("fieldset");
        fields.append(element("legend", "Business and location"));
        const business = textInput(settings.business_name);
        const name = textInput(settings.location_name);
        const timezone = textInput(settings.timezone);
        timezone.maxLength = 100;
        fields.append(field("Business name", business),
          field("Location name", name), field("Timezone", timezone));
        fields.append(element("p",
          "Pacific time: America/Los_Angeles", "description"));
        fields.append(submit("Save settings"));
        form.append(fields);
        form.addEventListener("submit", event => {
          event.preventDefault();
          run(() => save(path, "PATCH", {
            expected_organization_revision: settings.organization_revision,
            expected_location_revision: settings.location_revision,
            business_name: business.value.trim(),
            location_name: name.value.trim(),
            timezone: timezone.value.trim(),
          }));
        });
        container.replaceChildren(form);
        status.textContent = "";
      }));
      return panel;
    }

    async function load() {
      const prefix = base();
      const [employees, roles, qualifications, history] = await Promise.all(
        ["employees", "roles", "qualifications", "history?limit=20"]
          .map(path => request(prefix + path))
      );
      if (!alive()) return;
      const fragment = document.createDocumentFragment();
      if (location.selectedOptions[0]?.dataset.admin === "true") {
        fragment.append(settingsControls());
      }
      const columns = element("div", "", "team-columns");
      const staff = element("section");
      staff.append(element("h3", "Employees"), addForm("employees", "Add employee"));
      if (!employees.length) staff.append(element("p", "No employees added yet."));
      for (const employee of employees) {
        const details = document.createElement("details");
        details.append(element("summary",
          employee.display_name + (employee.active ? "" : " · Inactive")));
        details.append(editForm(employee, "employees"));
        const canAdmin = location.selectedOptions[0]?.dataset.admin === "true";
        if (employee.has_account) {
          details.append(element("p", "Employee sign-in is linked.", "description"));
          if (canAdmin) details.append(accessControls(employee));
        } else if (canAdmin && employee.active) {
          details.append(accountForm(employee));
        } else if (canAdmin) {
          details.append(element("p",
            "Activate this employee before creating a sign-in.", "description"));
        }
        const fields = document.createElement("fieldset");
        fields.append(element("legend", "Qualifications"));
        fields.append(element("p",
          "Each checkbox change saves immediately.", "description"));
        if (!roles.length) fields.append(element("p", "Add a job role first."));
        for (const role of roles) {
          const qualification = qualifications.find(q =>
            q.employee_id === employee.id && q.job_role_id === role.id);
          const input = checkbox(qualification?.active ?? false);
          fields.append(field(role.name + (role.active ? "" : " · Inactive role"), input));
          input.addEventListener("change", () => {
            const selected = input.checked;
            input.checked = qualification?.active ?? false;
            run(() => save(
              prefix + "employees/" + employee.id + "/qualifications/" + role.id,
              "PUT", {
                expected_revision: qualification?.revision ?? 0,
                active: selected,
              }
            ));
          });
        }
        details.append(fields);
        staff.append(details);
      }
      const jobs = element("section");
      jobs.append(element("h3", "Job roles"), addForm("roles", "Add job role"));
      if (!roles.length) jobs.append(element("p", "No job roles added yet."));
      for (const role of roles) {
        const details = document.createElement("details");
        details.append(element("summary",
          role.name + (role.active ? "" : " · Inactive")));
        details.append(editForm(role, "roles"));
        jobs.append(details);
      }
      columns.append(staff, jobs);
      fragment.append(columns);

      const changes = document.createElement("details");
      changes.append(element("summary", "Recent changes"));
      const list = document.createElement("ol");
      for (const event of history) {
        const after = event.after_data;
        const employee = employees.find(e => e.id === after.employee_id);
        const role = roles.find(r => r.id === after.job_role_id);
        const subject = after.display_name ?? after.name ??
          [employee?.display_name, role?.name].filter(Boolean).join(" / ");
        const time = new Date(event.created_at).toLocaleString();
        list.append(element("li",
          `${time} — ${event.action}: ${subject || "Staff record"} (revision ${after.revision})`));
      }
      if (!history.length) changes.append(element("p", "No changes recorded yet."));
      changes.append(list);
      fragment.append(changes);
      content.replaceChildren(fragment);
    }

    refresh.addEventListener("click", () => run(async () => {
      await load();
      status.textContent = "Up to date.";
    }));
    location.addEventListener("change", () => {
      content.replaceChildren();
      run(async () => {
        await load();
        status.textContent = "";
      });
    });

    run(async () => {
      const locations = await request("locations");
      for (const item of locations) {
        const option = element("option", item.organization_name + " / " + item.name);
        option.value = item.id;
        option.dataset.admin = String(identity.memberships.some(m =>
          m.organization_id === item.organization_id &&
          m.permissions.includes("administrator")));
        location.append(option);
      }
      if (locations.length) {
        await load();
        status.textContent = "";
      } else {
        status.textContent = "No accessible locations.";
      }
    });
  }

  return {start, reset};
}
