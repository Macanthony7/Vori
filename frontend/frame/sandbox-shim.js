// Loaded first in the seed frame. In a sandboxed (opaque-origin) iframe, even reading
// window.localStorage throws, and the bundled libraries probe it at load time. Replace it with a
// throwaway in-memory store: nothing in this frame can ever be persisted.
(function () {
    "use strict";
    // Errors inside a sandboxed frame are invisible to the page; report them (message text only,
    // never any input) so the user sees "frame failed" instead of a blank box.
    window.addEventListener("error", function (e) {
        window.parent.postMessage({ type: "frame-error", message: String(e.message).slice(0, 200) }, "*");
    });
    try {
        window.localStorage; // eslint-disable-line no-unused-expressions
        return; // storage works (frame opened directly, not sandboxed); leave it alone
    } catch (e) { /* sandboxed: fall through */ }
    var data = {};
    var memory = {
        getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
        setItem: function (k, v) { data[k] = String(v); },
        removeItem: function (k) { delete data[k]; },
        clear: function () { data = {}; },
        key: function (i) { return Object.keys(data)[i] || null; },
        get length() { return Object.keys(data).length; }
    };
    Object.defineProperty(window, "localStorage", { value: memory, configurable: true });
})();
