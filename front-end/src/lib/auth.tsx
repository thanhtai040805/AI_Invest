"use client"
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { authApi } from "./api"
import { tokenStore } from "./api/client"

type User = { id: string; email: string; displayName?: string | null; role?: string }

export type OtpChallengeResponse = {
  message: string
  verificationToken?: string
  resetToken?: string
  expiresInSeconds: number
  devCode?: string
}

type Auth = {
  authed: boolean
  loading: boolean
  name: string
  user: User | null
  login: (email: string, password: string) => Promise<void>
  sendRegisterCode: (name: string, email: string) => Promise<OtpChallengeResponse>
  register: (
    name: string,
    email: string,
    password: string,
    verificationCode?: string,
    verificationToken?: string
  ) => Promise<void>
  forgotPassword: (email: string) => Promise<OtpChallengeResponse>
  verifyResetCode: (email: string, code: string, resetToken: string) => Promise<void>
  resetPassword: (
    email: string,
    code: string,
    resetToken: string,
    newPassword: string
  ) => Promise<void>
  loginWithGoogle: (payload: {
    credential?: string
    accessToken?: string
    email?: string
    displayName?: string
  }) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<Auth | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!tokenStore.get()) {
      // The auth check is browser-only because it reads localStorage.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false)
      return
    }
    let active = true
    authApi
      .me()
      .then((value) => {
        if (active) setUser(value)
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const login = async (email: string, password: string) => {
    const cleanEmail = email.trim().toLowerCase()
    const result = await authApi.login(cleanEmail, password)
    tokenStore.set(result.accessToken)
    setUser(result.user)
  }

  const sendRegisterCode = async (name: string, email: string) => {
    const cleanEmail = email.trim().toLowerCase()
    const cleanName = name.trim().replace(/\s+/g, " ")
    return authApi.sendRegisterCode(cleanEmail, cleanName)
  }

  const register = async (
    name: string,
    email: string,
    password: string,
    verificationCode?: string,
    verificationToken?: string
  ) => {
    const cleanEmail = email.trim().toLowerCase()
    const cleanName = name.trim().replace(/\s+/g, " ")
    const result = await authApi.register(
      cleanEmail,
      password,
      cleanName,
      verificationCode?.trim(),
      verificationToken
    )
    tokenStore.set(result.accessToken)
    setUser(result.user)
  }

  const forgotPassword = async (email: string) => {
    const cleanEmail = email.trim().toLowerCase()
    return authApi.forgotPassword(cleanEmail)
  }

  const verifyResetCode = async (email: string, code: string, resetToken: string) => {
    const cleanEmail = email.trim().toLowerCase()
    await authApi.verifyResetCode(cleanEmail, code.trim(), resetToken)
  }

  const resetPassword = async (
    email: string,
    code: string,
    resetToken: string,
    newPassword: string
  ) => {
    const cleanEmail = email.trim().toLowerCase()
    const result = await authApi.resetPassword(
      cleanEmail,
      code.trim(),
      resetToken,
      newPassword
    )
    tokenStore.set(result.accessToken)
    setUser(result.user)
  }

  const loginWithGoogle = async (payload: {
    credential?: string
    accessToken?: string
    email?: string
    displayName?: string
  }) => {
    const result = await authApi.googleLogin(payload)
    tokenStore.set(result.accessToken)
    setUser(result.user)
  }

  const logout = async () => {
    try {
      await authApi.logout()
    } finally {
      tokenStore.clear()
      setUser(null)
      window.location.assign("/")
    }
  }

  return (
    <Ctx.Provider
      value={{
        authed: !!user,
        loading,
        name: user?.displayName || user?.email?.split("@")[0] || "",
        user,
        login,
        sendRegisterCode,
        register,
        forgotPassword,
        verifyResetCode,
        resetPassword,
        loginWithGoogle,
        logout,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

export const useAuth = () => {
  const value = useContext(Ctx)
  if (!value) throw new Error("AuthProvider is missing")
  return value
}
