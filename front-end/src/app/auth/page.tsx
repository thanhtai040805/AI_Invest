"use client";

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { GlassCard } from '@/components/ui/GlassCard';
import { authAPI, setAccessToken } from '@/services/api';

type Mode = 'login' | 'register';

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const isRegister = mode === 'register';

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const result = isRegister
        ? await authAPI.register(email.trim(), password, displayName.trim() || undefined)
        : await authAPI.login(email.trim(), password);

      setAccessToken(result.accessToken);
      router.push('/dashboard');
    } catch (err: any) {
      setError(err?.response?.data?.error || 'Không thể kết nối với máy chủ. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-background text-on-background flex items-center justify-center px-md py-xl">
      <div className="w-full max-w-5xl grid gap-xl lg:grid-cols-[1.2fr_0.8fr] items-center">
        <section className="space-y-lg">
          <div className="space-y-md">
            <span className="inline-flex items-center gap-2 px-sm py-2 rounded-full bg-primary/10 text-primary text-[13px] font-semibold uppercase tracking-[0.24em]">
              <span className="material-symbols-outlined">lock</span>
              Bảo mật token kép
            </span>
            <h1 className="font-headline-lg text-headline-lg">Đăng nhập hoặc đăng ký để bắt đầu đầu tư thông minh.</h1>
            <p className="text-on-surface-variant text-body-md">
              Hệ thống sử dụng Access token 30 phút và Refresh token 30 ngày. Sau khi đăng nhập, token được lưu trữ an toàn và làm mới tự động khi cần.
            </p>
          </div>

          <div className="grid gap-lg">
            <GlassCard className="p-lg" glow>
              <form onSubmit={handleSubmit} className="grid gap-sm">
                <div className="flex items-center gap-sm text-on-surface-variant text-title-sm">
                  <button
                    type="button"
                    onClick={() => setMode('login')}
                    className={mode === 'login' ? 'px-4 py-2 rounded-full bg-primary text-on-primary' : 'px-4 py-2 rounded-full bg-surface-variant text-on-surface-variant hover:bg-white/10 transition'}
                  >
                    Đăng nhập
                  </button>
                  <button
                    type="button"
                    onClick={() => setMode('register')}
                    className={mode === 'register' ? 'px-4 py-2 rounded-full bg-primary text-on-primary' : 'px-4 py-2 rounded-full bg-surface-variant text-on-surface-variant hover:bg-white/10 transition'}
                  >
                    Đăng ký
                  </button>
                </div>
                <div className="h-px bg-white/5" />
                <div className="space-y-md">
                  <div className="text-sm text-secondary font-semibold uppercase tracking-[0.24em]">Phiên</div>
                  <div className="grid gap-4">
                    <div className="grid gap-2">
                      <label className="text-sm text-on-surface-variant" htmlFor="email">Email</label>
                      <input
                        id="email"
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        className="w-full rounded-2xl border border-white/10 bg-surface px-4 py-3 text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary"
                        placeholder="you@example.com"
                        required
                      />
                    </div>

                    {isRegister && (
                      <div className="grid gap-2">
                        <label className="text-sm text-on-surface-variant" htmlFor="displayName">Tên hiển thị</label>
                        <input
                          id="displayName"
                          type="text"
                          value={displayName}
                          onChange={(event) => setDisplayName(event.target.value)}
                          className="w-full rounded-2xl border border-white/10 bg-surface px-4 py-3 text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary"
                          placeholder="Tên của bạn"
                        />
                      </div>
                    )}

                    <div className="grid gap-2">
                      <label className="text-sm text-on-surface-variant" htmlFor="password">Mật khẩu</label>
                      <input
                        id="password"
                        type="password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        className="w-full rounded-2xl border border-white/10 bg-surface px-4 py-3 text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary"
                        placeholder="Ít nhất 6 ký tự"
                        required
                      />
                    </div>
                  </div>

                  {error && (
                    <div className="rounded-2xl border border-error/20 bg-error/10 px-4 py-3 text-sm text-error">{error}</div>
                  )}

                  <button
                    type="submit"
                    className="w-full rounded-2xl bg-primary px-5 py-3 text-sm font-semibold text-on-primary transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={loading}
                  >
                    {loading ? 'Đang xử lý...' : isRegister ? 'Tạo tài khoản' : 'Đăng nhập'}
                  </button>
                </div>
              </form>
            </GlassCard>
          </div>
        </section>

        <section className="space-y-lg">
          <GlassCard className="p-lg bg-surface-container-lowest/90">
            <div className="space-y-md">
              <div className="flex items-center justify-between gap-md">
                <div>
                  <h2 className="font-headline-md text-headline-md">Quản lý mãi mãi</h2>
                  <p className="text-on-surface-variant">Bảo mật truy cập bằng Access token 30 phút và Refresh token 30 ngày.</p>
                </div>
                <Link href="/" className="text-primary text-sm font-semibold hover:underline">Về trang chủ</Link>
              </div>

              <div className="grid gap-4">
                <div className="rounded-2xl border border-white/10 bg-black/10 p-4">
                  <p className="text-sm text-primary font-semibold">Access token</p>
                  <p className="text-sm text-on-surface-variant">Dùng để gọi API bảo mật, tự động làm mới trong 30 phút.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/10 p-4">
                  <p className="text-sm text-secondary font-semibold">Refresh token</p>
                  <p className="text-sm text-on-surface-variant">Lưu trên client và đổi mới mỗi tháng khi phiên hết hạn.</p>
                </div>
                <div className="rounded-2xl border border-white/10 bg-black/10 p-4">
                  <p className="text-sm text-on-surface-variant">Lợi ích</p>
                  <ul className="list-disc list-inside text-sm text-on-surface-variant space-y-2">
                    <li>Giảm rủi ro giải mã token truy cập</li>
                    <li>Giữ phiên lâu dài với bảo mật tốt hơn</li>
                    <li>Tự động làm mới mà không mất trải nghiệm</li>
                  </ul>
                </div>
              </div>
            </div>
          </GlassCard>
        </section>
      </div>
    </div>
  );
}
