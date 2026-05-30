import Link from 'next/link';

/* ─────────────────────────────────────────────
   Landing Page — AIInvest
   Layout: Asymmetric split-screen hero (DESIGN_VARIANCE=8)
   Motion: CSS stagger + marquee (MOTION_INTENSITY=6)
   Density: Marketing page — airy (VISUAL_DENSITY=3)
───────────────────────────────────────────── */

/* Static market data for the ticker */
const tickerItems = [
  { symbol: 'VN-INDEX', value: '1,287.43', change: '+12.31', pct: '+0.97%', up: true },
  { symbol: 'VN30',     value: '1,301.18', change: '+9.84',  pct: '+0.76%', up: true },
  { symbol: 'HNX',      value: '231.07',   change: '-1.22',  pct: '-0.53%', up: false },
  { symbol: 'VCB',      value: '91.20',    change: '+0.80',  pct: '+0.88%', up: true },
  { symbol: 'HPG',      value: '26.45',    change: '-0.35',  pct: '-1.31%', up: false },
  { symbol: 'FPT',      value: '138.60',   change: '+2.10',  pct: '+1.54%', up: true },
  { symbol: 'MWG',      value: '62.30',    change: '+0.50',  pct: '+0.81%', up: true },
  { symbol: 'VHM',      value: '44.10',    change: '-0.90',  pct: '-2.00%', up: false },
  { symbol: 'TCB',      value: '19.85',    change: '+0.25',  pct: '+1.27%', up: true },
  { symbol: 'BID',      value: '45.70',    change: '+0.40',  pct: '+0.88%', up: true },
];

const features = [
  {
    icon: 'analytics',
    label: 'AI Scoring',
    title: 'Chấm điểm cổ phiếu theo CANSLIM & VSA',
    desc: 'Hệ thống AI đánh giá 700+ mã theo 47 tiêu chí kỹ thuật và cơ bản mỗi phiên.',
    accent: 'primary',
    size: 'large',
  },
  {
    icon: 'candlestick_chart',
    label: 'Advanced Chart',
    title: 'Biểu đồ kỹ thuật đa lớp',
    desc: 'TradingView tích hợp chỉ số AI độc quyền, sóng Elliott, Fibonacci real-time.',
    accent: 'secondary',
    size: 'small',
  },
  {
    icon: 'rocket_launch',
    label: 'Auto-Pilot',
    title: 'Giao dịch tự động hóa',
    desc: 'Thiết lập điều kiện mua/bán tự động. AI theo dõi 24/7, không bỏ lỡ cơ hội.',
    accent: 'tertiary',
    size: 'small',
  },
  {
    icon: 'groups',
    label: 'Community',
    title: 'Cộng đồng tinh hoa',
    desc: 'Copy lệnh từ top traders. Thảo luận real-time với nhà đầu tư chuyên nghiệp.',
    accent: 'primary',
    size: 'small',
  },
  {
    icon: 'account_balance_wallet',
    label: 'Portfolio',
    title: 'Danh mục thông minh',
    desc: 'Tổng hợp tài sản từ nhiều tài khoản, phân tích rủi ro và phân bổ tối ưu.',
    accent: 'secondary',
    size: 'small',
  },
];

const stats = [
  { value: '847', unit: 'nghìn', label: 'Nhà đầu tư' },
  { value: '2.3',  unit: 'tỷ/ngày', label: 'Giá trị giao dịch' },
  { value: '99.7', unit: '%',   label: 'Uptime thời gian thực' },
  { value: '47',   unit: 'ms',  label: 'Độ trễ dữ liệu' },
];

