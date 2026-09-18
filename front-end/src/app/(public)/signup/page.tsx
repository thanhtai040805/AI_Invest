"use client"

import { useState } from "react"
import { useAuth } from "@/lib/auth"
import { useRouter } from "@/lib/router"
import { Button } from "@/components/ui"
import { AuthShell, Field } from "../_components/auth"

export default function Register() {
  const { register } = useAuth()
  const { navigate } = useRouter()
  const [name, setName] = useState("")
  const [email, setEmail] = useState("")
  const [pw, setPw] = useState("")
  const [err, setErr] = useState(false)
  const submit = async () => {
    if (name.trim().length < 2 || !/.+@.+\..+/.test(email) || pw.length < 6) {
      setErr(true)
      return
    }
    try {
      await register(name.trim(), email, pw)
      navigate("/dashboard")
    } catch {
      setErr(true)
    }
  }
  return (
    <AuthShell
      title="Tạo tài khoản mới"
      sub="Thiết lập không gian làm việc của bạn trong chưa đầy một phút."
      footer={
        <>
          Đã có tài khoản?{" "}
          <button
            onClick={() => navigate("/login")}
            className="font-medium text-mineral hover:underline"
          >
            Đăng nhập
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
          label="Họ và tên"
          value={name}
          onChange={setName}
          placeholder="Nguyễn Văn A"
          error={
            err ? "Vui lòng điền đầy đủ thông tin (mật khẩu tối thiểu 6 ký tự)." : undefined
          }
        />
        <Field
          label="Địa chỉ Email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="you@firm.vn"
        />
        <Field
          label="Mật khẩu"
          type="password"
          value={pw}
          onChange={setPw}
          placeholder="Tối thiểu 6 ký tự"
        />
        <Button type="submit" variant="primary" className="h-11 w-full">
          Đăng ký tài khoản
        </Button>
      </form>
    </AuthShell>
  )
}
