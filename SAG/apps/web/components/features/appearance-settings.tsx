"use client";

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { persistLocale } from "@/i18n/client";
import { locales, type AppLocale } from "@/i18n/config";
import { THEME_OPTIONS, TIMEZONE_OPTIONS } from "@/lib/settings-config";

export function AppearanceSettings() {
  const appearance = useTranslations("Appearance");
  const language = useTranslations("Language");
  const theme = useTranslations("Theme");

  return (
    <div className="flex flex-col gap-6">
      <SettingsSection title={theme("sectionTitle")} description={theme("sectionDescription")}>
        <SettingsRow
          title={theme("modeTitle")}
          description={theme("modeDescription")}
          layout="inline"
        >
          <ThemeSegment />
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        title={appearance("localeSectionTitle")}
        description={appearance("localeSectionDescription")}
      >
        <SettingsRow
          title={language("label")}
          description={language("description")}
          layout="inline"
        >
          <LanguageSegment />
        </SettingsRow>
        <SettingsRow
          title={appearance("timezoneTitle")}
          description={appearance("timezoneDescription")}
          layout="inline"
        >
          <TimezoneSelect />
        </SettingsRow>
      </SettingsSection>
    </div>
  );
}

function TimezoneSelect() {
  const t = useTranslations("Appearance");
  const timezones = useTranslations("Timezones");
  const { timezone, updateTimezone } = useApp();
  const [saving, setSaving] = React.useState(false);
  const options = React.useMemo(
    () =>
      TIMEZONE_OPTIONS.some((option) => option.value === timezone)
        ? TIMEZONE_OPTIONS
        : [{ value: timezone, labelKey: null }, ...TIMEZONE_OPTIONS],
    [timezone],
  );

  const changeTimezone = async (value: string) => {
    if (value === timezone || saving) return;
    setSaving(true);
    try {
      await updateTimezone(value);
      toast.success(t("timezoneUpdated"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("timezoneSaveFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Select value={timezone} onValueChange={changeTimezone} disabled={saving}>
      <SelectTrigger className="w-full sm:w-72" aria-label={t("timezoneAria")}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.labelKey ? timezones(option.labelKey) : option.value}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function ThemeSegment() {
  const t = useTranslations("Theme");
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => setMounted(true), []);

  const current = mounted ? (theme ?? "system") : "system";
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      value={current}
      onValueChange={(value) => value && setTheme(value)}
      aria-label={t("sectionTitle")}
      className="grid w-full grid-cols-3 sm:inline-flex sm:w-auto"
    >
      {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => (
        <ToggleGroupItem
          key={value}
          value={value}
          aria-label={t(labelKey)}
          className="gap-1.5 px-3"
        >
          <Icon />
          {t(labelKey)}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function LanguageSegment() {
  const locale = useLocale();
  const t = useTranslations("Language");
  const router = useRouter();
  const [pending, startTransition] = React.useTransition();

  const changeLocale = (value: string) => {
    if (!value || value === locale || pending) return;
    persistLocale(value as AppLocale);
    startTransition(() => router.refresh());
  };

  const languageLabels: Record<AppLocale, string> = {
    "vi-VN": t("vietnamese"),
    "zh-CN": t("chinese"),
    "en-US": t("english"),
  };

  return (
    <ToggleGroup
      type="single"
      variant="outline"
      value={locale}
      onValueChange={changeLocale}
      aria-label={t("label")}
      aria-busy={pending}
      disabled={pending}
      className="grid w-full grid-cols-3 sm:inline-flex sm:w-auto"
    >
      {locales.map((candidate) => (
        <ToggleGroupItem key={candidate} value={candidate} aria-label={languageLabels[candidate]} className="px-4">
          {languageLabels[candidate]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
