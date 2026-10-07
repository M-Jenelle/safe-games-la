/* The chatbot works independently of the map and its Google Maps API key. */
(() => {
  const panel = document.querySelector("#chat-panel");
  const form = document.querySelector("#chat-form");
  const input = document.querySelector("#chat-input");
  const log = document.querySelector("#chat-log");
  const status = document.querySelector("#chat-status");
  const contextLabel = document.querySelector("#chat-context");
  const engineLabel = document.querySelector("#chat-engine");
  let claudeConfigured = false;
  let selectedVenue = null;
  let discussedVenue = null;
  let busy = false;
  let priorMessage = "";

  function renderContext() {
    const venue = selectedVenue || discussedVenue;
    contextLabel.textContent = venue
      ? `“This venue” refers to ${venue.venue_name}.`
      : "No venue selected. Include a venue name in your question.";
  }

  window.addEventListener("venue-context", (event) => {
    selectedVenue = event.detail;
    // app.js also updates this label; run after its synchronous render.
    queueMicrotask(renderContext);
  });

  function splitSource(content) {
    const markers = ["\n\nSource:", "\n\nCrime context only", "\n\nNo answer was calculated"];
    let index = -1;
    for (const marker of markers) {
      const at = content.indexOf(marker);
      if (at !== -1 && (index === -1 || at < index)) index = at;
    }
    if (index === -1) return { text: content, source: "" };
    return { text: content.slice(0, index).trim(), source: content.slice(index).trim() };
  }

  function messageNode(role, content, engine = "data") {
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${role}`;
    const author = document.createElement("strong");
    author.className = "chat-author";
    author.textContent = role === "user" ? "You" : engine === "claude" ? "Claude · venue data" : "Data assistant";
    const byline = document.createElement("div");
    byline.className = "chat-byline";
    if (role === "assistant") {
      const avatar = document.createElement("span");
      avatar.className = "chat-message-avatar bot-avatar";
      avatar.setAttribute("aria-hidden", "true");
      const icon = document.createElement("img");
      icon.src = "/static/chat-icon.jpg";
      icon.alt = "";
      avatar.append(icon);
      byline.append(avatar);
    }
    byline.append(author);
    const parts = role === "assistant" ? splitSource(content) : { text: content, source: "" };
    const body = document.createElement("p");
    body.textContent = parts.text;
    article.append(byline, body);
    if (parts.source) {
      const details = document.createElement("details");
      details.className = "chat-source";
      const summary = document.createElement("summary");
      summary.textContent = "Source";
      const note = document.createElement("p");
      note.textContent = parts.source;
      details.append(summary, note);
      article.append(details);
    }
    log.append(article);
    log.scrollTop = log.scrollHeight;
    return article;
  }

  function questionButton(label, message) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.chatMessage = message;
    button.textContent = label;
    button.addEventListener("click", () => send(message));
    return button;
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    form.querySelector("button").disabled = value;
    panel.querySelectorAll("[data-chat-message]").forEach((button) => {
      button.disabled = value;
    });
    status.textContent = value
      ? claudeConfigured ? "Claude is interpreting your question…" : "Reading the processed venue data…"
      : "";
  }

  async function send(message) {
    message = message.trim();
    if (!message || busy) return;
    messageNode("user", message);
    input.value = "";
    setBusy(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 20000);
    try {
      const context = selectedVenue || discussedVenue;
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          venue_id: context?.venue_id || null,
          prior_message: priorMessage || null,
        }),
        signal: controller.signal,
      });
      const body = await response.json();
      if (!body.answer) throw new Error("The server could not process this question. Try again.");
      const article = messageNode("assistant", body.answer, body.engine);
      engineLabel.textContent = body.engine === "claude"
        ? "Claude · answers calculated from venue data"
        : body.engine === "fallback" ? "Claude fallback · venue data mode"
        : claudeConfigured ? "Claude enabled · answers calculated from venue data" : "Venue data mode";
      if (body.engine_note) {
        const note = document.createElement("p");
        note.className = "chat-engine";
        note.textContent = body.engine_note;
        article.append(note);
      }
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
      if (body.explanation) {
        const note = document.createElement("p");
        note.className = "chat-explanation";
        note.textContent = body.explanation;
        article.append(note);
      }
      if (body.table?.href) {
        const link = document.createElement("a");
        link.className = "chat-jump";
        link.href = body.table.href;
        link.textContent = body.table.link_label || "Open this section";
        article.append(link);
      }
      if (body.choices?.length) {
        const choices = document.createElement("div");
        choices.className = "chat-choices";
        choices.setAttribute("aria-label", "Choose a venue");
        for (const choice of body.choices) {
          choices.append(questionButton(choice.venue_name, choice.message));
        }
        article.append(choices);
      }
      priorMessage = body.resolved_message || message;
      if (body.status === "answered") {
        const venueIds = new Set(body.results.map((result) => result.venue_id));
        discussedVenue = venueIds.size === 1 ? body.results[0] : null;
        renderContext();
      }
      log.scrollTop = log.scrollHeight;
    } catch (error) {
      const reason = error.name === "AbortError"
        ? "The request timed out. Please try again."
        : "The chatbot could not be reached. Please retry after checking the server.";
      messageNode("assistant", `${reason}\n\nNo answer was calculated. Crime context only: LAPD crime reports via the LA Open Data Portal, 2020–2024, 800 m radius around each venue. Supporting datasets use separate sources and scopes.`);
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

  async function loadSuggestions() {
    try {
      const response = await fetch("/api/chat/suggestions");
      if (!response.ok) throw new Error("Suggestions unavailable");
      const body = await response.json();
      const suggestions = document.querySelector("#chat-suggestions");
      for (const question of body.questions) {
        suggestions.append(questionButton(question, question));
      }
    } catch {
      status.textContent = "Suggested questions could not load. You can still type a question.";
    }
  }

  loadSuggestions();
  fetch("/api/chat/config")
    .then((response) => response.ok ? response.json() : null)
    .then((config) => {
      claudeConfigured = Boolean(config?.claude_configured);
      engineLabel.textContent = claudeConfigured
        ? "Claude enabled · answers calculated from venue data"
        : "Venue data mode · Claude not configured";
    })
    .catch(() => { engineLabel.textContent = "Venue data mode"; });
  if (new URLSearchParams(window.location.search).get("chat") === "1") {
    setChatOpen(true);
  }
})();
