// Memory-hook MNEMONICS are hidden as of 2026-05-22. A two-batch calibration study
// (research/analysis-2026-05-22-stale-pr-ideas.md) found most auto-generated keyword
// mnemonics low-quality, and the quality boundary was not reliably learnable (held-out
// Cohen's kappa = -0.12). Generation is also disabled backend-side via the
// ALIF_MEMORY_HOOKS_ENABLED env flag (default off). The other memory_hooks_json fields
// (cognates, collocations, usage_context, fun_fact) are unaffected and still shown.
// Flip to true to restore mnemonic display.
export const SHOW_MNEMONIC_HOOKS = false;
