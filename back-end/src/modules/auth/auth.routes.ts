import { Router, Request, Response, NextFunction } from 'express';
import axios from 'axios';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import crypto from 'crypto';
import nodemailer from 'nodemailer';
import { z } from 'zod';
import prisma from '../../config/database';
import { config } from '../../config';
import { authMiddleware, AuthRequest } from '../../middleware/auth';
import { authLimiter } from '../../middleware/rateLimiter';

const router = Router();
const db = prisma as any;
const REFRESH_COOKIE_NAME = 'refreshToken';
const REFRESH_COOKIE_PATH = '/api/v1/auth';

const sendRegisterCodeSchema = z.object({
  email: z.string().email(),
  displayName: z.string().optional(),
});

const registerSchema = z.object({
  email: z.string().email(),
  password: z.string().min(6),
  displayName: z.string().optional(),
  verificationCode: z.string().regex(/^\d{6}$/).optional(),
  verificationToken: z.string().optional(),
});

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string(),
});

const forgotPasswordSchema = z.object({
  email: z.string().email(),
});

const verifyResetCodeSchema = z.object({
  email: z.string().email(),
  code: z.string().regex(/^\d{6}$/),
  resetToken: z.string().min(10),
});

const resetPasswordSchema = z.object({
  email: z.string().email(),
  code: z.string().regex(/^\d{6}$/),
  resetToken: z.string().min(10),
  newPassword: z.string().min(6),
});

const googleAuthSchema = z.object({
  credential: z.string().optional(),
  accessToken: z.string().optional(),
  email: z.string().email().optional(),
  displayName: z.string().optional(),
});

function parseDuration(duration: string) {
  const match = duration.match(/^(\d+)([smhd])$/);
  if (!match) return 0;
  const value = Number(match[1]);
  switch (match[2]) {
    case 's': return value * 1000;
    case 'm': return value * 60 * 1000;
    case 'h': return value * 60 * 60 * 1000;
    case 'd': return value * 24 * 60 * 60 * 1000;
    default: return value * 1000;
  }
}

function createAccessToken(userId: string) {
  return (jwt.sign as any)(
    { userId, type: 'access' },
    config.jwt.accessSecret,
    { expiresIn: config.jwt.accessExpiresIn },
  );
}

function createRefreshToken(userId: string) {
  const tokenId = crypto.randomUUID();
  const token = (jwt.sign as any)(
    { userId, tokenId, type: 'refresh' },
    config.jwt.refreshSecret,
    { expiresIn: config.jwt.refreshExpiresIn },
  );
  return { token, tokenId };
}

function hashOtpCode(email: string, purpose: 'register_verify' | 'password_reset', code: string): string {
  return crypto
    .createHmac('sha256', config.jwt.accessSecret)
    .update(`${purpose}:${email.trim().toLowerCase()}:${code.trim()}`)
    .digest('hex');
}

function createSignedOtpToken(email: string, purpose: 'register_verify' | 'password_reset', code: string): string {
  const cleanEmail = email.trim().toLowerCase();
  const codeHash = hashOtpCode(cleanEmail, purpose, code);
  return (jwt.sign as any)(
    { email: cleanEmail, purpose, codeHash },
    config.jwt.accessSecret,
    { expiresIn: '10m' },
  );
}

function verifySignedOtpToken(
  token: string,
  expectedEmail: string,
  expectedPurpose: 'register_verify' | 'password_reset',
  submittedCode: string,
): boolean {
  try {
    const decoded = jwt.verify(token, config.jwt.accessSecret) as {
      email?: string;
      purpose?: string;
      codeHash?: string;
    };
    const cleanEmail = expectedEmail.trim().toLowerCase();
    if (!decoded || decoded.purpose !== expectedPurpose || decoded.email !== cleanEmail || !decoded.codeHash) {
      return false;
    }
    const expectedHash = hashOtpCode(cleanEmail, expectedPurpose, submittedCode);
    const a = Buffer.from(decoded.codeHash, 'hex');
    const b = Buffer.from(expectedHash, 'hex');
    if (a.length !== b.length) return false;
    return crypto.timingSafeEqual(a, b);
  } catch {
    return false;
  }
}

