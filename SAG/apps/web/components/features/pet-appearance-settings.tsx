"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight, Plus, RotateCcw, X } from "lucide-react";
import { useTranslations } from "next-intl";

import { DEFAULT_AGENT_AVATAR } from "@/lib/branding";
import {
  PET_APPEARANCE_LIMITS,
  resolvePetFace,
  usePetAppearancePreferences,
} from "@/lib/pet-appearance-preferences";
import { cn } from "@/lib/utils";
import { PetHeadAvatar } from "@/components/features/pet-head-avatar";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

const MAX_FACE_PRESETS = 24;

export function PetAppearanceSettings({
  agentFace = DEFAULT_AGENT_AVATAR,
  compact = false,
}: {
  agentFace?: string;
  compact?: boolean;
}) {
  const t = useTranslations("PetAppearance");
  const { preferences, update, reset } = usePetAppearancePreferences();
  const displayFace = resolvePetFace(preferences, agentFace);
  const canAddPreset =
    Boolean(displayFace)
    && !preferences.facePresets.includes(displayFace)
    && preferences.facePresets.length < MAX_FACE_PRESETS;

  function setCustomFace(face: string) {
    update({ faceMode: "custom", face });
  }

  function updatePresets(facePresets: string[]) {
    update({ facePresets });
  }

  function movePreset(preset: string, direction: -1 | 1) {
    const index = preferences.facePresets.indexOf(preset);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= preferences.facePresets.length) return;
    const next = [...preferences.facePresets];
    [next[index], next[target]] = [next[target], next[index]];
    updatePresets(next);
  }

  return (
    <div
      id="assistant-appearance"
      data-settings-section="assistant-appearance"
      tabIndex={-1}
      className="scroll-mt-4 outline-none"
    >
      <SettingsSection
        title={t("title")}
        description={t("description")}
        footer={
          <div
            className={cn(
              "flex gap-3",
              compact
                ? "flex-col items-stretch"
                : "items-center justify-between",
            )}
          >
            <p className="text-xs text-muted-foreground">
              {t("savedLocally")}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className={cn(compact && "self-end")}
              onClick={reset}
            >
              <RotateCcw />
              {t("reset")}
            </Button>
          </div>
        }
      >
        <SettingsRow
          title={t("face.title")}
          description={t("face.description")}
        >
          <div
            className={cn(
              "grid gap-4",
              !compact && "sm:grid-cols-[5rem_minmax(0,1fr)] sm:items-start",
            )}
          >
            <div className="flex justify-center rounded-lg border bg-muted/30 py-3">
              <PetHeadAvatar face={displayFace} size="lg" />
            </div>
            <div className="min-w-0 space-y-3">
              <div className="grid grid-cols-3 rounded-md bg-muted p-0.5">
                <FaceModeButton
                  active={preferences.faceMode === "agent"}
                  onClick={() => update({ faceMode: "agent" })}
                >
                  {t("face.agent")}
                </FaceModeButton>
                <FaceModeButton
                  active={preferences.faceMode === "custom" && Boolean(displayFace)}
                  onClick={() => setCustomFace(displayFace || "^_^")}
                >
                  {t("face.custom")}
                </FaceModeButton>
                <FaceModeButton
                  active={preferences.faceMode === "custom" && !displayFace}
                  onClick={() => setCustomFace("")}
                >
                  {t("face.empty")}
                </FaceModeButton>
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={displayFace}
                  onChange={(event) => setCustomFace(event.target.value)}
                  aria-label={t("face.customAria")}
                  placeholder="@_@"
                  className="min-w-0 text-center font-mono font-semibold"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => updatePresets([...preferences.facePresets, displayFace])}
                  disabled={!canAddPreset}
                  aria-label={t("presets.add")}
                  title={t("presets.add")}
                >
                  <Plus />
                </Button>
              </div>
            </div>
          </div>
        </SettingsRow>

        <SettingsRow
          title={t("presets.title")}
          description={t("presets.description")}
        >
          {preferences.facePresets.length > 0 ? (
            <div className={cn("grid gap-2", !compact && "sm:grid-cols-2")}>
              {preferences.facePresets.map((preset, index) => (
                <div
                  key={preset}
                  className={cn(
                    "flex h-9 min-w-0 items-center rounded-md border pl-3",
                    preferences.faceMode === "custom"
                      && displayFace === preset
                      && "border-foreground/35 bg-muted/60",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => setCustomFace(preset)}
                    className="min-w-0 flex-1 truncate text-left font-mono text-xs font-medium"
                  >
                    {preset}
                  </button>
                  <PresetButton
                    label={t("presets.moveForward", { preset })}
                    disabled={index === 0}
                    onClick={() => movePreset(preset, -1)}
                  >
                    <ChevronLeft />
                  </PresetButton>
                  <PresetButton
                    label={t("presets.moveBack", { preset })}
                    disabled={index === preferences.facePresets.length - 1}
                    onClick={() => movePreset(preset, 1)}
                  >
                    <ChevronRight />
                  </PresetButton>
                  <PresetButton
                    label={t("presets.delete", { preset })}
                    onClick={() => updatePresets(
                      preferences.facePresets.filter((value) => value !== preset),
                    )}
                    destructive
                  >
                    <X />
                  </PresetButton>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-md border border-dashed p-4 text-center text-sm text-muted-foreground">
              {t("presets.empty")}
            </div>
          )}
        </SettingsRow>

        <SettingsRow
          title={t("motion.title")}
          description={t("motion.description")}
        >
          <div
            className={cn(
              "grid gap-x-8 gap-y-5",
              !compact && "sm:grid-cols-2",
            )}
          >
            <PreferenceSlider
              label={t("motion.size")}
              value={`${Math.round(preferences.size * 100)}%`}
              sliderValue={preferences.size}
              limits={PET_APPEARANCE_LIMITS.size}
              onChange={(size) => update({ size })}
            />
            <PreferenceSlider
              label={t("motion.float")}
              value={`${Math.round(preferences.floatStrength * 100)}%`}
              sliderValue={preferences.floatStrength}
              limits={PET_APPEARANCE_LIMITS.floatStrength}
              onChange={(floatStrength) => update({ floatStrength })}
            />
            <PreferenceSlider
              label={t("motion.actionRate")}
              value={preferences.actionRate < 0.05
                ? t("motion.off")
                : `${Math.round(preferences.actionRate * 100)}%`}
              sliderValue={preferences.actionRate}
              limits={PET_APPEARANCE_LIMITS.actionRate}
              onChange={(actionRate) => update({ actionRate })}
            />
            <PreferenceSlider
              label={t("motion.expressionDelay")}
              value={`${preferences.expressionDelay.toFixed(1)}×`}
              sliderValue={preferences.expressionDelay}
              limits={PET_APPEARANCE_LIMITS.expressionDelay}
              onChange={(expressionDelay) => update({ expressionDelay })}
            />
          </div>
        </SettingsRow>

        <SettingsRow
          title={t("reduceMotion.title")}
          description={t("reduceMotion.description")}
          layout={compact ? "stacked" : "inline"}
        >
          <Switch
            checked={preferences.reduceMotion}
            onCheckedChange={(reduceMotion) => update({ reduceMotion })}
            aria-label={t("reduceMotion.aria")}
          />
        </SettingsRow>
      </SettingsSection>
    </div>
  );
}

function FaceModeButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-8 rounded text-xs text-muted-foreground transition-colors",
        active && "bg-background text-foreground shadow-sm",
      )}
    >
      {children}
    </button>
  );
}

function PresetButton({
  children,
  destructive,
  disabled,
  label,
  onClick,
}: {
  children: React.ReactNode;
  destructive?: boolean;
  disabled?: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "grid size-8 shrink-0 place-items-center text-muted-foreground transition-colors hover:text-foreground disabled:opacity-20",
        destructive && "hover:text-destructive",
        "[&_svg]:size-3.5",
      )}
    >
      {children}
    </button>
  );
}

function PreferenceSlider({
  label,
  limits,
  onChange,
  sliderValue,
  value,
}: {
  label: string;
  limits: { min: number; max: number; step: number };
  onChange: (value: number) => void;
  sliderValue: number;
  value: string;
}) {
  return (
    <label className="block">
      <span className="mb-2 flex items-center text-xs text-muted-foreground">
        <span className="flex-1">{label}</span>
        <span className="font-mono text-foreground">{value}</span>
      </span>
      <Slider
        value={[sliderValue]}
        min={limits.min}
        max={limits.max}
        step={limits.step}
        onValueChange={(next) => onChange(next[0] ?? sliderValue)}
        aria-label={label}
      />
    </label>
  );
}
