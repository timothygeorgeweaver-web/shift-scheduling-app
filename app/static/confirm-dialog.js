let pending = false;

export function confirmAction(message) {
  if (pending) return Promise.resolve(false);
  pending = true;
  const anchor = document.activeElement instanceof HTMLElement
    ? document.activeElement : document.body;
  const owner = anchor.closest("dialog, .team, .shift-editor") ?? anchor;

  return new Promise(resolve => {
    const dialog = document.createElement("dialog");
    dialog.className = "action-confirm-dialog";
    dialog.setAttribute("aria-label", "Confirm action");
    Object.assign(dialog.style, {
      position: "fixed", inset: "auto", margin: "0", boxSizing: "border-box",
      width: "340px", overflowY: "auto", padding: "18px",
      border: "1px solid #aebfca", borderRadius: "12px",
      background: "white", color: "#182b3a",
      boxShadow: "0 12px 42px #18344740", font: "inherit"
    });
    const heading = document.createElement("strong");
    heading.textContent = "Confirm action";
    const text = document.createElement("p");
    text.textContent = message;
    text.style.whiteSpace = "pre-line";
    text.style.overflowWrap = "anywhere";
    const buttons = document.createElement("div");
    Object.assign(buttons.style, {
      display: "flex", gap: "10px", flexWrap: "wrap"
    });
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "button secondary";
    cancel.textContent = "Cancel";
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "button";
    confirm.textContent = "Yes, continue";
    buttons.append(cancel, confirm);
    dialog.append(heading, text, buttons);
    document.body.append(dialog);
    let finished = false;

    function position() {
      if (!dialog.open) return;
      const view = window.visualViewport;
      const width = view?.width ?? window.innerWidth;
      const height = view?.height ?? window.innerHeight;
      const x = view?.offsetLeft ?? 0, y = view?.offsetTop ?? 0;
      const margin = 12, gap = 10;
      dialog.style.maxWidth = Math.max(1, width - 2 * margin) + "px";
      dialog.style.maxHeight = Math.max(1, height - 2 * margin) + "px";
      const box = dialog.getBoundingClientRect();
      const target = anchor.getBoundingClientRect();
      let left = x + (width - box.width) / 2;
      let top = y + (height - box.height) / 2;
      if (anchor !== document.body && target.width && target.height &&
          target.bottom > y && target.top < y + height) {
        top = target.top;
        if (target.right + gap + box.width <= x + width - margin) {
          left = target.right + gap;
        } else if (target.left - gap - box.width >= x + margin) {
          left = target.left - gap - box.width;
        } else {
          top = target.bottom + gap;
        }
      }
      dialog.style.left = Math.max(x + margin,
        Math.min(left, x + width - box.width - margin)) + "px";
      dialog.style.top = Math.max(y + margin,
        Math.min(top, y + height - box.height - margin)) + "px";
    }

    const observer = new MutationObserver(() => {
      if (!owner.isConnected) finish(false);
    });
    function finish(value) {
      if (finished) return;
      finished = true;
      observer.disconnect();
      window.removeEventListener("resize", position);
      window.visualViewport?.removeEventListener("resize", position);
      window.visualViewport?.removeEventListener("scroll", position);
      const accepted = Boolean(value && owner.isConnected);
      if (dialog.open) dialog.close();
      dialog.remove();
      pending = false;
      if (anchor.isConnected) anchor.focus({preventScroll: true});
      resolve(accepted);
    }

    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    dialog.addEventListener("cancel", event => {
      event.preventDefault();
      finish(false);
    });
    dialog.addEventListener("close", () => finish(false));
    observer.observe(document.body, {childList: true, subtree: true});
    window.addEventListener("resize", position);
    window.visualViewport?.addEventListener("resize", position);
    window.visualViewport?.addEventListener("scroll", position);
    dialog.showModal();
    position();
    cancel.focus({preventScroll: true});
  });
}
