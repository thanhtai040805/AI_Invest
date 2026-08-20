export const PET_DRAFT_EVENT = "sag:pet-draft";
export const PET_DRAFT_KEY = "sag:pet-draft";

export interface PetDraftPayload {
  text: string;
  submit: boolean;
}

export function encodePetDraft(payload: PetDraftPayload) {
  return JSON.stringify(payload);
}

export function parsePetDraft(value: unknown): PetDraftPayload | null {
  let candidate = value;
  if (typeof candidate === "string") {
    const text = candidate.trim();
    if (!text) return null;
    try {
      candidate = JSON.parse(text) as unknown;
    } catch {
      return { text, submit: false };
    }
  }

  if (!candidate || typeof candidate !== "object") return null;
  const payload = candidate as Partial<PetDraftPayload>;
  const text = typeof payload.text === "string" ? payload.text.trim() : "";
  if (!text) return null;
  return { text, submit: payload.submit === true };
}
