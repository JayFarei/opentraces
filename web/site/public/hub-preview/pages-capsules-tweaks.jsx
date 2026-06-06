// Floating Tweaks panel for the Capsule view. Lives in its own React root and
// writes to the shared CapView store; the CapsuleDetail reads the same store, so
// the panel and the inline "View as" control stay in lockstep. Hidden until the
// host toolbar's Tweaks toggle activates it.

(function () {
  function CapsuleTweaks() {
    const view = useCapView();
    return (
      <TweaksPanel title="Tweaks">
        <TweakSection label="Capsule view" />
        <TweakRadio
          label="View as"
          value={view.perspective}
          options={[
            { value: "preshare", label: "Pre-share" },
            { value: "published", label: "Published" },
            { value: "consumer", label: "Consumer" },
          ]}
          onChange={(v) => window.CapView.set({ perspective: v })}
        />
        <TweakRadio
          label="Density"
          value={view.density}
          options={[
            { value: "comfortable", label: "Comfortable" },
            { value: "compact", label: "Compact" },
          ]}
          onChange={(v) => window.CapView.set({ density: v })}
        />
        <div className="twk-sect" style={{ color: "rgba(41,38,27,.4)", fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
          Controls the Capsule detail view — open any capsule to see it.
        </div>
      </TweaksPanel>
    );
  }

  const mount = document.createElement("div");
  mount.id = "cap-tweaks-root";
  document.body.appendChild(mount);
  ReactDOM.createRoot(mount).render(<CapsuleTweaks />);
})();
