// athena-chat-view.js — Athena's branded chat surface.
//
// Subclass of Gryphon's GryphonChatView that owns Athena's brand text
// while delegating all functional behavior (provider cards, dismiss,
// chat lifecycle, etc.) to the bundled Gryphon view. Composition is
// preserved in spirit — Gryphon owns mechanics, Athena owns presentation.
//
// Architectural note: GryphonChatView's `displayText` option controls
// the workspace tab label only. The welcome panel hardcodes its own
// brand string. Rather than push that override into Gryphon (the
// dependency direction would invert — Gryphon would carry consumer-
// specific complexity), Athena overrides the welcome render here.

const { GryphonChatView } = require("../../vendor/gryphon/src/chat-view");

class AthenaChatView extends GryphonChatView {
  // Runs after Gryphon decides whether to show the welcome panel and
  // (if so) builds it. We let super do all the work — early-return on
  // provider/history, provider detection, card rendering, dismiss
  // handler — then rewrite just the brand-bearing text nodes.
  //
  // The selector-driven swap is robust against Gryphon refactors:
  // missing nodes fall through silently rather than crashing, so an
  // upstream rename produces fallback-to-Gryphon-text rather than a
  // broken welcome panel. The trade-off is silent staleness when
  // Gryphon adds new text — caught by visual review on each vendor bump.
  _renderWelcomePanelIfNeeded() {
    super._renderWelcomePanelIfNeeded();
    const panel = this._welcomePanelEl;
    if (!panel) return;  // super decided not to render — nothing to swap

    const h2 = panel.querySelector("h2");
    if (h2) h2.setText("Welcome to Athena — your Second Brain");

    // The intro paragraph is super's first <p>; the security line and
    // manual hint use distinct classes so we don't risk hitting them
    // with a naive first-paragraph selector.
    const intro = panel.querySelector("p:not(.gryphon-welcome-security):not(.gryphon-welcome-manual)");
    if (intro) {
      intro.setText(
        "Athena is your Second Brain in Obsidian — ingest URLs, papers, " +
        "repos, and videos into a structured knowledge base, then chat " +
        "with what you've read. Pick a provider to begin."
      );
    }

    // Security disclosure stays (the protection is real and still fires
    // inside Athena's bundled chat view), but drop the "Tune the rules
    // in Settings → Gryphon → Security" actionable since Athena doesn't
    // yet expose that UI in its own settings tab.
    const secLine = panel.querySelector(".gryphon-welcome-security");
    if (secLine) {
      secLine.empty();
      secLine.createSpan({
        text: "Built-in security: dangerous file paths and shell commands " +
          "(rm -rf, writes into .obsidian/, curl | bash, etc.) always prompt " +
          "before running — even in YOLO mode.",
      });
    }

    // Manual hint points at Athena's in-vault manual rather than Gryphon's.
    // The link target follows the same convention Athena uses elsewhere
    // (vault-root `Athena/MANUAL.md`).
    const manualHint = panel.querySelector(".gryphon-welcome-manual");
    if (manualHint) {
      manualHint.empty();
      manualHint.createSpan({ text: "New here? See the user manual at " });
      const link = manualHint.createEl("a", {
        text: "Athena/MANUAL.md",
        href: "#",
      });
      link.addEventListener("click", (e) => {
        e.preventDefault();
        this.app.workspace.openLinkText("Athena/MANUAL", "");
      });
      manualHint.createSpan({
        text: " in your vault for ingest commands, search, and synthesis.",
      });
    }
  }
}

module.exports = { AthenaChatView };