function buildOtpEmailHtml(params: {
  code: string;
  purpose: 'register_verify' | 'password_reset';
  recipientEmail: string;
}): { subject: string; html: string; text: string } {
  const isRegister = params.purpose === 'register_verify';
  const digits = params.code.trim().split('');
  const firstTriplet = digits.slice(0, 3);
  const secondTriplet = digits.slice(3, 6);

  const subject = isRegister
    ? `[AIInvest] ${params.code} — Mã xác minh kích hoạt tài khoản của bạn`
    : `[AIInvest] ${params.code} — Mã xác thực khôi phục mật khẩu của bạn`;

  const badgeLabel = isRegister
    ? 'KÍCH HOẠT TÀI KHOẢN · XÁC MINH EMAIL'
    : 'BẢO MẬT TÀI KHOẢN · KHÔI PHỤC MẬT KHẨU';

  const heading = isRegister
    ? 'Xác minh hộp thư để kích hoạt không gian nghiên cứu.'
    : 'Mã xác thực khôi phục quyền truy cập tài khoản.';

  const description = isRegister
    ? 'Cảm ơn bạn đã khởi tạo tài khoản tại AIInvest. Vui lòng nhập dãy 6 chữ số xác thực bên dưới vào màn hình đăng ký để hoàn tất xác minh quyền sở hữu hộp thư.'
    : 'Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản AIInvest gắn với địa chỉ email này. Vui lòng nhập dãy 6 chữ số bên dưới để tiếp tục thiết lập mật khẩu mới.';

  const text = `${heading}\n\nMã OTP 6 chữ số của bạn: ${params.code}\nThời hạn hiệu lực: 10 phút (chỉ sử dụng 1 lần)\nTài khoản nhận: ${params.recipientEmail}\n\nNếu bạn không thực hiện yêu cầu này, vui lòng bỏ qua email này.`;

  const renderDigitCell = (d: string) => `
    <td style="padding:0 3px;">
      <div style="width:42px;height:52px;line-height:52px;text-align:center;background-color:#FFFFFF;border:1px solid #C8D3CD;border-bottom:2px solid #19362E;border-radius:10px;font-family:'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace;font-size:24px;font-weight:700;color:#18201D;">
        ${d}
      </div>
    </td>
  `;

  const html = `
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${subject}</title>
</head>
<body style="margin:0;padding:40px 16px;background-color:#F2F4F0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#18201D;-webkit-font-smoothing:antialiased;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    <tr>
      <td align="center">
        <!-- Main Editorial Letter Container -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background-color:#FFFFFF;border:1px solid #D5DDD8;border-radius:18px;overflow:hidden;box-shadow:0 18px 48px rgba(24,32,29,0.07);">
          <!-- Top Dual-Tone Institutional Accent Bar -->
          <tr>
            <td style="height:4px;background:linear-gradient(90deg,#19362E 0%,#2E594E 65%,#9E7B47 100%);font-size:0;line-height:0;">&nbsp;</td>
          </tr>

          <!-- Masthead Header -->
          <tr>
            <td style="padding:22px 32px;background-color:#F7F8F5;border-bottom:1px solid #E3E8E4;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="left" valign="middle">
                    <table role="presentation" cellpadding="0" cellspacing="0">
                      <tr>
                        <td valign="middle">
                          <div style="width:34px;height:34px;line-height:34px;text-align:center;background-color:#19362E;color:#F5F6F3;border-radius:9px;font-family:Georgia,serif;font-weight:700;font-size:14px;letter-spacing:0.03em;">
                            AI
                          </div>
                        </td>
                        <td valign="middle" style="padding-left:11px;">
                          <div style="font-size:15px;font-weight:700;color:#18201D;letter-spacing:-0.01em;">
                            AIInvest
                          </div>
                          <div style="font-size:10.5px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;color:#66756F;">
                            Quantitative Research Desk
                          </div>
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td align="right" valign="middle">
                    <span style="display:inline-block;padding:5px 11px;background-color:#FFFFFF;border:1px solid #D5DDD8;border-radius:999px;font-family:'SFMono-Regular',Consolas,monospace;font-size:11px;font-weight:600;color:#2E594E;">
                      ● OTP · TLS 1.3
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Main Content Body -->
          <tr>
            <td style="padding:32px 32px 28px;">
              <div style="margin:0 0 10px;font-family:'SFMono-Regular',Consolas,monospace;font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:#2E594E;">
                ${badgeLabel}
              </div>

              <h1 style="margin:0 0 14px;font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:500;color:#18201D;line-height:1.24;letter-spacing:-0.015em;">
                ${heading}
              </h1>

              <p style="margin:0 0 26px;font-size:14.5px;line-height:1.65;color:#4E5B56;">
                ${description}
              </p>

              <!-- 6-Digit Visual Chamber (3 — 3 Grouping + Quick Copy Pill) -->
              <div style="padding:26px 20px 22px;background-color:#F5F6F3;border:1px solid #D5DDD8;border-radius:14px;text-align:center;margin-bottom:24px;">
                <div style="margin-bottom:14px;font-family:'SFMono-Regular',Consolas,monospace;font-size:10.5px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#66756F;">
                  MÃ XÁC MINH GỒM 6 CHỮ SỐ
                </div>

                <!-- 3 — 3 Visual Digit Boxes -->
                <table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:0 auto 16px;">
                  <tr>
                    ${firstTriplet.map(renderDigitCell).join('')}
                    <td style="padding:0 7px;font-family:Georgia,serif;font-size:20px;color:#9E7B47;font-weight:700;">
                      &mdash;
                    </td>
                    ${secondTriplet.map(renderDigitCell).join('')}
                  </tr>
                </table>

                <!-- Contiguous Copyable Code Pill -->
                <div style="margin-bottom:16px;">
                  <span style="display:inline-block;padding:6px 14px;background-color:#FFFFFF;border:1px dashed #B9C7C0;border-radius:8px;font-size:12px;color:#515D58;">
                    Sao chép nhanh:&nbsp;
                    <strong style="font-family:'SFMono-Regular',Consolas,monospace;font-size:14px;letter-spacing:0.12em;color:#19362E;user-select:all;">${params.code}</strong>
                  </span>
                </div>

                <!-- Metadata Divider & 2-Column Info -->
                <div style="border-top:1px solid #E1E6E2;padding-top:12px;font-size:12px;color:#5E6C66;line-height:1.5;">
                  <span>⏱ Hiệu lực: <strong style="color:#18201D;">10 phút</strong> (Dùng 1 lần)</span>
                  <span style="margin:0 8px;color:#C2CCC7;">|</span>
                  <span>🔒 Nhận tại: <strong style="color:#18201D;">${params.recipientEmail}</strong></span>
                </div>
              </div>

              <!-- Security Provenance Callout -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#FAF8F3;border-left:3px solid #9E7B47;border-radius:6px;">
                <tr>
                  <td style="padding:13px 16px;font-size:12.5px;line-height:1.58;color:#5A564C;">
                    <strong style="color:#18201D;">Nguyên tắc bảo mật:</strong> Đội ngũ AIInvest không bao giờ yêu cầu bạn cung cấp mã OTP này qua điện thoại, tin nhắn hoặc mạng xã hội. Nếu bạn không khởi tạo yêu cầu này, hãy bỏ qua thư này — tài khoản của bạn vẫn an toàn tuyệt đối.
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- 3-Pillar Institutional Footer -->
          <tr>
            <td style="padding:18px 32px;background-color:#F7F8F5;border-top:1px solid #E3E8E4;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="left" style="font-family:'SFMono-Regular',Consolas,monospace;font-size:11px;color:#66756F;">
                    12 Tác tử Định lượng &nbsp;·&nbsp; 04 Lớp Bằng chứng BCTC &nbsp;·&nbsp; 01 Khung Rủi ro
                  </td>
                  <td align="right" style="font-size:11.5px;font-weight:600;color:#19362E;">
                    aiinvest.cloud
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`.trim();

  return { subject, html, text };
}

