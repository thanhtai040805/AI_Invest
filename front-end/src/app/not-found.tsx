import Link from "next/link"
export default function NotFound() { return <main className="grid min-h-dvh place-items-center bg-paper p-6 text-center text-ink"><div><p className="font-mono text-sm text-muted">404</p><h1 className="mt-3 font-serif text-4xl">Không tìm thấy trang</h1><Link href="/dashboard" className="mt-6 inline-block text-mineral hover:underline">Về tổng quan</Link></div></main> }
