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
  const [err, setErr] = useState(false)
  const submit = async () => {
    if (!/.+@.+\..+/.test(email) || pw.length < 6) {
      setErr(true)
      return
    }
    try {
      await login(email, pw)
      navigate("/dashboard")
    } catch {
      setErr(true)
    }
  }
  return (
    <AuthShell
      title="Sign in"
      sub="Welcome back to your investment workspace."
      footer={
        <>
          New to AIInvest?{" "}
          <button
            onClick={() => navigate("/signup")}
            className="font-medium text-mineral hover:underline"
          >
            Create an account
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
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="you@firm.vn"
          error={err ? "Enter a valid email and password." : undefined}
        />
        <Field
          label="Password"
          type="password"
          value={pw}
          onChange={setPw}
          placeholder="••••••••"
        />
        <Button type="submit" variant="primary" className="h-11 w-full">
          Sign in
        </Button>
      </form>
    </AuthShell>
  )
}