async function sendOtpEmail(params: {
  to: string;
  code: string;
  purpose: 'register_verify' | 'password_reset';
}): Promise<boolean> {
  const { subject, html, text } = buildOtpEmailHtml({
    code: params.code,
    purpose: params.purpose,
    recipientEmail: params.to,
  });

  const resendApiKey = process.env.RESEND_API_KEY?.trim();
  const emailFrom = process.env.EMAIL_FROM?.trim() || 'AIInvest <onboarding@resend.dev>';

  if (resendApiKey) {
    try {
      await axios.post(
        'https://api.resend.com/emails',
        {
          from: emailFrom,
          to: [params.to],
          subject,
          html,
          text,
        },
        {
          headers: {
            Authorization: `Bearer ${resendApiKey}`,
            'Content-Type': 'application/json',
          },
          timeout: 8000,
        },
      );
      return true;
    } catch (err: any) {
      console.error('[Auth Mail] Resend delivery failed:', err?.response?.data || err?.message);
    }
  }

  const smtpUser = process.env.SMTP_USER?.trim();
  const smtpPass = process.env.SMTP_PASS?.replace(/\s+/g, '');
  const smtpHost = process.env.SMTP_HOST?.trim() || 'smtp.gmail.com';
  const smtpPort = Number(process.env.SMTP_PORT || 465);

  if (smtpUser && smtpPass) {
    try {
      const transporter = nodemailer.createTransport({
        host: smtpHost,
        port: smtpPort,
        secure: smtpPort === 465,
        auth: {
          user: smtpUser,
          pass: smtpPass,
        },
      });
      await transporter.sendMail({
        from: process.env.EMAIL_FROM?.trim() || `"AIInvest Research" <${smtpUser}>`,
        to: params.to,
        subject,
        text,
        html,
      });
      return true;
    } catch (err: any) {
      console.error('[Auth Mail] SMTP delivery failed:', err?.message);
    }
  }

  return false;
}

