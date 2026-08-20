/**
 * Sao chép văn bản. HTTP qua mạng nội bộ không thuộc security context, Clipboard API
 * có thể không khả dụng, vì vậy vẫn giữ cơ chế sao chép vùng chọn đồng bộ làm đường tương thích.
 */
export async function copyText(text: string): Promise<void> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Trình duyệt có thể từ chối vì lý do quyền hạn hoặc không phải security context, tiếp tục bằng đường tương thích.
    }
  }

  if (typeof document === "undefined" || !document.body) {
    throw new Error("Clipboard is unavailable");
  }

  const activeElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute("aria-hidden", "true");
  Object.assign(textarea.style, {
    position: "fixed",
    top: "0",
    left: "-9999px",
    width: "1px",
    height: "1px",
    opacity: "0",
    pointerEvents: "none",
  });
  document.body.appendChild(textarea);

  try {
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    if (!document.execCommand("copy")) {
      throw new Error("Copy command was rejected");
    }
  } finally {
    textarea.remove();
    try {
      activeElement?.focus({ preventScroll: true });
    } catch {
      // Sau khi sao chép hoàn tất, không để focus cũ đã vô hiệu ghi đè lên kết quả thành công.
    }
  }
}
