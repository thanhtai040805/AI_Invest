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
      title="Create your account"
      sub="Set up your workspace in under a minute."
      footer={
        <>
          Already have an account?{" "}
          <button
            onClick={() => navigate("/login")}
            className="font-medium text-mineral hover:underline"
          >
            Sign in
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
          label="Full name"
          value={name}
          onChange={setName}
          placeholder="Nguyễn Văn A"
          error={
            err ? "Complete all fields (password: 6+ characters)." : undefined
          }
        />
        <Field
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          placeholder="you@firm.vn"
        />
        <Field
          label="Password"
          type="password"
          value={pw}
          onChange={setPw}
          placeholder="At least 6 characters"
        />
        <Button type="submit" variant="primary" className="h-11 w-full">
          Create account
        </Button>
      </form>
    </AuthShell>
  )
}