function parseCookies(cookieHeader: string | undefined): Record<string, string> {
  if (!cookieHeader) return {};
  return Object.fromEntries(cookieHeader.split(';').map((segment) => {
    const [name, ...valueParts] = segment.trim().split('=');
    return [name, decodeURIComponent(valueParts.join('='))];
  }));
}

function getRefreshTokenFromRequest(req: Request) {
  const cookies = parseCookies(req.headers.cookie);
  return cookies[REFRESH_COOKIE_NAME];
}

function setRefreshTokenCookie(res: Response, token: string) {
  const maxAge = parseDuration(config.jwt.refreshExpiresIn);
  res.cookie(REFRESH_COOKIE_NAME, token, {
    httpOnly: true,
    secure: config.nodeEnv === 'production',
    sameSite: 'lax',
    path: REFRESH_COOKIE_PATH,
    maxAge,
  });
}

function clearRefreshTokenCookie(res: Response) {
  res.clearCookie(REFRESH_COOKIE_NAME, {
    httpOnly: true,
    secure: config.nodeEnv === 'production',
    sameSite: 'lax',
    path: REFRESH_COOKIE_PATH,
  });
}

async function createSessionTokens(userId: string, client = db) {
  const accessToken = createAccessToken(userId);
  const refresh = createRefreshToken(userId);

  await client.refreshToken.create({
    data: {
      tokenId: refresh.tokenId,
      userId,
      expiresAt: new Date(Date.now() + parseDuration(config.jwt.refreshExpiresIn)),
    },
  });

  return {
    accessToken,
    refreshToken: refresh.token,
  };
}

async function revokeRefreshToken(tokenId: string) {
  await db.refreshToken.updateMany({
    where: { tokenId },
    data: { isRevoked: true },
  });
}

function buildUserResponse(user: { id: string; email: string; displayName: string | null }) {
  return { id: user.id, email: user.email, displayName: user.displayName };
}

