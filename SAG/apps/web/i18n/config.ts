export const locales = ["vi-VN", "zh-CN", "en-US"] as const;

export type AppLocale = (typeof locales)[number];

export const defaultLocale: AppLocale = "vi-VN";
export const localeCookieName = "sag_locale";
export const localeCookieMaxAge = 60 * 60 * 24 * 365;

export function isAppLocale(value: unknown): value is AppLocale {
  return typeof value === "string" && locales.includes(value as AppLocale);
}

export function localeFromAcceptLanguage(value: string | null | undefined): AppLocale {
  if (!value) return defaultLocale;
  const preferred = value
    .split(",")
    .map((entry, index) => {
      const [rawTag, ...parameters] = entry.trim().split(";");
      const qualityParameter = parameters
        .map((parameter) => parameter.trim().match(/^q\s*=\s*(0(?:\.\d+)?|1(?:\.0+)?)$/i))
        .find(Boolean);
      const quality = qualityParameter ? Number(qualityParameter[1]) : 1;
      return { tag: rawTag?.trim().toLowerCase() ?? "", quality, index };
    })
    .filter((entry) => entry.tag && entry.quality > 0)
    .sort((left, right) => right.quality - left.quality || left.index - right.index);

  for (const { tag } of preferred) {
    if (tag === "*") return defaultLocale;
    if (tag === "vi" || tag.startsWith("vi-")) return "vi-VN";
    if (tag === "zh" || tag.startsWith("zh-")) return "zh-CN";
    if (tag === "en" || tag.startsWith("en-")) return "en-US";
  }
  return defaultLocale;
}

export function localeDocumentTag(locale: AppLocale): string {
  return locale;
}
