import * as React from "react";

/**
 * Đầu trang —— bố cục thống nhất toàn site gồm tiêu đề / mô tả / vùng hành động.
 * Trang danh sách và trang chi tiết dùng chung, đảm bảo kiểu chữ, khoảng cách và hành vi xuống dòng nhất quán.
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="font-display text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