router.post('/register/send-code', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = sendRegisterCodeSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();

    const existing = await prisma.user.findUnique({ where: { email } });
    if (existing) {
      res.status(409).json({ code: 'EMAIL_EXISTS', error: 'Email already registered' });
      return;
    }

    const otpCode = String(crypto.randomInt(100000, 1000000));
    const verificationToken = createSignedOtpToken(email, 'register_verify', otpCode);

    const delivered = await sendOtpEmail({
      to: email,
      code: otpCode,
      purpose: 'register_verify',
    });

    if (!delivered) {
      console.info(`[Auth Mail Verification] Registration OTP for ${email}: ${otpCode}`);
    }

    res.status(200).json({
      message: 'Mã xác minh gồm 6 chữ số đã được gửi đến địa chỉ email của bạn.',
      verificationToken,
      expiresInSeconds: 600,
      ...(config.nodeEnv !== 'production' && !delivered ? { devCode: otpCode } : {}),
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/register', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = registerSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();
    const { password, displayName, verificationCode, verificationToken } = parsed;

    if (verificationToken !== undefined || verificationCode !== undefined) {
      if (
        !verificationToken ||
        !verificationCode ||
        !verifySignedOtpToken(verificationToken, email, 'register_verify', verificationCode)
      ) {
        res.status(400).json({
          code: 'INVALID_OTP',
          error: 'Mã xác minh email không chính xác hoặc đã hết hạn.',
        });
        return;
      }
    }

    const existing = await prisma.user.findUnique({ where: { email } });
    if (existing) {
      res.status(409).json({ error: 'Email already registered' });
      return;
    }

    const passwordHash = await bcrypt.hash(password, 12);
    const user = await prisma.user.create({
      data: { email, passwordHash, displayName },
    });

    const tokens = await createSessionTokens(user.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.status(201).json({
      user: buildUserResponse(user),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/login', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = loginSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();
    const { password } = parsed;

    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      res.status(401).json({ error: 'Invalid credentials' });
      return;
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      res.status(401).json({ error: 'Invalid credentials' });
      return;
    }

    const tokens = await createSessionTokens(user.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.json({
      user: buildUserResponse(user),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/forgot-password', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = forgotPasswordSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();

    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      res.status(404).json({
        code: 'USER_NOT_FOUND',
        error: 'Không tìm thấy tài khoản nào được đăng ký với địa chỉ email này.',
      });
      return;
    }

    const otpCode = String(crypto.randomInt(100000, 1000000));
    const resetToken = createSignedOtpToken(email, 'password_reset', otpCode);

    const delivered = await sendOtpEmail({
      to: email,
      code: otpCode,
      purpose: 'password_reset',
    });

    if (!delivered) {
      console.info(`[Auth Password Reset] Recovery OTP for ${email}: ${otpCode}`);
    }

    res.status(200).json({
      message: 'Mã khôi phục mật khẩu gồm 6 chữ số đã được gửi đến email của bạn.',
      resetToken,
      expiresInSeconds: 600,
      ...(config.nodeEnv !== 'production' && !delivered ? { devCode: otpCode } : {}),
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/verify-reset-code', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = verifyResetCodeSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();
    const { code, resetToken } = parsed;

    if (!verifySignedOtpToken(resetToken, email, 'password_reset', code)) {
      res.status(400).json({
        code: 'INVALID_OTP',
        error: 'Mã xác minh OTP không chính xác hoặc đã hết hạn.',
      });
      return;
    }

    res.status(200).json({
      verified: true,
      message: 'Xác minh mã OTP thành công. Vui lòng thiết lập mật khẩu mới.',
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/reset-password', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = resetPasswordSchema.parse(req.body);
    const email = parsed.email.trim().toLowerCase();
    const { code, resetToken, newPassword } = parsed;

    if (!verifySignedOtpToken(resetToken, email, 'password_reset', code)) {
      res.status(400).json({
        code: 'INVALID_OTP',
        error: 'Mã khôi phục không chính xác hoặc đã hết hạn.',
      });
      return;
    }

    const user = await prisma.user.findUnique({ where: { email } });
    if (!user) {
      res.status(404).json({
        code: 'USER_NOT_FOUND',
        error: 'Không tìm thấy tài khoản với địa chỉ email này.',
      });
      return;
    }

    const passwordHash = await bcrypt.hash(newPassword, 12);
    const updatedUser = await prisma.user.update({
      where: { id: user.id },
      data: { passwordHash },
    });

    await db.refreshToken.updateMany({
      where: { userId: user.id },
      data: { isRevoked: true },
    });

    const tokens = await createSessionTokens(updatedUser.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.status(200).json({
      user: buildUserResponse(updatedUser),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/google', authLimiter, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const parsed = googleAuthSchema.parse(req.body);
    let verifiedEmail: string | undefined;
    let verifiedName: string | undefined;

    if (parsed.credential) {
      try {
        const { data } = await axios.get('https://oauth2.googleapis.com/tokeninfo', {
          params: { id_token: parsed.credential },
          timeout: 8000,
        });
        if (data?.email && (data?.email_verified === 'true' || data?.email_verified === true)) {
          verifiedEmail = String(data.email).trim().toLowerCase();
          verifiedName = data.name ? String(data.name).trim() : undefined;
        }
      } catch {
        res.status(401).json({
          code: 'GOOGLE_TOKEN_INVALID',
          error: 'Không thể xác thực thông tin đăng nhập từ Google.',
        });
        return;
      }
    } else if (parsed.accessToken) {
      try {
        const { data } = await axios.get('https://www.googleapis.com/oauth2/v3/userinfo', {
          headers: { Authorization: `Bearer ${parsed.accessToken}` },
          timeout: 8000,
        });
        if (data?.email) {
          verifiedEmail = String(data.email).trim().toLowerCase();
          verifiedName = data.name ? String(data.name).trim() : undefined;
        }
      } catch {
        res.status(401).json({
          code: 'GOOGLE_TOKEN_INVALID',
          error: 'Không thể xác thực phiên truy cập từ Google.',
        });
        return;
      }
    } else if (config.nodeEnv !== 'production' && parsed.email) {
      verifiedEmail = parsed.email.trim().toLowerCase();
      verifiedName = parsed.displayName?.trim() || verifiedEmail.split('@')[0];
    }

    if (!verifiedEmail) {
      res.status(400).json({
        code: 'GOOGLE_AUTH_MISSING',
        error: 'Thiếu thông tin xác thực tài khoản Google.',
      });
      return;
    }

    let user = await prisma.user.findUnique({ where: { email: verifiedEmail } });
    if (!user) {
      const randomPasswordHash = await bcrypt.hash(crypto.randomBytes(24).toString('hex'), 12);
      user = await prisma.user.create({
        data: {
          email: verifiedEmail,
          passwordHash: randomPasswordHash,
          displayName: verifiedName || verifiedEmail.split('@')[0],
        },
      });
    } else if (!user.displayName && verifiedName) {
      user = await prisma.user.update({
        where: { id: user.id },
        data: { displayName: verifiedName },
      });
    }

    const tokens = await createSessionTokens(user.id);
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.status(200).json({
      user: buildUserResponse(user),
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/refresh', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const refreshToken = getRefreshTokenFromRequest(req);
    if (!refreshToken) {
      res.status(401).json({ error: 'Refresh token missing' });
      return;
    }

    let decoded;
    try {
      decoded = jwt.verify(refreshToken, config.jwt.refreshSecret) as { userId: string; tokenId: string; type: string };
    } catch {
      res.status(401).json({ error: 'Invalid or expired refresh token' });
      return;
    }

    if (decoded.type !== 'refresh' || !decoded.tokenId) {
      res.status(401).json({ error: 'Invalid refresh token payload' });
      return;
    }

    const tokens = await prisma.$transaction(async (tx) => {
      const consumed = await (tx as any).refreshToken.updateMany({
        where: {
          tokenId: decoded.tokenId,
          userId: decoded.userId,
          isRevoked: false,
          expiresAt: { gt: new Date() },
        },
        data: { isRevoked: true },
      });
      if (consumed.count !== 1) throw new Error('REFRESH_TOKEN_REUSED');
      return createSessionTokens(decoded.userId, tx as any);
    });
    setRefreshTokenCookie(res, tokens.refreshToken);

    res.json({
      accessToken: tokens.accessToken,
    });
  } catch (err: any) {
    if (err.message === 'REFRESH_TOKEN_REUSED') {
      res.status(401).json({ error: 'Refresh token revoked or expired' });
      return;
    }
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.post('/logout', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const refreshToken = getRefreshTokenFromRequest(req);
    if (refreshToken) {
      try {
        const decoded = jwt.verify(refreshToken, config.jwt.refreshSecret) as { tokenId: string };
        if (decoded?.tokenId) {
          await revokeRefreshToken(decoded.tokenId);
        }
      } catch {
        // ignore invalid refresh token during logout
      }
    }

    clearRefreshTokenCookie(res);
    res.status(204).send();
  } catch (err: any) {
    if (err.name === 'ZodError') {
      res.status(400).json({ error: 'Validation error', details: err.issues });
      return;
    }
    next(err);
  }
});

router.get('/me', authMiddleware, authLimiter, async (req: AuthRequest, res: Response, next: NextFunction) => {
  try {
    const user = await prisma.user.findUnique({
      where: { id: req.userId },
      select: { id: true, email: true, displayName: true, createdAt: true },
    });

    if (!user) {
      res.status(404).json({ error: 'User not found' });
      return;
    }

    res.json(user);
  } catch (err) {
    next(err);
  }
});

export default router;
