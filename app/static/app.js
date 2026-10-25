import {createStaffManager} from "/static/staff.js?v=11";
import {createUnavailabilityManager} from "/static/unavailability.js?v=4";
import {createScheduleManager} from "/static/schedules.js?v=19";

(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  let csrfToken = "";
  const schedules = createScheduleManager({
    onEditorOpen: value => staff.setScheduleEditorOpen(value),
    api, expired: () => showLogin("Your session ended. Please sign in again.")
  });
  const unavailability = createUnavailabilityManager({
    api, expired: () => showLogin("Your session ended. Please sign in again.")
  });
  const staff = createStaffManager({api, onBusinessDaySaved: locationId => schedules.policySaved(locationId), expired: () => showLogin("Your session ended. Please sign in again.")});

  function message(id, text) {
    byId(id).textContent = text;
    byId(id).hidden = !text;
  }

  function showLogin(text = "") {
    staff.reset();
    unavailability.reset();
    schedules.reset();
    csrfToken = "";
    byId("loading").hidden = true;
    byId("account-view").hidden = true;
    byId("logout-button").hidden = true;
    byId("login-view").hidden = false;
    byId("password").value = "";
    byId("account-email").textContent = "";
    byId("account-permissions").replaceChildren();
    message("login-message", text);
  }

  function showAccount(identity) {
    csrfToken = identity.csrf_token;
    byId("account-email").textContent = identity.email;
    const permissions = identity.memberships[0]?.permissions || [];
    const labels = {
      administrator: "Administrator",
      scheduler: "Scheduler",
      employee: "Employee",
    };
    byId("account-permissions").replaceChildren();
    for (const permission of permissions) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = labels[permission] || permission;
      byId("account-permissions").append(badge);
    }
    if (!permissions.length) {
      byId("account-permissions").textContent = "No permissions assigned";
    }
    byId("loading").hidden = true;
    byId("login-view").hidden = true;
    byId("account-view").hidden = false;
    byId("logout-button").hidden = false;
    byId("password").value = "";
    message("account-message", "");
    staff.start(identity);
    unavailability.start(identity);
    schedules.start(identity);
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      return await fetch(path, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
  }

  async function loadIdentity() {
    const response = await api("/api/v1/auth/me");
    if (response.status === 401) {
      showLogin();
      return;
    }
    if (!response.ok) {
      throw new Error("Workspace unavailable");
    }
    showAccount(await response.json());
  }

  byId("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("login-button");
    button.disabled = true;
    button.textContent = "Signing in…";
    message("login-message", "");

    const body = JSON.stringify({
      email: byId("email").value.trim(),
      password: byId("password").value,
    });
    byId("password").value = "";

    try {
      const response = await api("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (response.status === 401) {
        message("login-message", "Email or password is incorrect.");
      } else if (response.status === 429) {
        message("login-message", "Too many attempts. Please wait a minute and try again.");
      } else if (!response.ok) {
        message("login-message", "Sign-in is unavailable. Please try again.");
      } else {
        await loadIdentity();
      }
    } catch {
      message("login-message", "We couldn’t connect. Please try again.");
    } finally {
      button.disabled = false;
      button.textContent = "Sign in";
      if (!byId("login-view").hidden) {
        byId("password").focus();
      }
    }
  });

  byId("logout-button").addEventListener("click", async () => {
    const button = byId("logout-button");
    button.disabled = true;
    message("account-message", "");
    try {
      const response = await api("/api/v1/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
      });
      if (response.ok || response.status === 401) {
        showLogin();
        byId("email").focus();
      } else {
        message("account-message", "We couldn’t sign you out. Please try again.");
      }
    } catch {
      message("account-message", "We couldn’t connect. Please try again.");
    } finally {
      button.disabled = false;
    }
  });

  loadIdentity().catch(() => {
    showLogin("We couldn’t open your workspace. Please try signing in.");
  });
})();
