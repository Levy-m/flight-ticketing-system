/* SkyLink Assistant - chat widget.
 * Sends each message to /chat (Flask) and shows the reply. Replies are inserted
 * with textContent only, never innerHTML, so nothing in a message can inject markup.
 * The conversation is kept in sessionStorage so it survives moving between pages.
 */
(function () {
    "use strict";
    var root = document.getElementById("chatbot");
    if (!root) { return; }

    var panel = root.querySelector(".chatbot-panel");
    var toggle = root.querySelector(".chatbot-toggle");
    var log = root.querySelector(".chatbot-log");
    var chips = root.querySelector(".chatbot-chips");
    var form = root.querySelector(".chatbot-form");
    var input = root.querySelector("#chatbot-input");
    var STORE_KEY = "skylink-chat";
    var history = [];   // [{who: "user"|"bot", text, items, links}]
    var currentChips = [];
    var busy = false;

    function load() {
        try { return JSON.parse(sessionStorage.getItem(STORE_KEY)) || {}; } catch (e) { return {}; }
    }
    function save(open) {
        try {
            sessionStorage.setItem(STORE_KEY, JSON.stringify({ history: history.slice(-40), open: open,
                                                               chips: currentChips }));
        } catch (e) { /* storage unavailable: the chat still works for this page */ }
    }

    /* ---------- rendering ---------- */
    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text) { node.textContent = text; }
        return node;
    }

    function render(msg) {
        var bubble = el("div", "chat-msg chat-" + msg.who);
        (msg.text || "").split("\n").forEach(function (line) {
            if (line) { bubble.appendChild(el("p", null, line)); }
        });
        if (msg.items && msg.items.length) {
            var ul = el("ul");
            msg.items.forEach(function (item) { ul.appendChild(el("li", null, item)); });
            bubble.appendChild(ul);
        }
        if (msg.links && msg.links.length) {
            var actions = el("div", "chat-links");
            msg.links.forEach(function (l) {
                if (typeof l.url !== "string" || l.url.charAt(0) !== "/") { return; }  // site links only
                var a = el("a", "chat-link", l.label);
                a.setAttribute("href", l.url);
                actions.appendChild(a);
            });
            bubble.appendChild(actions);
        }
        log.appendChild(bubble);
        log.scrollTop = log.scrollHeight;
    }

    function showChips(list) {
        currentChips = list || [];
        chips.textContent = "";
        currentChips.forEach(function (text) {
            var b = el("button", "chat-chip", text);
            b.type = "button";
            b.addEventListener("click", function () { send(text); });
            chips.appendChild(b);
        });
    }

    function add(msg) {
        history.push(msg);
        render(msg);
    }

    /* ---------- talking to the server ---------- */
    function send(text) {
        text = (text || "").trim();
        if (busy || (!text && history.length)) { return; }
        busy = true;
        if (text) { add({ who: "user", text: text }); }
        showChips([]);
        var typing = el("div", "chat-msg chat-bot chat-typing");
        typing.setAttribute("aria-label", "Assistant is typing");
        typing.appendChild(el("span")); typing.appendChild(el("span")); typing.appendChild(el("span"));
        log.appendChild(typing);
        log.scrollTop = log.scrollHeight;

        var body = new FormData();
        body.append("message", text);
        body.append("csrf_token", root.getAttribute("data-csrf"));
        fetch(root.getAttribute("data-url"), { method: "POST", body: body, credentials: "same-origin" })
            .then(function (res) {
                if (!res.ok) { throw new Error("HTTP " + res.status); }
                return res.json();
            })
            .then(function (reply) {
                add({ who: "bot", text: reply.text, items: reply.items, links: reply.links });
                showChips(reply.suggestions);
            })
            .catch(function () {
                add({ who: "bot", text: "Sorry, I couldn't reach the server. Please reload the page and try again, " +
                                     "or call us on +254 700 000 000." });
            })
            .then(function () {
                typing.remove();
                busy = false;
                save(true);
                input.focus();
            });
    }

    /* ---------- open / close ---------- */
    function setOpen(open) {
        panel.hidden = !open;
        root.classList.toggle("is-open", open);
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
            if (!history.length) { send(""); }  // fetch the greeting
            input.focus();
            log.scrollTop = log.scrollHeight;
        } else {
            toggle.focus();
        }
        save(open);
    }

    toggle.addEventListener("click", function () { setOpen(panel.hidden); });
    root.querySelector("[data-chat-close]").addEventListener("click", function () { setOpen(false); });
    root.querySelector("[data-chat-reset]").addEventListener("click", function () {
        history = [];
        log.textContent = "";
        send("");
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !panel.hidden) { setOpen(false); }
    });
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = input.value;
        input.value = "";
        send(text);
    });

    /* ---------- restore the conversation from the previous page ---------- */
    var saved = load();
    (saved.history || []).forEach(add);
    showChips(saved.chips);
    if (saved.open) { setOpen(true); }
})();
