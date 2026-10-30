/* SkyLink Airlines - client-side enhancements.
 * All validation here is a convenience for the user; the Flask server
 * re-validates every submission.
 */
document.addEventListener("DOMContentLoaded", function () {

    /* ---------- Mobile navigation ---------- */
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("main-nav");
    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            var open = nav.classList.toggle("open");
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
        });
    }

    /* ---------- Dismissible flash messages ---------- */
    document.querySelectorAll(".flash-close").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.parentElement.remove(); });
    });

    /* ---------- Print ticket ---------- */
    document.querySelectorAll("[data-print]").forEach(function (btn) {
        btn.addEventListener("click", function () { window.print(); });
    });

    /* ---------- Confirm dangerous actions ---------- */
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
        form.addEventListener("submit", function (e) {
            if (!window.confirm(form.getAttribute("data-confirm"))) { e.preventDefault(); }
        });
    });

    /* ---------- Search form: dates, trip type, swap ---------- */
    document.querySelectorAll(".search-form").forEach(function (form) {
        var dep = form.querySelector("#departure_date");
        var ret = form.querySelector("#return_date");
        var retGroup = form.querySelector(".return-group");
        var tripRadios = form.querySelectorAll("input[name='trip']");

        function syncTrip() {
            var isReturn = form.querySelector("input[name='trip']:checked").value === "return";
            ret.disabled = !isReturn;
            if (!isReturn) { ret.value = ""; }
            retGroup.classList.toggle("is-hidden", !isReturn);
        }
        tripRadios.forEach(function (r) { r.addEventListener("change", syncTrip); });
        syncTrip();

        // The return date can never be before the departure date.
        dep.addEventListener("change", function () {
            ret.min = dep.value || ret.min;
            if (ret.value && ret.value < dep.value) { ret.value = dep.value; }
        });

        var swap = form.querySelector(".swap-btn");
        if (swap) {
            swap.addEventListener("click", function () {
                var from = form.querySelector("#from"), to = form.querySelector("#to");
                var tmp = from.value; from.value = to.value; to.value = tmp;
            });
        }
    });

    /* ---------- Generic client-side validation ---------- */
    var emailRe = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

    function showError(input, message) {
        var group = input.closest(".form-group") || input.parentElement;
        var existing = group.querySelector(".field-error.js");
        if (existing) { existing.remove(); }
        input.classList.toggle("is-invalid", !!message);
        if (message) {
            var span = document.createElement("span");
            span.className = "field-error js";
            span.textContent = message;
            group.appendChild(span);
        }
    }

    function validateInput(input) {
        var v = (input.value || "").trim();
        if (input.disabled) { return ""; }
        if (input.type === "checkbox") { return input.required && !input.checked ? "Please tick this box to continue." : ""; }
        if (input.required && !v) { return "This field is required."; }
        if (!v) { return ""; }
        if (input.type === "email" && !emailRe.test(v)) { return "Enter a valid email address."; }
        if (input.type === "date" && input.min && v < input.min) { return "The date cannot be in the past."; }
        if (input.type === "number") {
            var n = Number(v);
            if (!Number.isInteger(n)) { return "Enter a whole number."; }
            if (input.min !== "" && n < Number(input.min)) { return "Must be at least " + input.min + "."; }
            if (input.max !== "" && n > Number(input.max)) { return "Must be at most " + input.max + "."; }
        }
        if (input.pattern && !new RegExp("^(?:" + input.pattern + ")$").test(v)) { return "Please use the correct format."; }
        if (input.minLength > 0 && v.length < input.minLength) { return "Please enter at least " + input.minLength + " characters."; }
        return "";
    }

    document.querySelectorAll("form[data-validate]").forEach(function (form) {
        form.addEventListener("submit", function (e) {
            var firstBad = null;
            form.querySelectorAll("input, select, textarea").forEach(function (input) {
                if (input.type === "hidden" || input.type === "radio") { return; }
                var msg = validateInput(input);
                showError(input, msg);
                if (msg && !firstBad) { firstBad = input; }
            });
            var from = form.querySelector("#from"), to = form.querySelector("#to");
            if (from && to && from.value.trim() && from.value.trim().toLowerCase() === to.value.trim().toLowerCase()) {
                showError(to, "Destination must be different from departure.");
                firstBad = firstBad || to;
            }
            if (firstBad) {
                e.preventDefault();
                firstBad.focus();
            }
        });
        form.querySelectorAll("input, select, textarea").forEach(function (input) {
            input.addEventListener("blur", function () {
                if (input.classList.contains("is-invalid")) { showError(input, validateInput(input)); }
            });
        });
    });

    /* ---------- Booking form: automatic total cost ---------- */
    var bookingForm = document.getElementById("booking-form");
    if (bookingForm) {
        var cls = bookingForm.querySelector("#travel_class");
        var pax = bookingForm.querySelector("#passengers");
        var fmt = function (n) { return "KSh " + Number(n).toLocaleString("en-KE"); };

        function updateTotal() {
            var opt = cls.options[cls.selectedIndex];
            if (!opt) { return; }
            var price = Number(opt.getAttribute("data-price"));
            var seats = Number(opt.getAttribute("data-seats"));
            var count = parseInt(pax.value, 10);
            pax.max = Math.min(9, seats);
            document.getElementById("sum-price").textContent = fmt(price);
            document.getElementById("sum-class").textContent = opt.value;
            if (!count || count < 1) {
                document.getElementById("sum-pax").textContent = "-";
                document.getElementById("sum-total").textContent = "-";
                document.getElementById("sum-formula").textContent = "";
                return;
            }
            document.getElementById("sum-pax").textContent = count;
            document.getElementById("sum-total").textContent = fmt(price * count);
            document.getElementById("sum-formula").textContent =
                fmt(price) + " × " + count + " passenger" + (count > 1 ? "s" : "") + " = " + fmt(price * count);
        }
        cls.addEventListener("change", updateTotal);
        pax.addEventListener("input", updateTotal);
        updateTotal();
    }

    /* ---------- Admin flight form: auto-fill airport from city ---------- */
    document.querySelectorAll("[data-airport-target]").forEach(function (input) {
        input.addEventListener("change", function () {
            var target = document.getElementById(input.getAttribute("data-airport-target"));
            var list = document.getElementById(input.getAttribute("list"));
            if (!target || !list || target.value) { return; }
            list.querySelectorAll("option").forEach(function (o) {
                if (o.value.toLowerCase() === input.value.trim().toLowerCase()) { target.value = o.getAttribute("data-airport"); }
            });
        });
    });
});
