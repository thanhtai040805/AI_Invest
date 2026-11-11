"use client"

import { useEffect, useId, useRef, useState } from "react"
import { useAuth } from "@/lib/auth"
import { useRouter } from "@/lib/router"
import { Button } from "@/components/ui"
import {
  AuthAlert,
  AuthDivider,
  AuthShell,
  buildAuthHref,
  evaluatePasswordStrength,
  extractAuthError,
  Field,
  getSafeRedirectTarget,
  GoogleAuthButton,
  OtpCodeInput,
  PasswordField,
  PasswordStrengthMeter,
  validateEmail,
  type NormalizedAuthError,
} from "../_components/auth"

const REMEMBERED_EMAIL_KEY = "aiinvest_remembered_email"

type SignupFlowStep = "details" | "verify-email"

function validateDisplayName(rawName: string): string | null {
  const normalized = rawName.trim().replace(/\s+/g, " ")
  if (!normalized) {
    return "Vui lòng nhập họ và tên hoặc tên hiển thị của bạn."
  }
  if (normalized.length < 2) {
    return "Tên hiển thị cần có tối thiểu 2 ký tự."
  }
  return null
}

export default function Register() {
  const { sendRegisterCode, register, loginWithGoogle } = useAuth()
  const { navigate } = useRouter()

  const uid = useId()
  const nameInputId = `signup-name-${uid}`
  const emailInputId = `signup-email-${uid}`
  const passwordInputId = `signup-password-${uid}`
  const strengthMeterId = `signup-password-strength-${uid}`

  const [step, setStep] = useState<SignupFlowStep>("details")
  const [name, setName] = useState("")
  const [email, setEmail] = useState<string>(() => {
    if (typeof window === "undefined") return ""
    const prefillEmail = new URLSearchParams(window.location.search).get("email")?.trim()
    return prefillEmail ? prefillEmail.toLowerCase() : ""
  })
  const [pw, setPw] = useState("")
  const [nextParam] = useState<string | null>(() => {
    if (typeof window === "undefined") return null
    return new URLSearchParams(window.location.search).get("next")
  })

  // Email OTP Verification state
  const [otpCode, setOtpCode] = useState("")
  const [verificationToken, setVerificationToken] = useState("")
  const [devOtpCode, setDevOtpCode] = useState<string | undefined>(undefined)
  const [otpError, setOtpError] = useState<string | undefined>(undefined)
  const [resendSec, setResendSec] = useState(0)

  const [fieldErrors, setFieldErrors] = useState<{
    name?: string
    email?: string
    password?: string
  }>({})
  const [serverError, setServerError] = useState<NormalizedAuthError | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [cooldownSec, setCooldownSec] = useState(0)
  const submitLockRef = useRef(false)

  useEffect(() => {
    if (cooldownSec <= 0) return
    const timer = window.setInterval(() => {
      setCooldownSec((prev) => (prev <= 1 ? 0 : prev - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [cooldownSec])

  useEffect(() => {
    if (resendSec <= 0) return
    const timer = window.setInterval(() => {
      setResendSec((prev) => (prev <= 1 ? 0 : prev - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [resendSec])

  const focusField = (id: string) => {
    if (typeof document === "undefined") return
    const el = document.getElementById(id) as HTMLInputElement | null
    el?.focus()
  }

  const handleNameBlur = () => {
    if (!name.trim()) return
    const err = validateDisplayName(name)
    setFieldErrors((prev) => ({ ...prev, name: err ?? undefined }))
  }

  const handleEmailBlur = () => {
    if (!email.trim()) return
    const err = validateEmail(email)
    setFieldErrors((prev) => ({ ...prev, email: err ?? undefined }))
  }

  const handleGoogleAuth = async (payload: {
    credential?: string
    accessToken?: string
    email?: string
    displayName?: string
  }) => {
    setServerError(null)
    try {
      await loginWithGoogle(payload)
      const target = getSafeRedirectTarget(nextParam)
      navigate(target)
    } catch (error) {
      const normalized = extractAuthError(error, "signup")
      setServerError(normalized)
      throw error
    }
  }

  const requestEmailCode = async (isResend = false) => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    setServerError(null)
    setOtpError(undefined)

    const cleanName = name.trim().replace(/\s+/g, " ")
    const cleanEmail = email.trim().toLowerCase()
    const nameErr = validateDisplayName(cleanName)
    const emailErr = validateEmail(cleanEmail)
    const pwStrength = evaluatePasswordStrength(pw)
    const pwErr = !pw
      ? "Vui lòng tạo mật khẩu cho tài khoản của bạn."
      : !pwStrength.meetsMinimum
        ? "Mật khẩu cần có tối thiểu 6 ký tự."
        : null

    if (nameErr || emailErr || pwErr) {
      setFieldErrors({
        name: nameErr ?? undefined,
        email: emailErr ?? undefined,
        password: pwErr ?? undefined,
      })
      if (nameErr) {
        focusField(nameInputId)
      } else if (emailErr) {
        focusField(emailInputId)
      } else if (pwErr) {
        focusField(passwordInputId)
      }
      return
    }

    setFieldErrors({})
    submitLockRef.current = true
    setSubmitting(true)

    try {
      const challenge = await sendRegisterCode(cleanName, cleanEmail)
      setVerificationToken(challenge.verificationToken ?? "")
      setDevOtpCode(challenge.devCode)
      if (!isResend) {
        setOtpCode("")
      }
      setResendSec(60)
      setStep("verify-email")
    } catch (error) {
      const normalized = extractAuthError(error, "signup")
      setServerError(normalized)

      if (normalized.status === 409) {
        setFieldErrors((prev) => ({
          ...prev,
          email: "Địa chỉ email này đã được đăng ký.",
        }))
        focusField(emailInputId)
      }

      if (normalized.isRateLimited) {
        setCooldownSec(30)
      }
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const verifyAndCreateAccount = async () => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    setServerError(null)
    setOtpError(undefined)

    const cleanOtp = otpCode.replace(/\D/g, "")
    if (cleanOtp.length !== 6) {
      setOtpError("Vui lòng nhập đủ 6 chữ số xác minh được gửi tới email của bạn.")
      return
    }

    const cleanName = name.trim().replace(/\s+/g, " ")
    const cleanEmail = email.trim().toLowerCase()

    submitLockRef.current = true
    setSubmitting(true)

    try {
      await register(cleanName, cleanEmail, pw, cleanOtp, verificationToken)

      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem(REMEMBERED_EMAIL_KEY, cleanEmail)
        }
      } catch {
        // Ignore storage errors
      }

      const target = getSafeRedirectTarget(nextParam)
      navigate(target)
    } catch (error) {
      const normalized = extractAuthError(error, "signup")
      if (normalized.code === "INVALID_OTP") {
        setOtpError(normalized.message)
      } else {
        setServerError(normalized)
      }

      if (normalized.isRateLimited) {
        setCooldownSec(30)
      }
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const navigateToLogin = () => {
    navigate(buildAuthHref("/login", { email, next: nextParam }))
  }

  const isDisabled = submitting || cooldownSec > 0

  return (
    <AuthShell
      mode="signup"
      title={step === "details" ? "Tạo tài khoản mới" : "Xác minh địa chỉ Email"}
      sub={
        step === "details"
          ? "Khởi tạo không gian nghiên cứu định lượng cá nhân chỉ trong 30 giây."
          : "Nhập mã xác minh gồm 6 chữ số được gửi tới hộp thư của bạn để kích hoạt tài khoản."
      }
      email={email}
      nextParam={nextParam}
      footer={
        step === "details" ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Đã có tài khoản AIInvest?</span>
            <button
              type="button"
              onClick={navigateToLogin}
              className="font-semibold text-ink underline-offset-4 transition-colors hover:text-mineral hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
            >
              Đăng nhập ngay →
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Nhập sai địa chỉ email?</span>
            <button
              type="button"
              onClick={() => {
                setStep("details")
                setServerError(null)
                setOtpError(undefined)
              }}
              className="font-semibold text-ink underline-offset-4 transition-colors hover:text-mineral hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
            >
              ← Quay lại chỉnh sửa thông tin
            </button>
          </div>
        )
      }
    >
      {/* =====================================================================
          STEP 1: ENTER ACCOUNT DETAILS OR GOOGLE SIGN-UP
      ===================================================================== */}
      {step === "details" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void requestEmailCode(false)
          }}
          className="space-y-2.5"
        >
          {serverError && (
            <AuthAlert
              title={serverError.title}
              message={
                cooldownSec > 0 && serverError.isRateLimited
                  ? `${serverError.message} (Có thể thử lại sau ${cooldownSec} giây)`
                  : serverError.message
              }
              tone={serverError.isRateLimited ? "warning" : "error"}
              actionLabel={serverError.actionLabel}
              onAction={
                serverError.actionHref ? () => navigateToLogin() : undefined
              }
            />
          )}

          <GoogleAuthButton
            mode="signup"
            disabled={isDisabled}
            defaultEmail={email}
            onAuthenticate={handleGoogleAuth}
          />

          <AuthDivider label="hoặc đăng ký bằng email" />

          <Field
            id={nameInputId}
            name="displayName"
            label="Họ và tên"
            value={name}
            onBlur={handleNameBlur}
            onChange={(val) => {
              setName(val)
              if (fieldErrors.name) {
                setFieldErrors((prev) => ({ ...prev, name: undefined }))
              }
              if (serverError) setServerError(null)
            }}
            placeholder="Ví dụ: Nguyễn Minh Quân"
            autoComplete="name"
            disabled={isDisabled}
            error={fieldErrors.name}
          />

          <Field
            id={emailInputId}
            name="email"
            label="Địa chỉ email"
            type="email"
            inputMode="email"
            value={email}
            onBlur={handleEmailBlur}
            onChange={(val) => {
              setEmail(val)
              if (fieldErrors.email) {
                setFieldErrors((prev) => ({ ...prev, email: undefined }))
              }
              if (serverError) setServerError(null)
            }}
            placeholder="ten.ban@tochuc.vn"
            autoComplete="email"
            disabled={isDisabled}
            error={fieldErrors.email}
          />

          <div>
            <PasswordField
              id={passwordInputId}
              name="password"
              label="Mật khẩu"
              value={pw}
              onChange={(val) => {
                setPw(val)
                if (fieldErrors.password) {
                  setFieldErrors((prev) => ({ ...prev, password: undefined }))
                }
                if (serverError) setServerError(null)
              }}
              placeholder="Tạo mật khẩu (tối thiểu 6 ký tự)"
              autoComplete="new-password"
              disabled={isDisabled}
              error={fieldErrors.password}
              describedByIds={strengthMeterId}
            />

            <PasswordStrengthMeter id={strengthMeterId} password={pw} />
          </div>

          <div className="pt-1">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-10 w-full rounded-[9px] text-[14px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang gửi mã xác minh…</span>
                </span>
              ) : cooldownSec > 0 ? (
                <span className="font-mono tnum">
                  Thử lại sau {cooldownSec}s
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Tiếp tục — Nhận mã xác minh Email</span>
                  <span
                    aria-hidden="true"
                    className="transition-transform duration-150 group-hover:translate-x-0.5"
                  >
                    →
                  </span>
                </span>
              )}
            </Button>
          </div>
        </form>
      )}

      {/* =====================================================================
          STEP 2: VERIFY 6-DIGIT EMAIL OTP & ACTIVATE ACCOUNT
      ===================================================================== */}
      {step === "verify-email" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void verifyAndCreateAccount()
          }}
          className="space-y-4"
        >
          {serverError && (
            <AuthAlert
              title={serverError.title}
              message={serverError.message}
              tone={serverError.isRateLimited ? "warning" : "error"}
            />
          )}

          <OtpCodeInput
            recipientEmail={email}
            onEditEmail={() => {
              setStep("details")
              setOtpError(undefined)
            }}
            value={otpCode}
            onChange={(code) => {
              setOtpCode(code)
              if (otpError) setOtpError(undefined)
            }}
            disabled={isDisabled}
            error={otpError}
            devCode={devOtpCode}
            resendCooldown={resendSec}
            onResend={() => void requestEmailCode(true)}
          />

          <div className="pt-1">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-10 w-full rounded-[9px] text-[14px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang xác minh & khởi tạo…</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Xác minh Email & Hoàn tất đăng ký</span>
                  <span
                    aria-hidden="true"
                    className="transition-transform duration-150 group-hover:translate-x-0.5"
                  >
                    →
                  </span>
                </span>
              )}
            </Button>
          </div>
        </form>
      )}
    </AuthShell>
  )
}
