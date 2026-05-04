// Agents tab. Placeholder for now -- the actual ReActAgent /
// ConstrainedAgent / ContractAgent UI lands in PLAN.md Phase 7.

export function mount() {
  const host = document.getElementById("agentsTabHost");
  if (!host) return;
  host.innerHTML = `
    <div class="rt-section">
      <div class="rt-section-head"><h3>Agents</h3></div>
      <div class="rt-placeholder">
        <p>Agent runner is planned for a later phase.</p>
        <p class="rt-placeholder-sub">
          ReActAgent, ConstrainedAgent, and ContractAgent integrations
          along with a tool catalog will land here.
        </p>
      </div>
    </div>
    <div class="rt-section">
      <div class="rt-section-head"><h3>Personas</h3></div>
      <div class="rt-placeholder">
        <p>Per-chat persona presets (system-prompt + sampling bundles)
        will be promotable to named personas here.</p>
      </div>
    </div>
    <div class="rt-section">
      <div class="rt-section-head"><h3>Tools</h3></div>
      <div class="rt-placeholder">
        <p>Tool catalog: web fetch, sandboxed file read, RAG-collection
        query, calculator. Off by default.</p>
      </div>
    </div>
  `;
}
