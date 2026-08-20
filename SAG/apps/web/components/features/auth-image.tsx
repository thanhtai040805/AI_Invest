"use client";

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";

import { api } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { cn } from "@/lib/utils";

// Đính kèm cần truy cập kèm Bearer: cache blob URL theo phiên (số lượng ảnh có hạn, không chủ động thu hồi)
const cache = new Map<string, string>();

/** Ảnh có xác thực —— bản xem trước cục bộ render trực tiếp bằng url; đính kèm phía server được kéo thành blob theo id kèm Bearer. */
export function AuthImage({
  id,
  url,
  alt,
  className,
}: {
  id?: string;
  url?: string;
  alt?: string;
  className?: string;
}) {
  const t = useTranslations("Markdown");
  const locale = useLocale();
  const [src, setSrc] = React.useState<string | null>(url ?? (id ? cache.get(id) ?? null : null));

  React.useEffect(() => {
    if (url) {
      setSrc(url);
      return;
    }
    if (!id) return;
    const hit = cache.get(id);
    if (hit) {
      setSrc(hit);
      return;
    }
    let alive = true;
    fetch(api.attachmentUrl(id), {
      headers: {
        Authorization: `Bearer ${getToken() ?? ""}`,
        "Accept-Language": locale,
      },
    })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        const obj = URL.createObjectURL(b);
        cache.set(id, obj);
        if (alive) setSrc(obj);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id, locale, url]);

  if (!src) return <div className={cn("animate-pulse rounded-md bg-muted", className)} />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={alt ?? t("image")} className={className} />;
}
