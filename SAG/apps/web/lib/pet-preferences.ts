"use client";

import * as React from "react";

import {
  APP_INITIALIZATION_DEFAULTS,
  APP_INITIALIZATION_STORAGE_KEYS,
  persistPetPresence,
  readInitialPetPresence,
  type PetPresence,
} from "@/lib/app-initialization";

const PET_PRESENCE_EVENT = "sag:pet-presence-change";

function getPetPresence() {
  if (typeof window === "undefined") return APP_INITIALIZATION_DEFAULTS.petPresence;
  return readInitialPetPresence(window.localStorage);
}

function subscribePetPresence(listener: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (
      event.key === APP_INITIALIZATION_STORAGE_KEYS.petPresence
      || event.key === APP_INITIALIZATION_STORAGE_KEYS.legacyPetEnabled
    ) listener();
  };
  window.addEventListener(PET_PRESENCE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(PET_PRESENCE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function setPetPresence(presence: PetPresence) {
  if (typeof window === "undefined") return;
  persistPetPresence(window.localStorage, presence);
  window.dispatchEvent(new Event(PET_PRESENCE_EVENT));
}

/** Giữ ở chế độ normal khi đang hoạt động thường trực; chế độ khám phá luôn hiển thị thú cưng. */
export function usePetPresence(): [PetPresence, (presence: PetPresence) => void] {
  const presence = React.useSyncExternalStore(
    subscribePetPresence,
    getPetPresence,
    () => APP_INITIALIZATION_DEFAULTS.petPresence,
  );
  const updatePresence = React.useCallback((value: PetPresence) => setPetPresence(value), []);
  return [presence, updatePresence];
}
