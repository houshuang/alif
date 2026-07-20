// Memory-hook MNEMONICS: display only hooks that passed the storage-time
// quality judge (memory_hooks_json.approved_at present).
//
// History: hidden entirely 2026-05-22 — a two-batch calibration study found most
// auto-generated mnemonics low-quality and the quality boundary unlearnable by an
// LLM critic (held-out kappa = -0.12). Re-enabled 2026-07-20: generation now uses
// a recognition-direction full-cover prompt and an independent 4-check judge
// (known anchor / enacted meaning / automatic trigger / memorable oddity)
// calibrated on 60 user ratings; only judge-passing hooks get approved_at stamped
// at storage time. Pre-2026-07-20 hooks have no stamp and stay hidden. The other
// memory_hooks_json fields (cognates, collocations, usage_context, fun_fact) are
// unaffected and always shown.
export const SHOW_MNEMONIC_HOOKS = true;

export function showMnemonic(
  hooks: { mnemonic?: string | null; approved_at?: string | null } | null | undefined,
): boolean {
  return SHOW_MNEMONIC_HOOKS && !!hooks?.mnemonic && !!hooks?.approved_at;
}
