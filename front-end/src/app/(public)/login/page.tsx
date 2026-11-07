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

type LoginFlowStep = "login" | "forgot-request" | "forgot-otp" | "forgot-reset"

export default function Login() {
  const {
    login,
    forgotPassword,
    verifyResetCode,
    resetPassword,
    loginWithGoogle,
  } = useAuth()
  const { navigate } = useRouter()

  const uid = useId()
  const emailInputId = `login-email-${uid}`
  const passwordInputId = `login-password-${uid}`
  const newPasswordInputId = `reset-password-${uid}`
  const confirmPasswordInputId = `confirm-password-${uid}`
  const resetStrengthId = `reset-strength-${uid}`
  const rememberId = `login-remember-${uid}`

  const [step, setStep] = useState<LoginFlowStep>(() => {
    if (typeof window === "undefined") return "login"
    const view = new URLSearchParams(window.location.search).get("view")
    return view === "forgot" ? "forgot-request" : "login"
  })

  const [email, setEmail] = useState<string>(() => {
    if (typeof window === "undefined") return ""
    const params = new URLSearchParams(window.location.search)
    const queryEmail = params.get("email")?.trim()
    if (queryEmail) return queryEmail.toLowerCase()
    try {
      const savedEmail = window.localStorage.getItem(REMEMBERED_EMAIL_KEY)
      if (savedEmail && savedEmail.trim()) return savedEmail.trim().toLowerCase()
    } catch {
      // Ignore storage access errors
    }
    return ""
  })
  const [password, setPassword] = useState("")
  const [rememberEmail, setRememberEmail] = useState(true)
  const [nextParam] = useState<string | null>(() => {
    if (typeof window === "undefined") return null
    return new URLSearchParams(window.location.search).get("next")
  })

  // Forgot-password 3-step state (Request -> Verify OTP -> Set New Password)
  const [otpCode, setOtpCode] = useState("")
  const [resetToken, setResetToken] = useState("")
  const [devOtpCode, setDevOtpCode] = useState<string | undefined>(undefined)
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [resendSec, setResendSec] = useState(0)

  const [emailError, setEmailError] = useState<string | undefined>(undefined)
  const [passwordError, setPasswordError] = useState<string | undefined>(undefined)
  const [confirmError, setConfirmError] = useState<string | undefined>(undefined)
  const [otpError, setOtpError] = useState<string | undefined>(undefined)
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

  const handleEmailBlur = () => {
    if (!email.trim()) return
    const err = validateEmail(email)
    setEmailError(err ?? undefined)
  }

  const switchStep = (nextStep: LoginFlowStep) => {
    setStep(nextStep)
    setEmailError(undefined)
    setPasswordError(undefined)
    setConfirmError(undefined)
    setOtpError(undefined)
    setServerError(null)
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
      const normalized = extractAuthError(error, "login")
      setServerError(normalized)
      throw error
    }
  }

  const submitLogin = async () => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    const normalizedEmail = email.trim().toLowerCase()
    const emailValidationMsg = validateEmail(normalizedEmail)
    const pwValidationMsg =
      password.length === 0
        ? "Vui lòng nhập mật khẩu của bạn."
        : password.length < 6
          ? "Mật khẩu cần có tối thiểu 6 ký tự."
          : undefined

    setEmailError(emailValidationMsg ?? undefined)
    setPasswordError(pwValidationMsg)
    setServerError(null)

    if (emailValidationMsg) {
      focusField(emailInputId)
      return
    }
    if (pwValidationMsg) {
      focusField(passwordInputId)
      return
    }

    submitLockRef.current = true
    setSubmitting(true)

    try {
      await login(normalizedEmail, password)

      try {
        if (rememberEmail) {
          window.localStorage.setItem(REMEMBERED_EMAIL_KEY, normalizedEmail)
        } else {
          window.localStorage.removeItem(REMEMBERED_EMAIL_KEY)
        }
      } catch {
        // Ignore storage write errors
      }

      const target = getSafeRedirectTarget(nextParam)
      navigate(target)
    } catch (error) {
      const normalized = extractAuthError(error, "login")
      setServerError(normalized)

      if (normalized.fieldErrors?.email) {
        setEmailError(normalized.fieldErrors.email)
      }
      if (normalized.fieldErrors?.password) {
        setPasswordError(normalized.fieldErrors.password)
      }

      if (normalized.isRateLimited) {
        setCooldownSec(30)
      }
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const submitForgotRequest = async (isResend = false) => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    const normalizedEmail = email.trim().toLowerCase()
    const emailValidationMsg = validateEmail(normalizedEmail)

    setEmailError(emailValidationMsg ?? undefined)
    setServerError(null)

    if (emailValidationMsg) {
      focusField(emailInputId)
      return
    }

    submitLockRef.current = true
    setSubmitting(true)

    try {
      const res = await forgotPassword(normalizedEmail)
      setResetToken(res.resetToken ?? "")
      setDevOtpCode(res.devCode)
      if (!isResend) {
        setOtpCode("")
      }
      setOtpError(undefined)
      setResendSec(60)
      setStep("forgot-otp")
    } catch (error) {
      const normalized = extractAuthError(error, "login")
      setServerError(normalized)
      if (normalized.isRateLimited) {
        setCooldownSec(30)
      }
    } finally {
      submitLockRef.current = false
      setSubmitting(false)
    }
  }

  const submitVerifyResetOtp = async () => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    setOtpError(undefined)
    setServerError(null)

    const cleanCode = otpCode.replace(/\D/g, "")
    if (cleanCode.length !== 6) {
      setOtpError("Vui lòng nhập đủ 6 chữ số xác minh từ email.")
      return
    }

    submitLockRef.current = true
    setSubmitting(true)

    try {
      const normalizedEmail = email.trim().toLowerCase()
      await verifyResetCode(normalizedEmail, cleanCode, resetToken)
      setNewPassword("")
      setConfirmPassword("")
      setPasswordError(undefined)
      setConfirmError(undefined)
      setStep("forgot-reset")
    } catch (error) {
      const normalized = extractAuthError(error, "login")
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

  const submitResetPassword = async () => {
    if (submitLockRef.current || submitting || cooldownSec > 0) return

    setPasswordError(undefined)
    setConfirmError(undefined)
    setServerError(null)

    const strength = evaluatePasswordStrength(newPassword)
    if (!newPassword) {
      setPasswordError("Vui lòng nhập mật khẩu mới.")
      focusField(newPasswordInputId)
      return
    }
    if (!strength.meetsMinimum) {
      setPasswordError("Mật khẩu mới cần có tối thiểu 6 ký tự.")
      focusField(newPasswordInputId)
      return
    }
    if (confirmPassword !== newPassword) {
      setConfirmError("Mật khẩu xác nhận không khớp với mật khẩu mới.")
      focusField(confirmPasswordInputId)
      return
    }

    submitLockRef.current = true
    setSubmitting(true)

    try {
      const normalizedEmail = email.trim().toLowerCase()
      const cleanCode = otpCode.replace(/\D/g, "")
      await resetPassword(normalizedEmail, cleanCode, resetToken, newPassword)

      try {
        window.localStorage.setItem(REMEMBERED_EMAIL_KEY, normalizedEmail)
      } catch {
        // Ignore storage write errors
      }

      const target = getSafeRedirectTarget(nextParam)
      navigate(target)
    } catch (error) {
      const normalized = extractAuthError(error, "login")
      if (normalized.code === "INVALID_OTP") {
        setOtpError(normalized.message)
        setStep("forgot-otp")
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

  const navigateToSignup = () => {
    navigate(buildAuthHref("/signup", { email, next: nextParam }))
  }

  const isDisabled = submitting || cooldownSec > 0

  const shellTitle =
    step === "login"
      ? "Chào mừng trở lại"
      : step === "forgot-request"
        ? "Khôi phục mật khẩu"
        : step === "forgot-otp"
          ? "Xác minh mã OTP"
          : "Thiết lập mật khẩu mới"

  const shellSub =
    step === "login"
      ? "Đăng nhập để tiếp tục theo dõi danh mục, luận điểm đầu tư và dữ liệu thị trường trực tiếp."
      : step === "forgot-request"
        ? "Nhập địa chỉ email đã đăng ký để nhận mã xác minh gồm 6 chữ số qua hộp thư của bạn."
        : step === "forgot-otp"
          ? "Nhập mã OTP 6 chữ số vừa được gửi tới email của bạn để xác thực quyền sở hữu tài khoản."
          : "Xác thực OTP thành công. Vui lòng tạo mật khẩu mới cho không gian làm việc của bạn."

  return (
    <AuthShell
      mode="login"
      title={shellTitle}
      sub={shellSub}
      email={email}
      nextParam={nextParam}
      footer={
        step === "login" ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Chưa có tài khoản nghiên cứu?</span>
            <button
              type="button"
              onClick={navigateToSignup}
              className="font-semibold text-ink underline-offset-4 transition-colors hover:text-mineral hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
            >
              Tạo tài khoản mới →
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Đã nhớ mật khẩu của bạn?</span>
            <button
              type="button"
              onClick={() => switchStep("login")}
              className="font-semibold text-ink underline-offset-4 transition-colors hover:text-mineral hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
            >
              ← Quay lại đăng nhập
            </button>
          </div>
        )
      }
    >
      {/* =====================================================================
          STEP 1: STANDARD LOGIN + GOOGLE OAUTH (2-STEP BY GOOGLE, NO OTP)
      ===================================================================== */}
      {step === "login" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void submitLogin()
          }}
          className="space-y-4"
        >
          {serverError && (
            <AuthAlert
              tone={serverError.isRateLimited ? "warning" : "error"}
              title={serverError.title}
              message={
                cooldownSec > 0 && serverError.isRateLimited
                  ? `${serverError.message} (Có thể thử lại sau ${cooldownSec} giây)`
                  : serverError.message
              }
              actionLabel={serverError.actionLabel}
              onAction={
                serverError.actionHref
                  ? () => {
                      if (serverError.actionHref === "/signup") {
                        navigateToSignup()
                      } else if (serverError.actionHref) {
                        navigate(serverError.actionHref)
                      }
                    }
                  : undefined
              }
            />
          )}

          <GoogleAuthButton
            mode="login"
            disabled={isDisabled}
            defaultEmail={email}
            onAuthenticate={handleGoogleAuth}
          />

          <AuthDivider label="hoặc đăng nhập với email" />

          <Field
            id={emailInputId}
            name="email"
            label="Địa chỉ email"
            type="email"
            inputMode="email"
            autoComplete="email"
            value={email}
            placeholder="ten.ban@tochuc.vn"
            disabled={isDisabled}
            error={emailError}
            onBlur={handleEmailBlur}
            onChange={(value) => {
              setEmail(value)
              if (emailError) setEmailError(undefined)
              if (serverError) setServerError(null)
            }}
          />

          <PasswordField
            id={passwordInputId}
            name="password"
            label="Mật khẩu"
            autoComplete="current-password"
            value={password}
            placeholder="Nhập mật khẩu của bạn"
            disabled={isDisabled}
            error={passwordError}
            rightLabel={
              <button
                type="button"
                disabled={isDisabled}
                onClick={() => switchStep("forgot-request")}
                className="text-[12px] font-medium text-teal underline-offset-4 transition-colors hover:text-ink hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
              >
                Quên mật khẩu?
              </button>
            }
            onChange={(value) => {
              setPassword(value)
              if (passwordError) setPasswordError(undefined)
              if (serverError) setServerError(null)
            }}
          />

          <div className="flex items-center justify-between pt-0.5">
            <label
              htmlFor={rememberId}
              className="inline-flex cursor-pointer select-none items-center gap-2.5 text-[13px] text-secondary hover:text-ink"
            >
              <input
                id={rememberId}
                type="checkbox"
                checked={rememberEmail}
                disabled={isDisabled}
                onChange={(e) => setRememberEmail(e.target.checked)}
                className="h-4 w-4 rounded-[5px] border-[#C5D0CB] text-ink accent-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-mineral"
              />
              <span>Ghi nhớ email trên thiết bị này</span>
            </label>
          </div>

          <div className="pt-1.5">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-11 w-full rounded-[10px] text-[14.5px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang xác thực…</span>
                </span>
              ) : cooldownSec > 0 ? (
                <span className="font-mono tnum">
                  Thử lại sau {cooldownSec}s
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Đăng nhập không gian làm việc</span>
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
          STEP 2A: FORGOT PASSWORD — ENTER EMAIL TO RECEIVE OTP
      ===================================================================== */}
      {step === "forgot-request" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void submitForgotRequest(false)
          }}
          className="space-y-4"
        >
          {serverError && (
            <AuthAlert
              tone={serverError.isRateLimited ? "warning" : "error"}
              title={serverError.title}
              message={serverError.message}
              actionLabel={serverError.actionLabel}
              onAction={
                serverError.actionHref === "/signup"
                  ? () => navigateToSignup()
                  : undefined
              }
            />
          )}

          <Field
            id={emailInputId}
            name="email"
            label="Địa chỉ email khôi phục"
            type="email"
            inputMode="email"
            autoComplete="email"
            value={email}
            placeholder="ten.ban@tochuc.vn"
            disabled={isDisabled}
            error={emailError}
            hint="Hệ thống sẽ gửi mã OTP gồm 6 chữ số qua Gmail (có hiệu lực trong 10 phút)."
            onBlur={handleEmailBlur}
            onChange={(value) => {
              setEmail(value)
              if (emailError) setEmailError(undefined)
              if (serverError) setServerError(null)
            }}
          />

          <div className="pt-1.5">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-11 w-full rounded-[10px] text-[14.5px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang gửi mã xác minh…</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Tiếp theo — Nhận mã OTP qua Email</span>
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
          STEP 2B: FORGOT PASSWORD — VERIFY 6-DIGIT EMAIL OTP
      ===================================================================== */}
      {step === "forgot-otp" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void submitVerifyResetOtp()
          }}
          className="space-y-4"
        >
          {serverError && (
            <AuthAlert
              tone={serverError.isRateLimited ? "warning" : "error"}
              title={serverError.title}
              message={serverError.message}
            />
          )}

          <OtpCodeInput
            recipientEmail={email}
            onEditEmail={() => switchStep("forgot-request")}
            value={otpCode}
            onChange={(code) => {
              setOtpCode(code)
              if (otpError) setOtpError(undefined)
            }}
            disabled={isDisabled}
            error={otpError}
            devCode={devOtpCode}
            resendCooldown={resendSec}
            onResend={() => void submitForgotRequest(true)}
          />

          <div className="pt-1.5">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-11 w-full rounded-[10px] text-[14.5px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang kiểm tra mã OTP…</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Xác minh mã OTP</span>
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
          STEP 2C: FORGOT PASSWORD — CREATE NEW PASSWORD (AFTER OTP VERIFIED)
      ===================================================================== */}
      {step === "forgot-reset" && (
        <form
          noValidate
          aria-busy={submitting}
          onSubmit={(e) => {
            e.preventDefault()
            void submitResetPassword()
          }}
          className="space-y-4"
        >
          {serverError && (
            <AuthAlert
              tone={serverError.isRateLimited ? "warning" : "error"}
              title={serverError.title}
              message={serverError.message}
            />
          )}

          <div className="flex items-center justify-between gap-2 rounded-[10px] border border-line bg-paper px-3.5 py-2.5 text-[12.5px]">
            <span className="font-mono text-ink">
              ✓ Đã xác thực OTP cho <strong>{email.trim().toLowerCase()}</strong>
            </span>
          </div>

          <div>
            <PasswordField
              id={newPasswordInputId}
              name="newPassword"
              label="Mật khẩu mới"
              autoComplete="new-password"
              value={newPassword}
              placeholder="Tạo mật khẩu mới (tối thiểu 6 ký tự)"
              disabled={isDisabled}
              error={passwordError}
              describedByIds={resetStrengthId}
              onChange={(val) => {
                setNewPassword(val)
                if (passwordError) setPasswordError(undefined)
              }}
            />
            <PasswordStrengthMeter id={resetStrengthId} password={newPassword} />
          </div>

          <PasswordField
            id={confirmPasswordInputId}
            name="confirmPassword"
            label="Xác nhận mật khẩu mới"
            autoComplete="new-password"
            value={confirmPassword}
            placeholder="Nhập lại mật khẩu mới"
            disabled={isDisabled}
            error={confirmError}
            onChange={(val) => {
              setConfirmPassword(val)
              if (confirmError) setConfirmError(undefined)
            }}
          />

          <div className="pt-1.5">
            <Button
              type="submit"
              variant="primary"
              disabled={isDisabled}
              className="group h-11 w-full rounded-[10px] text-[14.5px] font-medium shadow-[0_2px_6px_rgba(24,32,29,0.14)] transition-all active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <span className="inline-flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-4 w-4 animate-spin rounded-full border-2 border-paper/30 border-t-paper"
                  />
                  <span>Đang cập nhật mật khẩu…</span>
                </span>
              ) : (
                <span className="inline-flex items-center gap-2">
                  <span>Lưu mật khẩu & Đăng nhập ngay</span>
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
