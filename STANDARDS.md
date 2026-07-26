# Shared Engineering Standards

`redfish_ctl` consumes the shared engineering standards through
`standards-binding.yaml`. That binding defines the authority, exact version
lock, and required contract set; shared contract text is not copied into this
repository.

Before acting, read in this order:

1. `standards-binding.yaml`.
2. The shared `README.md` and `manifest.yaml` resolved from the binding's
   `spec.source.localPath`.
3. Every required contract named by that manifest and the project binding.

Follow the precedence defined by the pinned shared contracts. Project rules may
add stricter constraints but may not weaken the pinned standards.

If the binding, pinned revision, or a required contract cannot be read, stop and
report a precise `BLOCKER:`. Do not infer a replacement from another project or
an old handoff.
