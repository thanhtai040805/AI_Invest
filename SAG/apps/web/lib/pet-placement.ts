export interface PetPoint {
  x: number;
  y: number;
}

export interface PetSize {
  width: number;
  height: number;
}

export interface PlacementRect extends PetPoint, PetSize {}

export interface ExplorePetPlacementInput {
  viewport: PetSize;
  pet: PetSize;
  avoidRects?: PlacementRect[];
  margin?: number;
}

const EXPLORE_EDGE_INSET_MULTIPLIER = 4;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/** Keeps an existing pet position visible without choosing a new preferred location. */
export function clampPetPosition(
  point: PetPoint,
  viewport: PetSize,
  pet: PetSize,
  margin = 24,
) {
  return {
    x: clamp(point.x, margin, viewport.width - pet.width - margin),
    y: clamp(point.y, margin, viewport.height - pet.height - margin),
  };
}

function intersects(left: PlacementRect, right: PlacementRect) {
  return left.x < right.x + right.width
    && left.x + left.width > right.x
    && left.y < right.y + right.height
    && left.y + left.height > right.y;
}

function petRect(point: PetPoint, pet: PetSize): PlacementRect {
  return { ...point, ...pet };
}

/**
 * Places exploration controls inside a responsive lower-right safe zone and
 * moves them away from measured UI controls. The result is always clamped to
 * the current viewport, so resize and future desktop work-area adapters can
 * share the same policy.
 */
export function resolveExplorePetPosition({
  viewport,
  pet,
  avoidRects = [],
  margin = 24,
}: ExplorePetPlacementInput): PetPoint {
  const inset = margin * EXPLORE_EDGE_INSET_MULTIPLIER;
  const preferred = clampPetPosition(
    {
      x: viewport.width - pet.width - inset,
      y: viewport.height - pet.height - inset,
    },
    viewport,
    pet,
    margin,
  );
  const gap = margin;
  const candidates = [preferred];

  for (const rect of avoidRects) {
    if (!intersects(petRect(preferred, pet), rect)) continue;
    candidates.push(
      { x: rect.x - pet.width - gap, y: rect.y - pet.height - gap },
      { x: rect.x - pet.width - gap, y: preferred.y },
      { x: preferred.x, y: rect.y - pet.height - gap },
    );
  }

  return candidates
    .map((candidate) => clampPetPosition(candidate, viewport, pet, margin))
    .map((candidate) => {
      const overlapCount = avoidRects.filter((rect) =>
        intersects(petRect(candidate, pet), rect)).length;
      const distance = Math.hypot(candidate.x - preferred.x, candidate.y - preferred.y);
      return { candidate, score: overlapCount * 1_000_000 + distance };
    })
    .sort((left, right) => left.score - right.score)[0]?.candidate ?? preferred;
}
