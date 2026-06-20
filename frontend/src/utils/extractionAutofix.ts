import type {Entity} from "@/api/extractionApprovals";

export function getEntitySuggestedFix(entity: Entity) {
  return entity.ai_verdict?.suggested_fix ?? null;
}

export function canEntityAutoFix(entity: Entity): boolean {
  const fix = getEntitySuggestedFix(entity);
  if (!fix || fix.confidence !== "high") return false;
  if (entity.source === "genre_ml") return false;
  const effectiveText = (entity.effective_text ?? entity.text ?? "").trim();
  return fix.text.trim() !== effectiveText;
}

export function entityFixTitle(entity: Entity): string {
  const fix = getEntitySuggestedFix(entity);
  if (!fix) return "";
  return fix.reasoning ?? `Auto-fix: apply AI-suggested correction → ${fix.text}`;
}
