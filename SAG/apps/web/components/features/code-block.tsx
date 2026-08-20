/** Khối mã nội tuyến —— đẳng rộng, cuộn ngang được, nền dịu; dùng kèm CopyButton. */
export function CodeBlock({ children }: { children: string }) {
  return (
    <pre className="max-w-full overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed">
      {children}
    </pre>
  );
}
