import { cookies, headers } from "next/headers";
import { getRequestConfig } from "next-intl/server";

import {
  defaultLocale,
  isAppLocale,
  localeCookieName,
  localeFromAcceptLanguage,
} from "@/i18n/config";

export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const headerStore = await headers();
  const savedLocale = cookieStore.get(localeCookieName)?.value;
  const locale = isAppLocale(savedLocale)
    ? savedLocale
    : localeFromAcceptLanguage(headerStore.get("accept-language")) || defaultLocale;

  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
    onError(error) {
      if (process.env.NODE_ENV !== "production") console.error(error);
    },
    getMessageFallback({ namespace, key }) {
      return [namespace, key].filter(Boolean).join(".");
    },
  };
});
