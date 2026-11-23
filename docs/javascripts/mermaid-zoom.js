/* Open mermaid diagrams in a full-size overlay.
 *
 * Mermaid 11 (via Material) renders the SVG into a closed shadow root, so
 * querySelector('svg') cannot see it. Capture the fence source before that
 * happens, bind click on the host, and re-render into the overlay with
 * mermaid.render (light DOM). glightbox still covers ordinary markdown images.
 */
(function () {
  const OVERLAY_ID = "mermaid-zoom-overlay";
  const sources = [];
  let observer = null;
  let lastFocus = null;

  function collectSources() {
    const els = Array.prototype.slice.call(
      document.querySelectorAll("pre.mermaid, .mermaid")
    );
    const texts = els.map(function (el) {
      const existing = (el.getAttribute("data-mermaid-source") || "").trim();
      const text = existing || (el.textContent || "").trim();
      if (text) el.setAttribute("data-mermaid-source", text);
      return text;
    }).filter(Boolean);
    if (texts.length && texts.length >= sources.length) {
      sources.length = 0;
      Array.prototype.push.apply(sources, texts);
    }
  }

  function ensureOverlay() {
    let overlay = document.getElementById(OVERLAY_ID);
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.className = "mermaid-zoom-overlay";
    overlay.setAttribute("hidden", "");
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "Enlarged diagram");

    const close = document.createElement("button");
    close.type = "button";
    close.className = "mermaid-zoom-close";
    close.setAttribute("aria-label", "Close enlarged diagram");
    close.textContent = "×";

    const stage = document.createElement("div");
    stage.className = "mermaid-zoom-stage";

    overlay.appendChild(close);
    overlay.appendChild(stage);
    document.body.appendChild(overlay);

    close.addEventListener("click", function (event) {
      event.stopPropagation();
      hideOverlay();
    });
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) hideOverlay();
    });
    return overlay;
  }

  function hideOverlay() {
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay || overlay.hasAttribute("hidden")) return;
    overlay.setAttribute("hidden", "");
    const stage = overlay.querySelector(".mermaid-zoom-stage");
    if (stage) stage.replaceChildren();
    document.body.style.overflow = "";
    document.removeEventListener("keydown", onKeydown, true);
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    lastFocus = null;
  }

  function onKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      hideOverlay();
    }
  }

  function sizeOverlaySvg(svg) {
    const raw = (svg.getAttribute("viewBox") || "").trim().split(/[\s,]+/).map(Number);
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    svg.style.maxWidth = "92vw";
    svg.style.maxHeight = "86vh";
    svg.style.width = "auto";
    svg.style.height = "auto";
    let w = raw.length === 4 ? raw[2] : 0;
    let h = raw.length === 4 ? raw[3] : 0;
    const maxW = Math.floor(window.innerWidth * 0.92);
    const maxH = Math.floor(window.innerHeight * 0.86);
    if (w > 0 && h > 0) {
      const scale = Math.min(maxW / w, maxH / h, 3);
      svg.setAttribute("width", String(Math.max(1, Math.round(w * scale))));
      svg.setAttribute("height", String(Math.max(1, Math.round(h * scale))));
      svg.style.width = svg.getAttribute("width") + "px";
      svg.style.height = svg.getAttribute("height") + "px";
    } else {
      svg.style.width = "92vw";
      svg.style.height = "auto";
    }
  }

  async function showSource(source) {
    const overlay = ensureOverlay();
    const stage = overlay.querySelector(".mermaid-zoom-stage");
    stage.replaceChildren();
    const status = document.createElement("p");
    status.className = "mermaid-zoom-status";
    status.textContent = "Loading diagram…";
    stage.appendChild(status);

    lastFocus = document.activeElement;
    overlay.removeAttribute("hidden");
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeydown, true);
    overlay.querySelector(".mermaid-zoom-close").focus();

    try {
      if (!window.mermaid || typeof window.mermaid.render !== "function") {
        throw new Error("Mermaid is not loaded yet");
      }
      const id = "mermaid-zoom-" + Date.now();
      const result = await window.mermaid.render(id, source);
      stage.replaceChildren();
      if (typeof result === "string") {
        stage.innerHTML = result;
      } else if (result && result.svg) {
        stage.innerHTML = result.svg;
        if (typeof result.bindFunctions === "function") {
          result.bindFunctions(stage);
        }
      } else {
        throw new Error("Mermaid render returned no SVG");
      }
      const svg = stage.querySelector("svg");
      if (svg) sizeOverlaySvg(svg);
    } catch (err) {
      status.textContent = "Could not enlarge this diagram.";
      if (stage.childElementCount === 0) stage.appendChild(status);
      console.error("mermaid-zoom:", err);
    }
  }

  function bindDiagram(container, source) {
    if (!source || container.dataset.mermaidZoomBound === "1") return;
    container.dataset.mermaidZoomBound = "1";
    container.setAttribute("data-mermaid-source", source);
    container.classList.add("zoomable");
    if (!container.hasAttribute("tabindex")) container.setAttribute("tabindex", "0");
    if (!container.getAttribute("role")) container.setAttribute("role", "button");
    if (!container.getAttribute("aria-label")) {
      container.setAttribute("aria-label", "Enlarge diagram");
    }

    function open(event) {
      event.preventDefault();
      event.stopPropagation();
      showSource(source);
    }

    container.addEventListener("click", open);
    container.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") open(event);
    });
  }

  function bindAll() {
    collectSources();
    const els = Array.prototype.slice.call(document.querySelectorAll(".mermaid"));
    els.forEach(function (el, i) {
      const source =
        (el.getAttribute("data-mermaid-source") || "").trim() || sources[i] || "";
      bindDiagram(el, source);
    });
  }

  function start() {
    hideOverlay();
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    collectSources();
    bindAll();
    const target = document.querySelector(".md-content") || document.body;
    observer = new MutationObserver(function () {
      bindAll();
    });
    observer.observe(target, { childList: true, subtree: true });
  }

  collectSources();

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(start);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
