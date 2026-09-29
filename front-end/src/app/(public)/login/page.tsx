"use client"

import { useState } from "react"
import { useAuth } from "@/lib/auth"
import { useRouter } from "@/lib/router"
import { Button } from "@/components/ui"
import { AuthShell, Field } from "../_components/auth"

export default function Login() {
  const { login } = useAuth()
  const { navigate } = useRouter()
  const [email, setEmail] = useState("")
  const [pw, setPw] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const submit = async () => {
    if (!/.+@.+\..+/.test(email) || pw.length < 6) {
      setErr("Vui lòng nhập email hợp lệ và mật khẩu có ít nhất 6 ký tự.")
      return
    }
    try {
      await login(email, pw)
      navigate("/dashboard")
    } catch (error) {
      const status = (error as { status?: number })?.status
      setErr(status === 429
        ? "Bạn đã thử đăng nhập quá nhiều lần. Vui lòng chờ trước khi thử lại."
        : status === 401
          ? "Email hoặc mật khẩu không đúng."
          : "Không thể đăng nhập lúc này. Vui lòng thử lại sau.")
    }
  }
  return (
    <AuthShell
      title="Đăng nhập"
      sub="Chào mừng bạn quay trở lại không gian đầu tư định lượng."
      footer={
        <>
          Chưa có tài khoản AIInvest?{" "}
          <button
            onClick={() => navigate("/signup")}
            className="font-medium text-mineral hover:underline"
          >
            Đăng ký ngay
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
        className="space-y-4"
      >
        <Field
          label="Địa chỉ Email"
          type="email"
          value={email}
          onChange={(value) => { setEmail(value); setErr(null) }}
          placeholder="you@firm.vn"
          error={err ?? undefined}
        />
        <Field
          label="Mật khẩu"
          type="password"
          value={pw}
          onChange={(value) => { setPw(value); setErr(null) }}
          placeholder="••••••••"
        />
        <Button type="submit" variant="primary" className="h-11 w-full">
          Đăng nhập
        </Button>
      </form>
    </AuthShell>
  )
}
