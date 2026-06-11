(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  const React = SDK.React;
  const h = React.createElement;
  const C = SDK.components;
  const API = "/api/plugins/mix-workbench";

  function fmtTime(epochSeconds) {
    return new Date(epochSeconds * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  }

  function ImageCard(props) {
    const f = props.file;
    const url = API + "/file?path=" + encodeURIComponent(f.relpath);
    return h("a", { href: url, target: "_blank", rel: "noopener", className: "block" },
      h("div", { className: "border border-current/15 bg-background-base/70 p-2" },
        h("img", { src: url, alt: f.name, loading: "lazy", className: "w-full h-40 object-contain bg-black/20" }),
        h("div", { className: "mt-1 text-xs text-text-secondary truncate" }, f.name),
        h("div", { className: "text-[10px] text-midground" }, fmtTime(f.mtime))
      )
    );
  }

  function FileRow(props) {
    const f = props.file;
    const url = API + "/file?path=" + encodeURIComponent(f.relpath);
    return h("a", {
      href: url, target: "_blank", rel: "noopener",
      className: "flex items-center justify-between gap-3 border border-current/10 px-3 py-2 text-sm hover:bg-background-base/60",
    },
      h("span", { className: "truncate" }, f.relpath),
      h("span", { className: "shrink-0 text-[10px] text-midground" }, fmtTime(f.mtime))
    );
  }

  function Group(props) {
    const g = props.group;
    const images = g.files.filter(function (f) { return f.kind === "image"; });
    const others = g.files.filter(function (f) { return f.kind !== "image"; });
    return h("div", { className: "mt-6" },
      h("div", { className: "font-mondwest text-display text-sm uppercase tracking-[0.12em] text-midground" },
        g.label + " (" + g.files.length + ")"),
      images.length > 0 ? h("div", { className: "mt-3 grid grid-cols-2 md:grid-cols-4 gap-3" },
        images.map(function (f) { return h(ImageCard, { key: f.relpath, file: f }); })) : null,
      others.length > 0 ? h("div", { className: "mt-3 flex flex-col gap-1" },
        others.slice(0, 30).map(function (f) { return h(FileRow, { key: f.relpath, file: f }); })) : null,
      g.files.length === 0 ? h("p", { className: "mt-2 text-sm text-text-secondary" }, "No artifacts yet.") : null
    );
  }

  function MixWorkbenchPage() {
    const state = React.useState(null);
    const data = state[0]; const setData = state[1];
    const errState = React.useState(null);
    const err = errState[0]; const setErr = errState[1];

    const load = React.useCallback(function () {
      setErr(null);
      SDK.fetchJSON(API + "/runs")
        .then(setData)
        .catch(function (e) { setErr(String(e && e.message || e)); });
    }, [setData, setErr]);

    React.useEffect(function () { load(); }, [load]);

    return h("div", { className: "p-6 max-w-6xl" },
      h("div", { className: "flex items-center justify-between" },
        h("h1", { className: "font-mondwest text-display text-lg uppercase tracking-[0.12em]" }, "Mix Workbench"),
        h(C.Button || "button", { onClick: load, className: "text-xs" }, "Refresh")
      ),
      h("p", { className: "mt-1 text-sm text-text-secondary" },
        "Read-only view of mix-mono builder artifacts. UI screens refresh on every iOS pre-push."),
      err ? h("p", { className: "mt-4 text-sm text-red-400" }, "Failed to load: " + err) : null,
      data ? data.groups.map(function (g) { return h(Group, { key: g.id, group: g }); }) :
        (err ? null : h("p", { className: "mt-4 text-sm text-text-secondary" }, "Loading…"))
    );
  }

  window.__HERMES_PLUGINS__.register("mix-workbench", MixWorkbenchPage);
})();
