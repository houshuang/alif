import { colors } from "./theme";

export type FrequencyBand = "common" | "medium" | "rare" | "unknown";

export function getFrequencyBand(rank: number | null): {
  band: FrequencyBand;
  label: string;
  color: string;
} {
  if (rank == null)
    return { band: "unknown", label: "", color: colors.textSecondary };
  if (rank <= 500)
    return { band: "common", label: "common", color: colors.stateKnown };
  if (rank <= 2000)
    return { band: "medium", label: "medium", color: colors.stateLearning };
  return { band: "rare", label: "rare", color: colors.textSecondary };
}

const cefrColors: Record<string, string> = {
  A1: colors.cefrA1,
  A2: colors.cefrA2,
  B1: colors.cefrB1,
  B2: colors.cefrB2,
  C1: colors.cefrC1,
  C2: colors.cefrC2,
};

export function getCefrColor(level: string | null): string {
  if (!level) return colors.textSecondary;
  return cefrColors[level.toUpperCase()] ?? colors.textSecondary;
}
