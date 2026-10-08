/* Trial bot. Torchy stays on /api/chat and in chat.js. */
(() => {
  const panel = document.querySelector("#chat-panel-v2");
  const launcher = document.querySelector("#chat-launcher-v2");
  const form = document.querySelector("#chat-form-v2");
  const input = document.querySelector("#chat-input-v2");
  const log = document.querySelector("#chat-log-v2");
  const status = document.querySelector("#chat-status-v2");
  const contextLabel = document.querySelector("#chat-context-v2");
  const suggestions = document.querySelector("#chat-suggestions-v2");
  let selectedVenue = null;
  let discussedVenue = null;
  let busy = false;
  const history = [];

  const samples = [
    "How many incidents were reported near Peacock Theater?",
    "Wet days versus dry days near Peacock Theater",
    "What is the weather now at Peacock Theater?",
    "Are there Metro advisories near Peacock Theater?",
  ];

  function renderContext() {
    const venue = selectedVenue || discussedVenue;
    contextLabel.textContent = venue
      ? `“This venue” refers to ${venue.venue_name}.`
      : "No venue selected. Include a venue name in your question.";
  }

  window.addEventListener("venue-context", (event) => {
    selectedVenue = event.detail;
    queueMicrotask(renderContext);
  });

  function setOpen(open) {
    panel.hidden = !open;
    launcher.hidden = open;
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      const other = document.querySelector("#chat-panel");
      const otherLauncher = document.querySelector("#chat-launcher");
      if (other) other.hidden = true;
      if (otherLauncher) {
        otherLauncher.hidden = false;
        otherLauncher.setAttribute("aria-expanded", "false");
      }
      input.focus();
    } else launcher.focus();
  }

  launcher.addEventListener("click", () => setOpen(true));
  document.querySelector("#chat-close-v2").addEventListener("click", () => setOpen(false));

  function messageNode(role, content) {
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${role}`;
    const author = document.createElement("strong");
    author.className = "chat-author";
    author.textContent = role === "user" ? "You" : "v2";
    article.append(author);
    const body = document.createElement("p");
    body.textContent = content;
    article.append(body);
    log.append(article);
    log.scrollTop = log.scrollHeight;
    return article;
  }

  function addLine(article, className, content) {
    if (!content) return;
    const note = document.createElement("p");
    note.className = className;
    note.textContent = content;
    article.append(note);
  }

  function questionButton(label, message) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.chatV2Message = message;
    button.textContent = label;
    button.addEventListener("click", () => send(message));
    return button;
  }

  for (const sample of samples) suggestions.append(questionButton(sample, sample));

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    form.querySelector("button").disabled = value;
    panel.querySelectorAll("[data-chat-v2-message]").forEach((button) => {
      button.disabled = value;
    });
    status.textContent = value ? "Calculating from venue data…" : "";
  }

  async function send(message) {
    message = message.trim();
    if (!message || busy) return;
    messageNode("user", message);
    input.value = "";
    setBusy(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const context = selectedVenue || discussedVenue;
      const response = await fetch("/api/chat/v2/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          venue_id: context?.venue_id || null,
          history: history.slice(-6),
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error("empty");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let article = null;
      let body = null;
      while (true) {
        const step = await reader.read();
        buffer += decoder.decode(step.value || new Uint8Array(), { stream: !step.done });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const line = chunk.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (event.event === "template") {
            body = event;
            if (!body.answer) throw new Error("empty");
            article = messageNode("assistant", body.answer);
            if (body.table?.columns && body.table.rows) {
              const table = document.createElement("table");
              table.className = "chat-table";
              const head = document.createElement("tr");
              for (const column of body.table.columns) {
                const cell = document.createElement("th");
                cell.textContent = column;
                head.append(cell);
              }
              table.append(head);
              for (const row of body.table.rows) {
                const line = document.createElement("tr");
                for (const value of row) {
                  const cell = document.createElement("td");
                  cell.textContent = value;
                  line.append(cell);
                }
                table.append(line);
              }
              article.append(table);
            }
            addLine(article, "chat-caveat", body.confidence?.text);
            addLine(article, "chat-caveat", body.caveat);
            for (const link of body.links || []) {
              const anchor = document.createElement("a");
              anchor.className = "chat-jump";
              anchor.href = link.href;
              anchor.textContent = link.label;
              article.append(anchor);
            }
            log.scrollTop = log.scrollHeight;
          } else if (event.event === "narration" && article) {
            addLine(article, "chat-explanation", event.narration);
            log.scrollTop = log.scrollHeight;
          }
        }
        if (step.done) break;
      }
      if (!body?.answer) throw new Error("empty");
      if (body.choices?.length) {
        const choices = document.createElement("div");
        choices.className = "chat-choices";
        for (const choice of body.choices) {
          choices.append(questionButton(choice.venue_name, choice.message));
        }
        article.append(choices);
      }
      history.push({
        user_text: message,
        tool: body.tool || null,
        arguments: body.arguments || {},
      });
      if (history.length > 6) history.shift();
      if (body.status === "answered" && body.results?.length === 1) {
        discussedVenue = body.results[0];
        renderContext();
      }
      log.scrollTop = log.scrollHeight;
    } catch (error) {
      const reason = error.name === "AbortError"
        ? "The request timed out. The venue page still has the same figures."
        : "v2 could not be reached. Torchy is unchanged.";
      messageNode("assistant", reason);
      input.value = message;
    } finally {
      window.clearTimeout(timeout);
      setBusy(false);
      if (!panel.hidden) input.focus();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });
})();
