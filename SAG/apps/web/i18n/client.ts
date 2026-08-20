import {
  defaultLocale,
  isAppLocale,
  localeCookieMaxAge,
  localeCookieName,
  type AppLocale,
} from "./config";

/** Read the currently active locale without coupling non-React code to next-intl hooks. */
export function readClientLocale(): AppLocale {
  if (typeof document === "undefined") return defaultLocale;
  const documentLocale = document.documentElement.lang;
  if (isAppLocale(documentLocale)) return documentLocale;
  const cookieLocale = document.cookie
    .split(";")
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${localeCookieName}=`))
    ?.slice(localeCookieName.length + 1);
  const decoded = cookieLocale ? decodeURIComponent(cookieLocale) : null;
  return isAppLocale(decoded) ? decoded : defaultLocale;
}

/** Persist the locale for both the next server render and future visits. */
export function persistLocale(locale: AppLocale) {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${localeCookieName}=${encodeURIComponent(locale)}; Path=/; Max-Age=${localeCookieMaxAge}; SameSite=Lax${secure}`;
  document.documentElement.lang = locale;
}