export default function Home() {
  return (
    <div className="font-body-lg text-body-lg selection:bg-primary/30 bg-[#08080a] text-[#ecebee]">
      <a href="#main-content" className="skip-link">Chuyển đến nội dung chính</a>

      {/* ── Ticker banner ── */}
      <div className="border-b border-white/[0.05] bg-[#060608] h-8 overflow-hidden flex items-center">
        <div className="flex-shrink-0 px-4 h-full flex items-center border-r border-white/[0.05] z-10 bg-[#060608]">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
            <span className="font-label-caps text-[#2dbd7e] text-[9px] tracking-[0.18em]">LIVE</span>
          </span>
        </div>
        <div className="flex overflow-hidden flex-1">
          {/* Duplicate for seamless loop */}
          <div className="flex gap-8 animate-marquee whitespace-nowrap shrink-0">
            {[...tickerItems, ...tickerItems].map((item, i) => (
              <span key={i} className="flex items-center gap-2 font-data-mono text-[10px]">
                <span className="text-white/40 font-bold">{item.symbol}</span>
                <span className="text-white/80">{item.value}</span>
                <span className={item.up ? 'text-[#2dbd7e]' : 'text-[#f87171]'}>
                  {item.change} ({item.pct})
                </span>
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* ── Header ── */}
      <header className="fixed top-8 inset-x-0 z-50 flex justify-between items-center px-lg h-16 bg-[#08080a]/80 backdrop-blur-xl border-b border-white/[0.05]">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg aura-glow flex items-center justify-center shrink-0">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" className="text-[#e8a940]">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor" />
            </svg>
          </div>
          <span className="font-bold text-sm tracking-tight text-white/90">AIInvest</span>
        </div>

        <nav className="hidden md:flex items-center gap-6">
          {[
            { label: 'Dashboard', href: '/dashboard' },
            { label: 'AI Agent', href: '/agent' },
            { label: 'Cộng đồng', href: '/community' },
          ].map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-[13px] text-white/45 hover:text-white/85 transition-colors duration-200 font-medium"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <Link
            href="/auth"
            className="hidden sm:flex px-4 py-2 rounded-lg bg-[#e8a940]/10 border border-[#e8a940]/20 text-[#e8a940] text-[13px] font-semibold hover:bg-[#e8a940]/15 transition-all duration-200 items-center gap-1.5 amber-glow"
          >
            <span className="material-symbols-outlined text-[16px]">login</span>
            Đăng nhập
          </Link>
          <Link
            href="/dashboard"
            className="px-4 py-2 rounded-lg bg-[#e8a940] text-[#1a0d00] text-[13px] font-bold hover:bg-[#f5c46e] transition-all duration-200 btn-press"
          >
            Dùng thử miễn phí
          </Link>
        </div>
      </header>

      <main id="main-content" className="pt-24">

        {/* ─────────── HERO — Asymmetric split-screen ─────────── */}
        <section className="relative min-h-[100dvh] flex items-center overflow-hidden">
          {/* Ambient background */}
          <div className="absolute inset-0 z-0 pointer-events-none">
            <div className="absolute top-0 left-0 w-[700px] h-[700px] bg-[#e8a940]/6 blur-[140px] rounded-full -translate-x-1/4 -translate-y-1/4" />
            <div className="absolute bottom-0 right-0 w-[600px] h-[500px] bg-[#2dbd7e]/4 blur-[160px] rounded-full translate-x-1/4 translate-y-1/4" />
          </div>

          <div className="container-max grid lg:grid-cols-[55%_45%] gap-12 items-center relative z-10 py-24">

            {/* Left: Content — left-aligned, NOT centered */}
            <div className="space-y-8" style={{ animationDelay: '0ms' }}>
              <div className="inline-flex items-center gap-2 px-3 py-1.5 glass-card rounded-full border-[#2dbd7e]/20">
                <span className="w-1.5 h-1.5 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
                <span className="font-label-caps text-[#2dbd7e] text-[9px] tracking-[0.16em]">
                  DỮ LIỆU THỜI GIAN THỰC · HOSE · HNX · UPCOM
                </span>
              </div>

              <h1 className="text-[52px] md:text-[64px] font-extrabold leading-[1.02] tracking-tight">
                Hệ sinh thái đầu tư{' '}
                <span className="text-gradient-primary">AI toàn diện</span>
                {' '}cho thị trường Việt Nam.
              </h1>

              <p className="text-[#95949c] text-lg max-w-[42ch] leading-relaxed">
                Kết hợp phân tích kỹ thuật chuyên nghiệp, trợ lý AI thông minh và cộng đồng nhà đầu tư tinh hoa — tất cả trong một nền tảng.
              </p>

              <div className="flex flex-wrap gap-3 pt-2">
                <Link
                  href="/dashboard"
                  className="group inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#e8a940] text-[#1a0d00] font-bold text-[15px] hover:bg-[#f5c46e] transition-all duration-200 btn-press shadow-primary-tint"
                >
                  Vào Dashboard
                  <span className="material-symbols-outlined text-[18px] group-hover:translate-x-1 transition-transform duration-200">
                    arrow_forward
                  </span>
                </Link>
                <Link
                  href="/auth"
                  className="inline-flex items-center gap-2 px-6 py-3 rounded-xl glass-card text-white/80 font-semibold text-[15px] hover:text-white hover:bg-white/[0.07] transition-all duration-200"
                >
                  <span className="material-symbols-outlined text-[18px]">play_circle</span>
                  Xem demo
                </Link>
              </div>

              {/* Social proof stats */}
              <div className="flex flex-wrap gap-8 pt-4 border-t border-white/[0.06]">
                {stats.map((s) => (
                  <div key={s.label} className="flex flex-col">
                    <span className="font-data-mono text-xl font-bold text-[#ecebee]">
                      {s.value}
                      <span className="text-[#e8a940] text-sm ml-1">{s.unit}</span>
                    </span>
                    <span className="font-label-caps text-[#95949c] text-[9px] mt-0.5">{s.label}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Right: Premium UI preview card — NOT a stock photo */}
            <div className="relative hidden lg:block">
              {/* Outer glow ring */}
              <div className="absolute -inset-4 bg-gradient-to-br from-[#e8a940]/10 via-transparent to-[#2dbd7e]/8 rounded-3xl blur-xl" />

              <div className="relative glass-card rounded-2xl overflow-hidden border-white/8 shadow-2xl">
                {/* Mock terminal header */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06] bg-white/[0.02]">
                  <div className="flex items-center gap-2">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#f87171]/70" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#e8a940]/70" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#2dbd7e]/70" />
                  </div>
                  <span className="font-data-mono text-[10px] text-white/25">AIInvest · Live Dashboard</span>
                  <span className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
                    <span className="font-label-caps text-[#2dbd7e] text-[9px]">LIVE</span>
                  </span>
                </div>

                {/* Mock index bar */}
                <div className="flex items-center gap-0 border-b border-white/[0.05] bg-white/[0.01]">
                  {[
                    { name: 'VN-INDEX', value: '1,287.43', pct: '+0.97%', up: true },
                    { name: 'VN30',     value: '1,301.18', pct: '+0.76%', up: true },
                    { name: 'HNX',      value: '231.07',   pct: '-0.53%', up: false },
                  ].map((idx, i) => (
                    <div key={i} className="flex-1 px-3 py-2.5 border-r last:border-0 border-white/[0.05]">
                      <div className="font-label-caps text-[9px] text-white/30 mb-0.5">{idx.name}</div>
                      <div className="font-data-mono text-[13px] font-bold text-white/90">{idx.value}</div>
                      <div className={`font-data-mono text-[10px] font-bold ${idx.up ? 'text-[#2dbd7e]' : 'text-[#f87171]'}`}>
                        {idx.pct}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Mock candlestick chart (pure CSS/SVG) */}
                <div className="p-4">
                  <svg viewBox="0 0 400 140" className="w-full h-32" preserveAspectRatio="none">
                    {/* Grid lines */}
                    {[0, 1, 2, 3].map(i => (
                      <line key={i} x1="0" y1={i * 35 + 10} x2="400" y2={i * 35 + 10} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
                    ))}
                    {/* Candles */}
                    {[
                      [30,  90, 60,  75, 95,  true],
                      [70,  75, 40,  55, 80,  true],
                      [110, 55, 75,  70, 85,  false],
                      [150, 70, 50,  55, 75,  true],
                      [190, 50, 30,  35, 55,  true],
                      [230, 35, 60,  50, 65,  false],
                      [270, 55, 35,  40, 60,  true],
                      [310, 35, 20,  22, 40,  true],
                      [350, 22, 45,  35, 50,  false],
                    ].map(([x, high, low, open, close, up]: [number, number, number, number, number, boolean], i) => (
                      <g key={i}>
                        <line
                          x1={x} y1={high} x2={x} y2={low}
                          stroke={up ? '#2dbd7e' : '#f87171'}
                          strokeWidth="1.5"
                          opacity="0.7"
                        />
                        <rect
                          x={x - 7} y={up ? close : open}
                          width="14" height={Math.abs((open as number) - (close as number)) || 2}
                          fill={up ? '#2dbd7e' : '#f87171'}
                          opacity="0.85"
                          rx="1"
                        />
                      </g>
                    ))}
                    {/* Moving average line */}
                    <polyline
                      points="30,82 70,68 110,62 150,62 190,45 230,50 270,47 310,30 350,38"
                      fill="none"
                      stroke="#e8a940"
                      strokeWidth="1.5"
                      strokeDasharray="0"
                      opacity="0.7"
                    />
                  </svg>
                </div>

                {/* Mock stock rows */}
                <div className="border-t border-white/[0.05]">
                  {[
                    { sym: 'VCB',  name: 'Vietcombank',  price: '91.20', chg: '+0.88%', vol: '12.4M', up: true },
                    { sym: 'FPT',  name: 'FPT Corp',     price: '138.60', chg: '+1.54%', vol: '8.7M',  up: true },
                    { sym: 'HPG',  name: 'Hòa Phát',     price: '26.45', chg: '-1.31%', vol: '31.2M', up: false },
                  ].map((row, i) => (
                    <div
                      key={i}
                      className="flex items-center px-4 py-2.5 border-b last:border-0 border-white/[0.04] hover:bg-white/[0.025] transition-colors"
                    >
                      <div className="w-10 h-7 rounded-md bg-[#e8a940]/10 border border-[#e8a940]/15 flex items-center justify-center mr-3">
                        <span className="font-bold text-[10px] text-[#e8a940]">{row.sym}</span>
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-bold text-[11px] text-white/80">{row.sym}</div>
                        <div className="text-[9px] text-white/30 truncate">{row.name}</div>
                      </div>
                      <div className="text-right mr-4">
                        <div className="font-data-mono font-bold text-[12px] text-white/85">{row.price}</div>
                        <div className={`font-data-mono text-[10px] font-bold ${row.up ? 'text-[#2dbd7e]' : 'text-[#f87171]'}`}>
                          {row.chg}
                        </div>
                      </div>
                      <div className="font-data-mono text-[10px] text-white/30 w-12 text-right">{row.vol}</div>
                    </div>
                  ))}
                </div>

                {/* Gradient fade at bottom */}
                <div className="absolute bottom-0 inset-x-0 h-12 bg-gradient-to-t from-[#101013] to-transparent pointer-events-none" />
              </div>
            </div>
          </div>
        </section>

        {/* ─────────── AI ASSISTANT SECTION ─────────── */}
        <section className="py-32 px-md border-t border-white/[0.05]">
          <div className="container-max">
            <div className="grid lg:grid-cols-[48%_52%] gap-16 items-center">

              {/* Mock AI chat — left */}
              <div className="glass-card rounded-2xl overflow-hidden border-white/[0.07] shadow-2xl relative">
                <div className="flex items-center gap-3 px-5 py-4 border-b border-white/[0.06]">
                  <div className="w-9 h-9 rounded-full bg-[#e8a940]/12 border border-[#e8a940]/20 flex items-center justify-center">
                    <span className="material-symbols-outlined text-[#e8a940] text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }}>
                      smart_toy
                    </span>
                  </div>
                  <div>
                    <h4 className="font-semibold text-[13px] text-white/90">AI Portfolio Assistant</h4>
                    <p className="font-label-caps text-[9px] text-[#2dbd7e] mt-0.5 tracking-[0.14em]">
                      ACTIVE · GEMINI PRO ENHANCED
                    </p>
                  </div>
                </div>

                <div className="p-5 space-y-4">
                  {/* User message */}
                  <div className="flex gap-3">
                    <div className="w-7 h-7 rounded-lg bg-white/[0.06] border border-white/[0.06] flex items-center justify-center shrink-0 text-[10px] font-bold text-white/50">T</div>
                    <div className="bg-white/[0.04] border border-white/[0.05] px-3 py-2.5 rounded-xl rounded-tl-sm max-w-[78%]">
                      <p className="text-[13px] text-white/65">Tôi nên mua VCB hay HPG vào thời điểm này?</p>
                    </div>
                  </div>

                  {/* AI response */}
                  <div className="flex gap-3 flex-row-reverse">
                    <div className="w-7 h-7 rounded-lg bg-[#e8a940]/12 border border-[#e8a940]/20 flex items-center justify-center shrink-0">
                      <span className="material-symbols-outlined text-[#e8a940] text-[14px]" style={{ fontVariationSettings: "'FILL' 1" }}>smart_toy</span>
                    </div>
                    <div className="bg-[#e8a940]/5 border border-[#e8a940]/15 px-3 py-2.5 rounded-xl rounded-tr-sm max-w-[85%]">
                      <p className="text-[13px] text-white/80 leading-relaxed">
                        Dựa trên dữ liệu HOSE hôm nay,{' '}
                        <span className="text-[#2dbd7e] font-bold">VCB</span> đang giữ nền giá tốt tại 91.5 với hỗ trợ mạnh.{' '}
                        <span className="text-[#f87171] font-bold">HPG</span> chịu áp lực bán từ khối ngoại. AI đề xuất giải ngân 30% vào VCB để phòng thủ.
                      </p>
                      <div className="mt-3 pt-3 border-t border-white/[0.06] grid grid-cols-2 gap-2">
                        <div className="bg-black/25 px-2.5 py-1.5 rounded-lg border border-white/[0.05]">
                          <span className="block font-label-caps text-[9px] text-white/35 mb-0.5">VCB SIGNAL</span>
                          <span className="font-data-mono text-[11px] font-bold text-[#2dbd7e]">Strong Buy</span>
                        </div>
                        <div className="bg-black/25 px-2.5 py-1.5 rounded-lg border border-white/[0.05]">
                          <span className="block font-label-caps text-[9px] text-white/35 mb-0.5">HPG SIGNAL</span>
                          <span className="font-data-mono text-[11px] font-bold text-[#7bbcee]">Neutral</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Input */}
                  <div className="flex items-center gap-2 pt-1 border-t border-white/[0.05]">
                    <input
                      readOnly
                      className="flex-1 bg-white/[0.03] border border-white/[0.07] rounded-xl px-3 py-2 text-[13px] text-white/40 outline-none cursor-pointer"
                      placeholder="Hỏi AI về mã cổ phiếu..."
                    />
                    <button className="w-9 h-9 rounded-xl bg-[#e8a940] flex items-center justify-center text-[#1a0d00] btn-press hover:bg-[#f5c46e] transition-colors shrink-0">
                      <span className="material-symbols-outlined text-[16px]">send</span>
                    </button>
                  </div>
                </div>
              </div>

              {/* Right: feature highlights */}
              <div className="space-y-4">
                <div>
                  <p className="font-label-caps text-[#e8a940] text-[9px] tracking-[0.18em] mb-3">TRỢ LÝ AI CÁ NHÂN HÓA</p>
                  <h2 className="text-[2.4rem] font-extrabold tracking-tight leading-[1.1] mb-4">
                    Phân tích sâu. Quyết định nhanh.
                  </h2>
                  <p className="text-[#95949c] text-[15px] leading-relaxed max-w-[48ch]">
                    AI theo dõi hàng nghìn biến động giá mỗi giây trên HOSE, HNX, UPCOM và đưa ra nhận định chính xác tức thì.
                  </p>
                </div>

                <div className="space-y-3 pt-2">
                  {[
                    { icon: 'bolt',           title: 'Phân tích real-time',   desc: 'Tín hiệu mua/bán cập nhật liên tục trong phiên giao dịch.' },
                    { icon: 'shield',          title: 'Cảnh báo rủi ro',       desc: 'Phát hiện tự động đảo chiều và áp lực bán đột biến.' },
                    { icon: 'calculate',       title: 'Tối ưu hóa chi phí',    desc: 'Tính thuế TNCN và phí giao dịch tự động cho mọi lệnh.' },
                    { icon: 'psychology_alt',  title: 'Sentiment Analysis',    desc: 'Đọc tâm lý thị trường từ 50,000+ nguồn tin tức và diễn đàn.' },
                  ].map((item) => (
                    <div key={item.title} className="flex gap-3 p-4 rounded-xl border border-white/[0.05] hover:border-[#e8a940]/15 hover:bg-[#e8a940]/[0.03] transition-all duration-200 group">
                      <div className="w-9 h-9 rounded-lg bg-[#e8a940]/8 border border-[#e8a940]/15 flex items-center justify-center shrink-0 group-hover:bg-[#e8a940]/12 transition-colors">
                        <span className="material-symbols-outlined text-[#e8a940] text-[18px]">{item.icon}</span>
                      </div>
                      <div>
                        <h5 className="font-semibold text-[13px] text-white/85">{item.title}</h5>
                        <p className="text-[12px] text-[#95949c] mt-0.5 leading-relaxed">{item.desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ─────────── FEATURES BENTO GRID ─────────── */}
        <section className="py-32 px-md border-t border-white/[0.05] bg-[#060608]/60">
          <div className="container-max">
            <div className="flex flex-col md:flex-row md:items-end justify-between mb-14 gap-4">
              <div>
                <p className="font-label-caps text-[#e8a940] text-[9px] tracking-[0.18em] mb-3">TÍNH NĂNG TOÀN DIỆN</p>
                <h2 className="text-[2.4rem] font-extrabold tracking-tight leading-[1.1]">
                  Mọi công cụ bạn cần.<br />
                  <span className="text-[#95949c] font-medium">Trong một nền tảng.</span>
                </h2>
              </div>
              <Link href="/dashboard" className="flex items-center gap-1.5 text-[#e8a940] text-[13px] font-semibold hover:gap-2.5 transition-all duration-200 group">
                Khám phá toàn bộ
                <span className="material-symbols-outlined text-[18px] group-hover:translate-x-0.5 transition-transform">arrow_forward</span>
              </Link>
            </div>

            {/* Asymmetric bento grid — not 3-equal-columns */}
            <div className="grid grid-cols-1 md:grid-cols-12 grid-rows-[auto] gap-4">

              {/* Large feature: 7 cols, 2 rows */}
              <div className="md:col-span-7 glass-card rounded-2xl overflow-hidden border-white/[0.06] group hover:border-[#e8a940]/12 transition-all duration-300 relative">
                {/* Chart preview */}
                <div className="absolute inset-0 opacity-20">
                  <svg viewBox="0 0 700 300" className="w-full h-full" preserveAspectRatio="xMidYMid slice">
                    <defs>
                      <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#e8a940" stopOpacity="0.3" />
                        <stop offset="100%" stopColor="#e8a940" stopOpacity="0" />
                      </linearGradient>
                    </defs>
                    <path
                      d="M0,200 C80,180 120,160 180,140 C240,120 280,130 340,100 C400,70 440,90 500,60 C560,30 620,50 700,20 L700,300 L0,300 Z"
                      fill="url(#chartFill)"
                    />
                    <path
                      d="M0,200 C80,180 120,160 180,140 C240,120 280,130 340,100 C400,70 440,90 500,60 C560,30 620,50 700,20"
                      fill="none"
                      stroke="#e8a940"
                      strokeWidth="2"
                    />
                  </svg>
                </div>
                <div className="relative p-8 h-full flex flex-col justify-between min-h-[260px]">
                  <div className="flex items-start justify-between">
                    <div className="w-11 h-11 rounded-xl bg-[#e8a940]/10 border border-[#e8a940]/20 flex items-center justify-center">
                      <span className="material-symbols-outlined text-[#e8a940] text-[22px]">analytics</span>
                    </div>
                    <span className="font-label-caps text-[#e8a940] text-[9px] tracking-[0.16em] px-2.5 py-1 bg-[#e8a940]/8 rounded-full border border-[#e8a940]/15">
                      AI SCORING
                    </span>
                  </div>
                  <div>
                    <h3 className="text-[22px] font-bold tracking-tight mb-2 text-white/90">
                      Chấm điểm cổ phiếu tự động theo CANSLIM & VSA
                    </h3>
                    <p className="text-[#95949c] text-[14px] leading-relaxed max-w-[50ch]">
                      AI phân tích 700+ mã theo 47 tiêu chí kỹ thuật và cơ bản mỗi phiên. Điểm số cập nhật theo thời gian thực.
                    </p>
                  </div>
                </div>
              </div>

              {/* Small feature: 5 cols */}
              <div className="md:col-span-5 glass-card rounded-2xl p-6 border-white/[0.06] group hover:border-[#2dbd7e]/12 transition-all duration-300 relative overflow-hidden">
                <div className="absolute top-0 right-0 w-32 h-32 bg-[#2dbd7e]/5 blur-2xl rounded-full -translate-y-1/2 translate-x-1/2" />
                <div className="relative">
                  <div className="w-10 h-10 rounded-xl bg-[#2dbd7e]/8 border border-[#2dbd7e]/15 flex items-center justify-center mb-4">
                    <span className="material-symbols-outlined text-[#2dbd7e] text-[20px]">rocket_launch</span>
                  </div>
                  <h3 className="text-[18px] font-bold tracking-tight mb-2 text-white/90">Auto-Pilot</h3>
                  <p className="text-[#95949c] text-[13px] leading-relaxed">
                    Thiết lập điều kiện mua/bán tự động. AI theo dõi 24/7 không bỏ lỡ cơ hội.
                  </p>
                  <div className="mt-5 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-[#2dbd7e] animate-pulse-dot" />
                    <span className="font-label-caps text-[#2dbd7e] text-[9px] tracking-[0.14em]">3 chiến lược đang chạy</span>
                  </div>
                </div>
              </div>

              {/* Bottom row: 3 equal-ish slots but with variety */}
              <div className="md:col-span-4 glass-card rounded-2xl p-6 border-white/[0.06] group hover:border-[#7bbcee]/12 transition-all duration-300">
                <div className="w-10 h-10 rounded-xl bg-[#7bbcee]/8 border border-[#7bbcee]/15 flex items-center justify-center mb-4">
                  <span className="material-symbols-outlined text-[#7bbcee] text-[20px]">candlestick_chart</span>
                </div>
                <h3 className="text-[16px] font-bold tracking-tight mb-1.5 text-white/90">Biểu đồ kỹ thuật</h3>
                <p className="text-[#95949c] text-[13px] leading-relaxed">
                  TradingView với chỉ số AI độc quyền, sóng Elliott, Fibonacci real-time.
                </p>
              </div>

              <div className="md:col-span-4 glass-card rounded-2xl p-6 border-white/[0.06] group hover:border-[#e8a940]/12 transition-all duration-300">
                <div className="w-10 h-10 rounded-xl bg-[#e8a940]/8 border border-[#e8a940]/20 flex items-center justify-center mb-4">
                  <span className="material-symbols-outlined text-[#e8a940] text-[20px]">groups</span>
                </div>
                <h3 className="text-[16px] font-bold tracking-tight mb-1.5 text-white/90">Cộng đồng VIP</h3>
                <p className="text-[#95949c] text-[13px] leading-relaxed">
                  Copy lệnh từ top traders. Thảo luận real-time với nhà đầu tư chuyên nghiệp.
                </p>
              </div>

              <div className="md:col-span-4 glass-card rounded-2xl p-6 border-white/[0.06] group hover:border-[#2dbd7e]/12 transition-all duration-300">
                <div className="w-10 h-10 rounded-xl bg-[#2dbd7e]/8 border border-[#2dbd7e]/15 flex items-center justify-center mb-4">
                  <span className="material-symbols-outlined text-[#2dbd7e] text-[20px]">account_balance_wallet</span>
                </div>
                <h3 className="text-[16px] font-bold tracking-tight mb-1.5 text-white/90">Quản lý danh mục</h3>
                <p className="text-[#95949c] text-[13px] leading-relaxed">
                  Tổng hợp tài sản từ nhiều tài khoản, phân tích rủi ro và phân bổ tối ưu.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ─────────── CTA SECTION ─────────── */}
        <section className="py-32 px-md border-t border-white/[0.05]">
          <div className="container-max">
            <div className="relative glass-card rounded-3xl p-12 md:p-16 text-center overflow-hidden border-white/[0.07]">
              {/* Ambient glow */}
              <div className="absolute inset-0 pointer-events-none">
                <div className="absolute top-1/2 left-1/2 w-[500px] h-[500px] bg-[#e8a940]/6 blur-[120px] rounded-full -translate-x-1/2 -translate-y-1/2" />
              </div>
              <div className="relative z-10">
                <p className="font-label-caps text-[#e8a940] text-[9px] tracking-[0.18em] mb-4">BẮT ĐẦU NGAY HÔM NAY</p>
                <h2 className="text-[2.8rem] font-extrabold tracking-tight leading-[1.05] mb-4">
                  Sẵn sàng đầu tư<br />thông minh hơn?
                </h2>
                <p className="text-[#95949c] text-[15px] max-w-[46ch] mx-auto mb-8 leading-relaxed">
                  Tham gia cùng hơn 847 nghìn nhà đầu tư đang sử dụng AIInvest để đưa ra quyết định dựa trên dữ liệu, không cảm xúc.
                </p>
                <div className="flex flex-wrap items-center justify-center gap-3">
                  <Link
                    href="/auth"
                    className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl bg-[#e8a940] text-[#1a0d00] font-bold text-[15px] hover:bg-[#f5c46e] transition-all duration-200 btn-press shadow-primary-tint amber-glow"
                  >
                    Đăng ký miễn phí
                    <span className="material-symbols-outlined text-[18px]">arrow_forward</span>
                  </Link>
                  <Link
                    href="/dashboard"
                    className="inline-flex items-center gap-2 px-8 py-3.5 rounded-xl glass-card text-white/70 font-semibold text-[15px] hover:text-white hover:bg-white/[0.07] transition-all duration-200"
                  >
                    Xem Demo trực tiếp
                  </Link>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* ── Footer ── */}
      <footer className="border-t border-white/[0.05] bg-[#060608] py-16 px-md">
        <div className="container-max">
          <div className="grid grid-cols-1 md:grid-cols-[2fr_1fr_1fr_1fr] gap-12 mb-12">

            <div className="space-y-4">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-lg aura-glow flex items-center justify-center">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" className="text-[#e8a940]">
                    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" fill="currentColor" />
                  </svg>
                </div>
                <span className="font-bold text-[15px] tracking-tight text-white/90">AIInvest</span>
              </div>
              <p className="text-[13px] text-[#95949c] leading-relaxed max-w-[32ch]">
                Tiên phong công nghệ AI trong lĩnh vực đầu tư tài chính tại Việt Nam.
              </p>
              <div className="flex gap-2">
                {['facebook', 'smart_display', 'alternate_email'].map((icon) => (
                  <a
                    key={icon}
                    href="#"
                    className="w-8 h-8 glass-card rounded-lg flex items-center justify-center text-[#95949c] hover:text-[#e8a940] transition-colors"
                  >
                    <span className="material-symbols-outlined text-[16px]">{icon}</span>
                  </a>
                ))}
              </div>
            </div>

            {[
              {
                title: 'Sản phẩm',
                links: ['Giao dịch cơ sở', 'Chứng khoán phái sinh', 'AI Copilot', 'Mô phỏng đầu tư'],
              },
              {
                title: 'Hỗ trợ',
                links: ['Trung tâm hỗ trợ', 'Biểu phí giao dịch', 'Mở tài khoản', 'Chính sách bảo mật'],
              },
              {
                title: 'Pháp lý',
                links: ['Điều khoản sử dụng', 'Chính sách cookie', 'Cảnh báo rủi ro', 'Liên hệ'],
              },
            ].map((col) => (
              <div key={col.title} className="space-y-3">
                <h5 className="font-semibold text-[13px] text-white/70">{col.title}</h5>
                <ul className="space-y-2">
                  {col.links.map((link) => (
                    <li key={link}>
                      <a href="#" className="text-[13px] text-[#95949c] hover:text-[#e8a940] transition-colors duration-200">
                        {link}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="pt-8 border-t border-white/[0.05] flex flex-col md:flex-row justify-between items-center gap-3 opacity-50">
            <p className="font-label-caps text-[10px] tracking-widest">© 2024 AIInvest. All Rights Reserved.</p>
            <p className="text-[11px] text-center max-w-[60ch] text-[#95949c]">
              Cảnh báo: Đầu tư chứng khoán luôn tiềm ẩn rủi ro. Nhận định từ AI chỉ mang tính chất tham khảo.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
