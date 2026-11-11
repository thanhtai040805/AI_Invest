/* ============================================================================
 * TradingView Complete Catalog (Free + Essential + Plus + Pro + Premium + Ultimate)
 * Extracted via REA MCP from pine-facade (standard, candlestick, fundamental,
 * editors_picks, script-store) and @tv-* native studies (287 Total Unlocked Items)
 * ============================================================================ */

import type {
  TvDialogSidebarSection,
  TvFundamentalSubTab,
  TvIndicatorCategory,
  TvIndicatorMetadata,
  TvIndicatorParamDef,
  TvTechnicalSubTab,
  TvTierBadge,
} from "../types"

const P_LEN = (def = 14, label = "Chu kỳ (Length)", max = 500): TvIndicatorParamDef[] => [
  { key: "length", label, defaultValue: def, min: 1, max },
]

const P_FAST_SLOW = (fast = 12, slow = 26): TvIndicatorParamDef[] => [
  { key: "fast", label: "Chu kỳ nhanh (Fast)", defaultValue: fast, min: 1, max: 200 },
  { key: "slow", label: "Chu kỳ chậm (Slow)", defaultValue: slow, min: 2, max: 400 },
]

function makeStudy(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  category: TvIndicatorCategory,
  overlay: boolean,
  defaultColor: string,
  params: TvIndicatorParamDef[] = P_LEN(14),
  extra: Partial<TvIndicatorMetadata> = {}
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category,
    section: "technicals",
    subTab: "indicators",
    overlay,
    defaultColor,
    params,
    ...extra,
  }
}

function makeStrategy(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  tvScriptId: string,
  params: TvIndicatorParamDef[] = P_LEN(14),
  badge?: TvTierBadge
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "strategy",
    section: "technicals",
    subTab: "strategies",
    tvScriptId,
    badge,
    overlay: true,
    defaultColor: "#2962FF",
    secondaryColor: "#089981",
    tertiaryColor: "#F23645",
    params,
  }
}

function makeProfile(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  tvScriptId: string,
  badge: TvTierBadge,
  rowsDefault = 24,
  vaPct = 70
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "profile",
    section: "technicals",
    subTab: "profiles",
    tvScriptId,
    badge,
    overlay: true,
    defaultColor: "#2962FF",
    secondaryColor: "#F59E0B",
    params: [
      { key: "rows", label: "Số bậc giá (Row Size)", defaultValue: rowsDefault, min: 8, max: 80 },
      { key: "vaPct", label: "Vùng giá trị VA (%)", defaultValue: vaPct, min: 50, max: 95 },
    ],
  }
}

function makeChartPattern(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  badge: TvTierBadge = "ULTIMATE"
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "pattern",
    section: "technicals",
    subTab: "patterns",
    tvScriptId: `@tv-chart_patterns:${id}`,
    badge,
    overlay: true,
    defaultColor: "#2962FF",
    secondaryColor: "#089981",
    params: [{ key: "pivotLen", label: "Độ nhạy đỉnh/đáy (Pivot)", defaultValue: 5, min: 2, max: 20 }],
  }
}

function makeCandlePattern(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  bias: "bull" | "bear" | "neutral" | "all",
  tvScriptId: string
): TvIndicatorMetadata {
  const color = bias === "bull" ? "#089981" : bias === "bear" ? "#F23645" : "#2962FF"
  return {
    id,
    name,
    shortName,
    viName,
    description: `Tự động quét và gắn nhãn mẫu nến Nhật "${name}" trực tiếp trên biểu đồ.`,
    category: "candlestick",
    section: "technicals",
    subTab: "patterns",
    tvScriptId,
    overlay: true,
    defaultColor: color,
    params: [{ key: "trendLen", label: "Chu kỳ lọc xu hướng EMA", defaultValue: 14, min: 2, max: 100 }],
  }
}

function makeOptionStudy(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  overlay: boolean,
  defaultColor: string,
  badge: TvTierBadge = "ULTIMATE",
  params: TvIndicatorParamDef[] = P_LEN(20)
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "options",
    section: "technicals",
    subTab: "options",
    tvScriptId: `@tv-optionstudies:${id}`,
    badge,
    overlay,
    defaultColor,
    params,
  }
}

function makeFundamental(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  fundamentalGroup: TvFundamentalSubTab,
  defaultColor: string
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "fundamental",
    section: "fundamentals",
    fundamentalGroup,
    tvScriptId: `STD;Fund_${id}`,
    badge: "PRO",
    overlay: false,
    defaultColor,
    params: [{ key: "smooth", label: "Chu kỳ làm mượt (Quý/Phiên)", defaultValue: 4, min: 1, max: 30 }],
  }
}

function makeCommunityScript(
  id: string,
  name: string,
  shortName: string,
  viName: string,
  description: string,
  section: TvDialogSidebarSection,
  author: string,
  likes: number,
  overlay: boolean,
  defaultColor: string,
  badge?: TvTierBadge,
  params: TvIndicatorParamDef[] = P_LEN(14),
  extra: Partial<TvIndicatorMetadata> = {}
): TvIndicatorMetadata {
  return {
    id,
    name,
    shortName,
    viName,
    description,
    category: "community",
    section,
    author,
    likes,
    badge,
    overlay,
    defaultColor,
    params,
    ...extra,
  }
}

/* ----------------------------------------------------------------------------
 * 1. BUILT-IN TECHNICAL INDICATORS (120 Official Studies — Exact A–Z Order)
 *    Preserves all 25 original IDs (SMA, EMA, WMA, BB, ICHIMOKU, VWAP, PSAR,
 *    SUPERTREND, DONCHIAN, KELTNER, ENVELOPE, PIVOT, VOL, RSI, MACD, STOCH,
 *    STOCH_RSI, ADX, ATR, CCI, OBV, MFI, WILLR, MOM, ROC)
 * ---------------------------------------------------------------------------- */
export const BUILTIN_TECHNICAL_STUDIES: TvIndicatorMetadata[] = [
  makeStudy("VOL_24H", "24-hour volume", "24h Vol", "Khối lượng giao dịch 24 giờ", "Tổng khối lượng khớp lệnh cuộn theo chu kỳ 24 phiên/giờ quy đổi giá trị.", "volume", false, "#26A69A", P_LEN(24, "Chu kỳ cuộn")),
  makeStudy("ADL", "Accumulation/distribution", "A/D", "Đường Tích lũy / Phân phối (A/D)", "Đo lường dòng tiền tích lũy hay phân phối dựa trên vị trí giá đóng cửa trong biên độ cao–thấp và khối lượng.", "volume", false, "#00897B", P_LEN(14, "EMA tín hiệu")),
  makeStudy("ADV_DEC_LINE", "Advance-decline line", "ADL Breadth", "Đường Tăng – Giảm độ rộng thị trường", "Tích lũy độ rộng xung lượng tăng/giảm giúp xác nhận sức mạnh xu hướng chung.", "trend", false, "#2962FF", P_LEN(14)),
  makeStudy("ADV_DEC_RATIO", "Advance-decline ratio", "ADR Ratio", "Tỷ lệ Tăng / Giảm (Advance-Decline Ratio)", "Tỷ số giữa áp lực nến tăng và áp lực nến giảm trong N phiên.", "oscillator", false, "#7E57C2", P_LEN(14), { referenceLevels: [{ value: 1, color: "#787B86", label: "1.0", dashed: true }] }),
  makeStudy("ADV_DEC_BARS", "Advance-decline ratio (bars)", "ADR Bars", "Tỷ lệ số nến Tăng / Giảm (Bars)", "Đếm tỷ lệ số phiên đóng cửa xanh so với số phiên đóng cửa đỏ trong cửa sổ N phiên.", "oscillator", false, "#00ACC1", P_LEN(14), { referenceLevels: [{ value: 1, color: "#787B86", label: "1.0", dashed: true }] }),
  makeStudy("ALMA", "Arnaud Legoux moving average", "ALMA", "Đường trung bình Arnaud Legoux (ALMA)", "Trung bình động bộ lọc Gaussian giảm độ trễ tối đa mà vẫn triệt tiêu nhiễu.", "trend", true, "#00BFA5", [
    { key: "length", label: "Chu kỳ (Length)", defaultValue: 9, min: 2, max: 300 },
    { key: "offset", label: "Hệ số Offset", defaultValue: 0.85, min: 0.1, max: 0.99, step: 0.05 },
    { key: "sigma", label: "Hệ số Sigma", defaultValue: 6, min: 1, max: 20 },
  ], { allowMultiple: true }),
  makeStudy("AROON", "Aroon", "Aroon", "Chỉ báo Aroon Up & Down", "Xác định xu hướng mới hình thành qua thời gian kể từ đỉnh cao nhất và đáy thấp nhất gần nhất.", "trend", false, "#FF6D00", P_LEN(14), { secondaryColor: "#2962FF", fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70", dashed: true }, { value: 30, color: "#787B86", label: "30", dashed: true }] }),
  makeStudy("AROON_OSC", "Aroon oscillator", "Aroon Osc", "Dao động Aroon (Aroon Oscillator)", "Hiệu số giữa Aroon Up và Aroon Down dao động từ -100 đến +100.", "oscillator", false, "#8E24AA", P_LEN(14), { fixedRange: [-100, 100], referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("AUTO_FIB_EXT", "Auto fib extension", "Auto Fib Ext", "Tự động vẽ Fibonacci Mở rộng (Auto Fib Extension)", "Tự động tìm sóng Swing A–B–C và vẽ các ngưỡng mở rộng 0.618, 1.0, 1.272, 1.618, 2.618.", "trend", true, "#2962FF", [{ key: "depth", label: "Độ sâu Swing (Depth)", defaultValue: 10, min: 3, max: 50 }], { badge: "PRO" }),
  makeStudy("AUTO_FIB_RET", "Auto fib retracement", "Auto Fib", "Tự động vẽ Fibonacci Thoái lui (Auto Fib Retracement)", "Tự động xác định đỉnh/đáy sóng gần nhất và kẻ dải Fibonacci 0.236, 0.382, 0.5, 0.618, 0.786.", "trend", true, "#00897B", [{ key: "depth", label: "Độ sâu Swing (Depth)", defaultValue: 12, min: 3, max: 60 }], { badge: "PRO" }),
  makeStudy("AUTO_KEY_LEVELS", "Auto key levels", "Key Levels", "Tự động xác định Cản Hỗ trợ / Kháng cự", "Thuật toán gom cụm các vùng đỉnh/đáy thanh khoản cao thành các ngưỡng Key Levels tự động.", "trend", true, "#7E57C2", [{ key: "lookback", label: "Số nến quét", defaultValue: 120, min: 30, max: 250 }], { badge: "BETA" }),
  makeStudy("AUTO_PITCHFORK", "Auto pitchfork", "Auto Pitchfork", "Tự động vẽ Kênh Andrews' Pitchfork", "Tự động bắt 3 điểm xoay P0–P1–P2 gần nhất và phóng kênh trung tuyến Pitchfork.", "trend", true, "#E91E63", [{ key: "depth", label: "Độ sâu Pivot", defaultValue: 8, min: 3, max: 40 }], { badge: "PRO" }),
  makeStudy("AUTO_TRENDLINES", "Auto trendlines", "Auto Trend", "Tự động vẽ Đường xu hướng (Auto Trendlines)", "Tự động nối các đỉnh giảm dần và các đáy nâng dần kèm cảnh báo điểm phá vỡ (Breakout).", "trend", true, "#2962FF", [{ key: "depth", label: "Độ nhạy Swing", defaultValue: 6, min: 2, max: 30 }], { badge: "BETA" }),
  makeStudy("ADR", "Average daily range", "ADR", "Biên độ dao động ngày trung bình (ADR)", "Trung bình hiệu số (High - Low) qua N phiên giao dịch.", "volatility", false, "#FB8C00", P_LEN(14)),
  makeStudy("ADX", "Average directional index", "ADX", "Chỉ số định hướng trung bình (ADX)", "Đo lường cường độ mạnh/yếu của xu hướng hiện tại kèm +DI và -DI.", "trend", false, "#F50057", P_LEN(14, "Chu kỳ ADX/DI"), { secondaryColor: "#089981", tertiaryColor: "#F23645", fixedRange: [0, 100], referenceLevels: [{ value: 25, color: "#787B86", label: "25", dashed: true }] }),
  makeStudy("ATR", "Average true range", "ATR", "Biên độ dao động thực tế trung bình (ATR)", "Đo mức độ biến động tuyệt đối (VND) của cổ phiếu qua từng phiên.", "volatility", false, "#B71C1C", P_LEN(14)),
  makeStudy("AO", "Awesome oscillator", "AO", "Dao động Tuyệt vời Bill Williams (AO)", "Histogram động lượng tính bằng hiệu SMA(5) và SMA(34) của giá trung bình HL2.", "oscillator", false, "#089981", P_FAST_SLOW(5, 34), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("BOP", "Balance of power", "BOP", "Cán cân sức mạnh Mua/Bán (Balance of Power)", "Đo khả năng phe Mua hay phe Bán đẩy giá về sát mức cực đại trong phiên: (Close - Open) / (High - Low).", "oscillator", false, "#EC407A", P_LEN(14, "Làm mượt SMA"), { fixedRange: [-1, 1], referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("BBTREND", "BBTrend", "BBTrend", "Chỉ báo xu hướng BBTrend (John Bollinger)", "Đo lường sức mạnh và hướng của xu hướng qua độ chênh hai dải Bollinger ngắn (20) và dài (50).", "trend", false, "#26A69A", P_FAST_SLOW(20, 50), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("BB", "Bollinger Bands", "BB", "Dải Bollinger (Bollinger Bands)", "Đo lường biến động giá với trục SMA trung tâm và 2 biên độ lệch chuẩn.", "volatility", true, "#2962FF", [
    { key: "length", label: "Chu kỳ (Length)", defaultValue: 20, min: 2, max: 200 },
    { key: "mult", label: "Độ lệch chuẩn (StdDev)", defaultValue: 2, min: 0.5, max: 5, step: 0.1 },
  ], { secondaryColor: "#089981" }),
  makeStudy("BB_PCT_B", "Bollinger Bands %b", "%B", "Chỉ báo Bollinger Bands %B", "Xác định vị trí tương đối của giá đóng cửa so với biên dưới (0) và biên trên (1) của dải Bollinger.", "volatility", false, "#2962FF", [
    { key: "length", label: "Chu kỳ", defaultValue: 20, min: 2, max: 200 },
    { key: "mult", label: "StdDev", defaultValue: 2, min: 0.5, max: 5, step: 0.1 },
  ], { referenceLevels: [{ value: 1, color: "#787B86", label: "1.0", dashed: true }, { value: 0.5, color: "#787B8655", label: "0.5", dashed: true }, { value: 0, color: "#787B86", label: "0.0", dashed: true }] }),
  makeStudy("BB_WIDTH", "Bollinger BandWidth", "BBW", "Độ rộng dải Bollinger (BandWidth)", "Phát hiện vùng nén chặt biến động (Bollinger Squeeze) trước khi bùng nổ xu hướng lớn.", "volatility", false, "#AB47BC", [
    { key: "length", label: "Chu kỳ", defaultValue: 20, min: 2, max: 200 },
    { key: "mult", label: "StdDev", defaultValue: 2, min: 0.5, max: 5, step: 0.1 },
  ]),
  makeStudy("BB_BARS", "Bollinger Bars", "BB Bars", "Dải thanh nến Bollinger", "Tô nhấn xung lượng giá vượt biên biến động chuẩn của Bollinger.", "volatility", true, "#42A5F5", P_LEN(20)),
  makeStudy("BULL_BEAR_POWER", "Bull bear power", "BBP", "Sức mạnh Bò & Gấu (Elder-Ray Bull Bear Power)", "Tổng lực đẩy phe Mua (High - EMA) và phe Bán (Low - EMA) của tiến sĩ Alexander Elder.", "oscillator", false, "#089981", P_LEN(13), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("CMF", "Chaikin money flow", "CMF", "Dòng tiền Chaikin (Chaikin Money Flow)", "Trung bình dòng tiền tích lũy/phân phối có trọng số khối lượng trong N phiên.", "volume", false, "#43A047", P_LEN(20), { fixedRange: [-0.6, 0.6], referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("CHAIKIN_OSC", "Chaikin oscillator", "Chaikin Osc", "Dao động dòng tiền Chaikin", "Hiệu số EMA nhanh (3) và EMA chậm (10) của đường Tích lũy/Phân phối A/D.", "volume", false, "#00897B", P_FAST_SLOW(3, 10), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("CHANDE_KROLL", "Chande Kroll stop", "CK Stop", "Điểm dừng lỗ Chande Kroll Stop", "Hệ thống hai đường dừng lỗ động cho vị thế Mua (Long Stop) và Bán (Short Stop) dựa trên ATR.", "volatility", true, "#FF6D00", [
    { key: "length", label: "Chu kỳ ATR", defaultValue: 10, min: 2, max: 100 },
    { key: "mult", label: "Hệ số ATR", defaultValue: 1, min: 0.5, max: 5, step: 0.5 },
  ], { secondaryColor: "#2962FF" }),
  makeStudy("CHANDELIER_EXIT", "Chandelier exit", "CE", "Điểm thoát lệnh Chandelier Exit", "Trailing Stop bám theo đỉnh cao nhất trừ bội số ATR giúp giữ trọn sóng tăng lớn.", "trend", true, "#089981", [
    { key: "length", label: "Chu kỳ ATR", defaultValue: 22, min: 2, max: 100 },
    { key: "mult", label: "Hệ số ATR", defaultValue: 3, min: 0.5, max: 8, step: 0.5 },
  ], { secondaryColor: "#F23645" }),
  makeStudy("CMO", "Chande momentum oscillator", "CMO", "Dao động động lượng Chande (CMO)", "Đo động lượng thuần không làm mượt trên thang -100 đến +100.", "oscillator", false, "#2962FF", P_LEN(9), { fixedRange: [-100, 100], referenceLevels: [{ value: 50, color: "#787B86", label: "+50", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -50, color: "#787B86", label: "-50", dashed: true }] }),
  makeStudy("CHOP", "Choppiness index", "CHOP", "Chỉ số đi ngang Choppiness Index", "Phân biệt thị trường đang có xu hướng mạnh (< 38.2) hay đang đi ngang tích lũy (> 61.8).", "volatility", false, "#2962FF", P_LEN(14), { fixedRange: [0, 100], referenceLevels: [{ value: 61.8, color: "#787B86", label: "61.8", dashed: true }, { value: 38.2, color: "#787B86", label: "38.2", dashed: true }] }),
  makeStudy("CHOP_ZONE", "Chop zone", "Chop Zone", "Vùng nhiễu xu hướng Chop Zone", "Biểu đồ cột màu sắc thể hiện góc nghiêng đường EMA34 nhằm tránh giao dịch vùng giằng co.", "trend", false, "#26A69A", P_LEN(34)),
  makeStudy("CCI", "Commodity channel index", "CCI", "Chỉ số kênh hàng hóa (CCI)", "Đo độ lệch của giá so với mức trung bình thống kê (+100 / -100).", "oscillator", false, "#00897B", P_LEN(20), { referenceLevels: [{ value: 100, color: "#787B86", label: "+100", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -100, color: "#787B86", label: "-100", dashed: true }] }),
  makeStudy("CONNORS_RSI", "Connors RSI", "CRSI", "Chỉ số Connors RSI (CRSI)", "Kết hợp 3 thành phần: RSI(3), RSI chuỗi phiên tăng/giảm liên tiếp (2) và Bách phân vị ROC(100).", "oscillator", false, "#7E57C2", P_LEN(3, "Chu kỳ RSI ngắn"), { fixedRange: [0, 100], referenceLevels: [{ value: 90, color: "#787B86", label: "90", dashed: true }, { value: 10, color: "#787B86", label: "10", dashed: true }] }),
  makeStudy("COPPOCK", "Coppock curve", "Coppock", "Đường cong Coppock (Coppock Curve)", "Chỉ báo xác nhận đáy dài hạn tính bằng WMA(10) của tổng ROC(14) và ROC(11).", "oscillator", false, "#3949AB", P_LEN(10, "Chu kỳ WMA"), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("CORR_COEFF", "Correlation coefficient", "CC", "Hệ số tương quan xu hướng (Correlation)", "Đo mức độ đồng pha tuyến tính của chuỗi giá trong N phiên (-1 đến +1).", "oscillator", false, "#1E88E5", P_LEN(20), { fixedRange: [-1, 1], referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("CVI", "Cumulative volume index", "CVI", "Chỉ số khối lượng tích lũy ròng (CVI)", "Cộng dồn hiệu khối lượng phiên tăng trừ khối lượng phiên giảm theo thời gian.", "volume", false, "#00ACC1", P_LEN(14)),
  makeStudy("DPO", "Detrended price oscillator", "DPO", "Dao động giá khử xu hướng (DPO)", "Loại bỏ xu hướng dài hạn để làm nổi bật chu kỳ đỉnh–đáy ngắn hạn của giá.", "oscillator", false, "#43A047", P_LEN(21), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("DMI", "Directional movement index", "DMI", "Chỉ số chuyển động định hướng (+DI / -DI / ADX)", "Bộ 3 đường +DI, -DI và ADX của J. Welles Wilder.", "trend", false, "#2962FF", P_LEN(14), { secondaryColor: "#FF6D00", tertiaryColor: "#F50057", fixedRange: [0, 100], referenceLevels: [{ value: 25, color: "#787B86", label: "25", dashed: true }] }),
  makeStudy("DONCHIAN", "Donchian channels", "DC", "Kênh giá Donchian (Donchian Channels)", "Kênh giá cao nhất / thấp nhất trong N phiên giúp phát hiện điểm Breakout.", "volatility", true, "#00ACC1", P_LEN(20)),
  makeStudy("DEMA", "Double exponential moving average", "DEMA", "Trung bình động hàm mũ kép (DEMA)", "Giảm độ trễ so với EMA truyền thống bằng công thức 2*EMA - EMA(EMA).", "trend", true, "#43A047", P_LEN(9), { allowMultiple: true }),
  makeStudy("EOM", "Ease of movement", "EOM", "Chỉ báo dịch chuyển dễ dàng (Ease of Movement)", "Liên hệ biến động giá với khối lượng để đánh giá giá tăng có tốn nhiều thanh khoản hay không.", "volume", false, "#7CB342", P_LEN(14), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("EFI", "Elder's force index", "EFI", "Chỉ số lực Elder (Elder's Force Index)", "Kết hợp hướng giá, biên độ thay đổi và khối lượng giao dịch: EMA((Close - PrevClose) * Volume).", "volume", false, "#F4511E", P_LEN(13), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("ENVELOPE", "Envelope", "Env", "Dải bao trung bình động (MA Envelopes)", "Hai dải biên cách đường SMA một tỷ lệ phần trăm cố định.", "trend", true, "#5C6BC0", [
    { key: "length", label: "Chu kỳ (Length)", defaultValue: 20, min: 2, max: 200 },
    { key: "percent", label: "Biên độ (%)", defaultValue: 5, min: 0.5, max: 25, step: 0.5 },
  ]),
  makeStudy("EMA", "Exponential moving average", "EMA", "Đường trung bình động hàm mũ (EMA)", "Trung bình động đặt trọng số cao hơn cho các phiên gần nhất.", "trend", true, "#FF6D00", P_LEN(20), { allowMultiple: true }),
  makeStudy("FISHER", "Fisher transform", "Fisher", "Biến đổi Fisher (Ehlers Fisher Transform)", "Chuyển đổi giá sang phân phối chuẩn Gaussian giúp điểm đảo chiều cực trị sắc nét.", "oscillator", false, "#2962FF", P_LEN(9), { secondaryColor: "#FF6D00", referenceLevels: [{ value: 1.5, color: "#787B86", label: "1.5", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -1.5, color: "#787B86", label: "-1.5", dashed: true }] }),
  makeStudy("GAPS", "Gaps", "Gaps", "Tự động đánh dấu Khoảng trống giá (Price Gaps)", "Tự động tô vùng Gap Up và Gap Down chưa được lấp trên biểu đồ nến.", "volatility", true, "#AB47BC", [{ key: "minPct", label: "Biên độ Gap tối thiểu (%)", defaultValue: 0.6, min: 0.1, max: 5, step: 0.1 }]),
  makeStudy("HV", "Historical volatility", "HV", "Độ biến động lịch sử năm hóa (Historical Volatility)", "Độ lệch chuẩn tỷ suất sinh lời logarit quy đổi theo năm (%).", "volatility", false, "#2962FF", P_LEN(10)),
  makeStudy("HMA", "Hull moving average", "HMA", "Đường trung bình động Hull (HMA)", "Đường trung bình siêu mượt gần như triệt tiêu hoàn toàn độ trễ của Alan Hull.", "trend", true, "#00BCD4", P_LEN(9), { allowMultiple: true }),
  makeStudy("ICHIMOKU", "Ichimoku cloud", "Ichimoku", "Mây Ichimoku Kinko Hyo", "Hệ thống xác định xu hướng, kháng cự/hỗ trợ và động lượng 5 đường chuẩn Nhật Bản.", "trend", true, "#2962FF", [
    { key: "tenkan", label: "Tenkan-sen (Conversion)", defaultValue: 9, min: 2, max: 120 },
    { key: "kijun", label: "Kijun-sen (Base)", defaultValue: 26, min: 2, max: 240 },
    { key: "senkouB", label: "Senkou Span B", defaultValue: 52, min: 5, max: 360 },
  ], { secondaryColor: "#B71C1C", tertiaryColor: "#43A047" }),
  makeStudy("KAMA", "Kaufman's adaptive moving average", "KAMA", "Trung bình động thích ứng Kaufman (KAMA)", "Tự động bám sát khi giá có sóng mạnh và đi ngang phẳng khi thị trường nhiễu.", "trend", true, "#8E24AA", P_LEN(10), { allowMultiple: true }),
  makeStudy("KELTNER", "Keltner channels", "KC", "Kênh giá Keltner (Keltner Channels)", "Kênh biến động dựa trên đường EMA trung tâm và biên độ ATR.", "volatility", true, "#3949AB", [
    { key: "length", label: "Chu kỳ EMA", defaultValue: 20, min: 2, max: 200 },
    { key: "mult", label: "Hệ số ATR", defaultValue: 2, min: 0.5, max: 5, step: 0.5 },
  ]),
  makeStudy("KLINGER", "Klinger oscillator", "KVO", "Dao động khối lượng Klinger (KVO)", "So sánh dòng tiền vào/ra dài hạn với biến động giá ngắn hạn kèm đường Signal.", "volume", false, "#2962FF", P_FAST_SLOW(34, 55), { secondaryColor: "#43A047", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("KST", "Know sure thing", "KST", "Dao động tổng hợp chu kỳ Know Sure Thing (Martin Pring)", "Tổng trọng số 4 chu kỳ ROC đã làm mượt kèm đường SMA tín hiệu.", "oscillator", false, "#00897B", P_LEN(9, "Chu kỳ Signal"), { secondaryColor: "#F4511E", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("LSMA", "Least squares moving average", "LSMA", "Trung bình động bình phương tối thiểu (Linear Regression MA)", "Dự phóng điểm cuối đường hồi quy tuyến tính qua N phiên gần nhất.", "trend", true, "#FB8C00", P_LEN(25), { allowMultiple: true }),
  makeStudy("LINREG_CHANNEL", "Linear regression channel", "LinReg Ch", "Kênh hồi quy tuyến tính tự động (Linear Regression Channel)", "Tự động vẽ trục hồi quy trung tâm và kênh lệch chuẩn ±2σ trên N phiên gần nhất.", "volatility", true, "#2962FF", [
    { key: "length", label: "Số nến hồi quy", defaultValue: 80, min: 20, max: 250 },
    { key: "mult", label: "Độ lệch chuẩn (±σ)", defaultValue: 2, min: 0.5, max: 4, step: 0.5 },
  ]),
  makeStudy("MASS_INDEX", "Mass index", "Mass", "Chỉ số khối đảo chiều (Mass Index)", "Phát hiện tín hiệu Reversal Bulge khi tỷ số biên độ High-Low vượt ngưỡng 27 rồi cắt xuống 26.5.", "volatility", false, "#3949AB", P_LEN(10), { referenceLevels: [{ value: 27, color: "#F23645", label: "27", dashed: true }, { value: 26.5, color: "#787B86", label: "26.5", dashed: true }] }),
  makeStudy("MCGINLEY", "McGinley dynamic", "McGinley", "Đường động lực McGinley Dynamic", "Tự động điều chỉnh tốc độ theo biến động thị trường để tránh tín hiệu giả Whipsaw.", "trend", true, "#D81B60", P_LEN(14), { allowMultiple: true }),
  makeStudy("MEDIAN", "Median", "Median", "Dải Trung vị giá & ATR (Median)", "Tính trung vị thống kê của HL2 trong N phiên kèm dải băng xu hướng ATR.", "trend", true, "#43A047", P_LEN(14), { secondaryColor: "#AB47BC" }),
  makeStudy("MOM", "Momentum", "Mom", "Động lượng giá (Momentum)", "Chênh lệch giá đóng cửa hiện tại so với N phiên trước đó.", "oscillator", false, "#2962FF", P_LEN(10), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("MFI", "Money flow index", "MFI", "Chỉ số dòng tiền (Money Flow Index)", "RSI có trọng số khối lượng giao dịch (Quá mua > 80, Quá bán < 20).", "volume", false, "#7E57C2", P_LEN(14), { fixedRange: [0, 100], referenceLevels: [{ value: 80, color: "#787B86", label: "80", dashed: true }, { value: 20, color: "#787B86", label: "20", dashed: true }] }),
  makeStudy("MOON_PHASES", "Moon phases", "Moon", "Chu kỳ Mặt Trăng thiên văn (Moon Phases)", "Đánh dấu ngày Trăng Tròn (Full Moon) và Trăng Non (New Moon) theo lịch thiên văn chuẩn.", "trend", true, "#F59E0B", [{ key: "showLabels", label: "Hiển thị nhãn (1=Có)", defaultValue: 1, min: 0, max: 1 }]),
  makeStudy("MACD", "Moving average convergence divergence", "MACD", "Phân kỳ hội tụ trung bình động (MACD)", "Theo dõi động lượng xu hướng qua đường MACD, đường Tín hiệu và Histogram 4 sắc thái.", "oscillator", false, "#2962FF", [
    { key: "fast", label: "Chu kỳ nhanh (Fast)", defaultValue: 12, min: 2, max: 100 },
    { key: "slow", label: "Chu kỳ chậm (Slow)", defaultValue: 26, min: 3, max: 200 },
    { key: "signal", label: "Đường tín hiệu (Signal)", defaultValue: 9, min: 2, max: 100 },
  ], { secondaryColor: "#FF6D00", referenceLevels: [{ value: 0, color: "#787B8688", label: "0", dashed: false }] }),
  makeStudy("MA_CROSS", "Moving average crossover", "MA Cross", "Giao cắt 2 đường Trung bình động (MA Cross)", "Vẽ 2 đường MA nhanh/chậm và tự động gắn điểm Golden Cross / Death Cross.", "trend", true, "#2962FF", P_FAST_SLOW(9, 26), { secondaryColor: "#FF6D00" }),
  makeStudy("MA_RIBBON", "Moving average ribbon", "MA Ribbon", "Dải lụa 4 đường Trung bình động (MA Ribbon)", "Hệ thống 4 đường trung bình động (20, 50, 100, 200) giúp nhận diện cấu trúc xu hướng vĩ mô.", "trend", true, "#2962FF", [
    { key: "ma1", label: "MA 1", defaultValue: 20, min: 2, max: 100 },
    { key: "ma2", label: "MA 2", defaultValue: 50, min: 5, max: 200 },
    { key: "ma3", label: "MA 3", defaultValue: 100, min: 10, max: 300 },
    { key: "ma4", label: "MA 4", defaultValue: 200, min: 20, max: 500 },
  ]),
  makeStudy("MTF_CHARTS", "Multi-time period charts", "MTF Boxes", "Khung nến Đa khung thời gian (Multi-Time Period)", "Đóng khung hộp OHLC của khung thời gian lớn ngay trên biểu đồ hiện tại.", "trend", true, "#00ACC1", [{ key: "groupBars", label: "Gộp số nến / Khung lớn", defaultValue: 5, min: 2, max: 30 }], { badge: "PRO" }),
  makeStudy("NVI", "Negative volume index", "NVI", "Chỉ số khối lượng âm (Negative Volume Index)", "Theo dõi hành vi dòng tiền thông minh trong những phiên thanh khoản sụt giảm.", "volume", false, "#E53935", P_LEN(255, "EMA tín hiệu"), { secondaryColor: "#2962FF" }),
  makeStudy("NET_VOL", "Net volume", "Net Vol", "Khối lượng Mua/Bán chủ động ròng (Net Volume)", " Ước tính chênh lệch khối lượng khớp chủ động Mua (Delta+) và Bán (Delta-) từng phiên.", "volume", false, "#089981", P_LEN(14), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("OBV", "On-balance volume", "OBV", "Khối lượng cân bằng (On-Balance Volume)", "Tích lũy dòng tiền khối lượng theo phiên tăng/giảm để phát hiện gom/xả ngầm.", "volume", false, "#2962FF", [{ key: "maLength", label: "Chu kỳ SMA tín hiệu", defaultValue: 20, min: 2, max: 100 }], { secondaryColor: "#FF6D00" }),
  makeStudy("OPEN_INTEREST", "Open interest", "OI", "Khối lượng Hợp đồng mở ước tính (Open Interest)", "Mô phỏng dòng hợp đồng mở tích lũy theo thanh khoản và biến động giá.", "volume", false, "#8E24AA", P_LEN(14)),
  makeStudy("PSAR", "Parabolic SAR", "SAR", "Parabolic SAR (Điểm dừng & Đảo chiều)", "Xác định điểm đảo chiều xu hướng và đặt lệnh Trailing Stop.", "trend", true, "#00897B", [
    { key: "start", label: "Bước khởi đầu (Start)", defaultValue: 0.02, min: 0.005, max: 0.2, step: 0.01 },
    { key: "max", label: "Tối đa (Max)", defaultValue: 0.2, min: 0.05, max: 0.5, step: 0.05 },
  ]),
  makeStudy("PVO", "Percentage volume oscillator", "PVO", "Dao động phần trăm khối lượng (PVO)", "MACD tính trên khối lượng giao dịch theo đơn vị phần trăm (%).", "volume", false, "#2962FF", [
    { key: "fast", label: "EMA nhanh", defaultValue: 12, min: 2, max: 100 },
    { key: "slow", label: "EMA chậm", defaultValue: 26, min: 3, max: 200 },
    { key: "signal", label: "Signal", defaultValue: 9, min: 2, max: 60 },
  ], { secondaryColor: "#FF6D00", referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("PERFORMANCE", "Performance", "Perf %", "Hiệu suất sinh lời lũy kế (% Performance)", "Tỷ suất lợi nhuận phần trăm lũy kế tính từ đầu chu kỳ quan sát.", "oscillator", false, "#2962FF", P_LEN(60, "Chu kỳ cơ sở"), { referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("PIVOT_HL", "Pivot points high low", "Pivots HL", "Điểm xoay Đỉnh / Đáy Swing (Pivot Points High Low)", "Tự động đánh dấu giá trị các đỉnh Swing High và đáy Swing Low quan trọng trên nến.", "trend", true, "#FB8C00", [{ key: "length", label: "Số nến Left/Right", defaultValue: 7, min: 2, max: 30 }]),
  makeStudy("PIVOT", "Pivot points standard", "Pivots", "Điểm xoay kỹ thuật (Pivot Points P/R1/S1/R2/S2)", "Tự động tính các vùng hỗ trợ (S1, S2) và kháng cự (R1, R2) theo cụm phiên.", "trend", true, "#FB8C00", [{ key: "period", label: "Cụm phiên (Bars)", defaultValue: 15, min: 5, max: 60 }]),
  makeStudy("PVI", "Positive volume index", "PVI", "Chỉ số khối lượng dương (Positive Volume Index)", "Đo lường xu hướng giá trong những phiên khối lượng tăng cao hơn phiên liền trước.", "volume", false, "#089981", P_LEN(255, "EMA tín hiệu"), { secondaryColor: "#FF6D00" }),
  makeStudy("PMO", "Price momentum oscillator", "PMO", "Dao động động lượng giá DecisionPoint (PMO)", "Chỉ báo động lượng làm mượt kép ROC của DecisionPoint kèm đường tín hiệu.", "oscillator", false, "#2962FF", P_FAST_SLOW(35, 20), { secondaryColor: "#F23645", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("PPO", "Price oscillator", "PPO", "Dao động giá phần trăm (Percentage Price Oscillator)", "Đo chênh lệch phần trăm giữa hai đường EMA nhanh và chậm.", "oscillator", false, "#00897B", [
    { key: "fast", label: "EMA nhanh", defaultValue: 12, min: 2, max: 100 },
    { key: "slow", label: "EMA chậm", defaultValue: 26, min: 3, max: 200 },
    { key: "signal", label: "Signal", defaultValue: 9, min: 2, max: 60 },
  ], { secondaryColor: "#FF6D00", referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("PRICE_TARGET", "Price target", "Price Target", "Mục tiêu giá dự phóng 12 tháng (Analyst Price Target)", "Phóng chiếu vùng mục tiêu giá Cao nhất / Trung bình / Thấp nhất 12 tháng tới trên biểu đồ.", "trend", true, "#2962FF", P_LEN(60, "Chu kỳ định giá"), { badge: "ULTIMATE" }),
  makeStudy("PVT", "Price-volume trend", "PVT", "Xu hướng Giá – Khối lượng (Price-Volume Trend)", "Cộng dồn khối lượng nhân với tỷ lệ phần trăm thay đổi giá từng phiên.", "volume", false, "#2962FF", P_LEN(20, "SMA tín hiệu")),
  makeStudy("PRING_K", "Pring's special K", "Special K", "Chỉ báo chu kỳ dài hạn Pring's Special K", "Kết hợp động lượng ngắn hạn, trung hạn và dài hạn trên một đường dao động duy nhất.", "oscillator", false, "#7E57C2", P_LEN(10, "Làm mượt SMA"), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("RCI", "Rank correlation index", "RCI", "Chỉ số tương quan hạng Spearman (RCI)", "Đo tương quan thứ bậc giữa thời gian và mức giá (-100 đến +100), cực phổ biến tại Nhật Bản.", "oscillator", false, "#2962FF", P_LEN(9), { fixedRange: [-100, 100], referenceLevels: [{ value: 80, color: "#787B86", label: "+80", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -80, color: "#787B86", label: "-80", dashed: true }] }),
  makeStudy("ROC", "Rate of change", "ROC", "Tốc độ thay đổi giá (% ROC)", "Tỷ lệ phần trăm thay đổi của giá so với N phiên trước.", "oscillator", false, "#00897B", P_LEN(9), { referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("RCI_RIBBON", "RCI ribbon", "RCI Ribbon", "Bộ 3 dải tương quan hạng RCI (9, 26, 52)", "Kết hợp 3 chu kỳ RCI ngắn, trung và dài hạn để bắt điểm đồng thuận đảo chiều.", "oscillator", false, "#2962FF", [
    { key: "short", label: "RCI Ngắn", defaultValue: 9, min: 3, max: 50 },
    { key: "mid", label: "RCI Trung", defaultValue: 26, min: 5, max: 100 },
    { key: "long", label: "RCI Dài", defaultValue: 52, min: 10, max: 200 },
  ], { secondaryColor: "#089981", tertiaryColor: "#F23645", fixedRange: [-100, 100], referenceLevels: [{ value: 80, color: "#787B86", label: "80", dashed: true }, { value: -80, color: "#787B86", label: "-80", dashed: true }] }),
  makeStudy("RSI", "Relative strength index", "RSI", "Chỉ số sức mạnh tương đối (RSI)", "Đo tốc độ và biên độ biến động giá (Quá mua > 70, Quá bán < 30).", "oscillator", false, "#7E57C2", [
    { key: "length", label: "Chu kỳ RSI", defaultValue: 14, min: 2, max: 100 },
    { key: "maLength", label: "Chu kỳ SMA làm mượt", defaultValue: 14, min: 2, max: 100 },
  ], { secondaryColor: "#FBC02D", fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70", dashed: true }, { value: 50, color: "#787B8666", label: "50", dashed: true }, { value: 30, color: "#787B86", label: "30", dashed: true }] }),
  makeStudy("RVGI", "Relative vigor index", "RVGI", "Chỉ số sinh lực tương đối (Relative Vigor Index)", "So sánh mức giá đóng cửa so với mở cửa trên tổng biên độ High-Low kèm đường Signal.", "oscillator", false, "#089981", P_LEN(10), { secondaryColor: "#F23645", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("RVI", "Relative volatility index", "RVI", "Chỉ số biến động tương đối (Donald Dorsey RVI)", "Tính công thức RSI nhưng dựa trên Độ lệch chuẩn (StdDev) thay vì thay đổi giá thuần túy.", "volatility", false, "#00ACC1", P_LEN(10), { fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70", dashed: true }, { value: 30, color: "#787B86", label: "30", dashed: true }] }),
  makeStudy("RVOL_TIME", "Relative volume at time", "RVOL", "Khối lượng tương đối đột biến (Relative Volume RVOL)", "Tỷ số khối lượng phiên hiện tại chia cho khối lượng trung bình cùng thời điểm (RVOL > 1.5x = Đột biến).", "volume", false, "#26A69A", P_LEN(20), { referenceLevels: [{ value: 1.5, color: "#F59E0B", label: "1.5x", dashed: true }, { value: 1, color: "#787B86", label: "1.0x", dashed: true }] }),
  makeStudy("GHOST_PIVOTS", "Rob Booker — Ziv ghost pivots", "Ghost Pivots", "Điểm xoay ẩn Rob Booker Ziv Ghost Pivots", "Các ngưỡng Pivot định chế ẩn đóng vai trò nam châm hút giá trong tuần.", "trend", true, "#9C27B0", [{ key: "period", label: "Cụm phiên", defaultValue: 10, min: 3, max: 50 }]),
  makeStudy("RSI_DIVERGENCE", "RSI divergence indicator", "RSI Div", "Chỉ báo tự động bắt Phân kỳ RSI (Bull/Bear Divergence)", "Tự động phát hiện và vẽ đường nối Phân kỳ Dương (Bull) & Phân kỳ Âm (Bear) trên RSI.", "oscillator", false, "#7E57C2", P_LEN(14), { badge: "PRO", fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70", dashed: true }, { value: 30, color: "#787B86", label: "30", dashed: true }] }),
  makeStudy("SEASONALITY", "Seasonality", "Seasonality", "Chu kỳ Mùa vụ thống kê (Seasonality %)", "Đo xác suất và hiệu suất trung bình theo chu kỳ mùa vụ lịch sử của cổ phiếu.", "oscillator", false, "#00897B", P_LEN(20), { badge: "PREMIUM", referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("SMA", "Simple moving average", "SMA", "Đường trung bình động giản đơn (SMA)", "Trung bình cộng giá đóng cửa trong N phiên gần nhất.", "trend", true, "#2962FF", P_LEN(20), { allowMultiple: true }),
  makeStudy("SMI_ERGODIC", "SMI ergodic indicator", "SMII", "Chỉ báo SMI Ergodic (William Blau)", "Đường động lượng TSI làm mượt kép kèm đường Signal EMA.", "oscillator", false, "#2962FF", P_FAST_SLOW(5, 20), { secondaryColor: "#FF6D00", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("SMI_ERGODIC_OSC", "SMI ergodic oscillator", "SMIO", "Histogram dao động SMI Ergodic", "Histogram hiệu số giữa đường SMI Ergodic và đường tín hiệu.", "oscillator", false, "#26A69A", P_FAST_SLOW(5, 20), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("SMMA", "Smoothed moving average", "SMMA", "Đường trung bình động làm mượt (SMMA / Wilder)", "Trung bình động làm mượt chuỗi dài của Welles Wilder giảm nhiễu tối đa.", "trend", true, "#673AB7", P_LEN(7), { allowMultiple: true }),
  makeStudy("STDDEV_IND", "Standard deviation", "StdDev", "Độ lệch chuẩn thống kê (Standard Deviation)", "Đo mức độ phân tán của giá đóng cửa quanh đường trung bình SMA.", "volatility", false, "#00897B", P_LEN(20)),
  makeStudy("STDERR_BANDS", "Standard error bands", "SE Bands", "Dải sai số chuẩn hồi quy (Standard Error Bands)", "Kênh hồi quy tuyến tính cộng/trừ 2 lần sai số chuẩn thống kê (Jon Andersen).", "volatility", true, "#2962FF", P_LEN(21)),
  makeStudy("STOCH", "Stochastic", "Stoch", "Dao động ngẫu nhiên (Stochastic %K/%D)", "So sánh giá đóng cửa với dải giá cao–thấp trong N phiên (Quá mua > 80, Quá bán < 20).", "oscillator", false, "#2962FF", [
    { key: "kPeriod", label: "Chu kỳ %K", defaultValue: 14, min: 2, max: 100 },
    { key: "smoothK", label: "Làm mượt %K", defaultValue: 3, min: 1, max: 30 },
    { key: "dPeriod", label: "Chu kỳ %D", defaultValue: 3, min: 1, max: 30 },
  ], { secondaryColor: "#FF6D00", fixedRange: [0, 100], referenceLevels: [{ value: 80, color: "#787B86", label: "80", dashed: true }, { value: 20, color: "#787B86", label: "20", dashed: true }] }),
  makeStudy("SMI", "Stochastic momentum index", "SMI", "Chỉ số động lượng Stochastic (SMI)", "Đo khoảng cách từ giá đóng cửa tới trung điểm dải High-Low trên thang -100 đến +100.", "oscillator", false, "#2962FF", P_LEN(10), { secondaryColor: "#FF6D00", fixedRange: [-100, 100], referenceLevels: [{ value: 40, color: "#787B86", label: "+40", dashed: true }, { value: -40, color: "#787B86", label: "-40", dashed: true }] }),
  makeStudy("STOCH_RSI", "Stochastic RSI", "Stoch RSI", "Chỉ báo Stochastic RSI", "Áp dụng công thức Stochastic trực tiếp lên chuỗi giá trị RSI để bắt điểm đảo chiều nhạy hơn.", "oscillator", false, "#2962FF", [
    { key: "rsiLength", label: "Chu kỳ RSI", defaultValue: 14, min: 2, max: 100 },
    { key: "stochLength", label: "Chu kỳ Stoch", defaultValue: 14, min: 2, max: 100 },
    { key: "k", label: "Làm mượt K", defaultValue: 3, min: 1, max: 20 },
    { key: "d", label: "Làm mượt D", defaultValue: 3, min: 1, max: 20 },
  ], { secondaryColor: "#FF6D00", fixedRange: [0, 100], referenceLevels: [{ value: 80, color: "#787B86", label: "80", dashed: true }, { value: 20, color: "#787B86", label: "20", dashed: true }] }),
  makeStudy("SUPERTREND", "Supertrend", "Supertrend", "Chỉ báo xu hướng Supertrend", "Chỉ báo bám sát xu hướng dựa trên biên độ biến động thực tế ATR.", "trend", true, "#089981", [
    { key: "atrLength", label: "Chu kỳ ATR", defaultValue: 10, min: 2, max: 100 },
    { key: "factor", label: "Hệ số nhân (Factor)", defaultValue: 3, min: 0.5, max: 10, step: 0.5 },
  ], { secondaryColor: "#F23645" }),
  makeStudy("TECH_RATINGS", "Technical ratings", "Ratings", "Chấm điểm Kỹ thuật tổng hợp TradingView (Technical Ratings)", "Tổng hợp 26 chỉ báo MA & Oscillators thành điểm Khuyến nghị Mua Mạnh (+1) / Bán Mạnh (-1).", "oscillator", false, "#2962FF", P_LEN(14), { badge: "PRO", fixedRange: [-1, 1], referenceLevels: [{ value: 0.5, color: "#089981", label: "Strong Buy", dashed: true }, { value: 0, color: "#787B86", label: "0", dashed: true }, { value: -0.5, color: "#F23645", label: "Strong Sell", dashed: true }] }),
  makeStudy("TWAP", "Time-weighted average price", "TWAP", "Giá trung bình gia quyền thời gian (TWAP)", "Mức giá trung bình định chế chia đều theo thời gian thực thi thuật toán.", "trend", true, "#00897B", P_LEN(20)),
  makeStudy("TRADING_SESSIONS", "Trading sessions", "Sessions", "Phân vùng Phiên giao dịch (Trading Sessions)", "Tô nền phân tách trực quan các cụm phiên giao dịch để theo dõi biên độ mở cửa/đóng cửa.", "volatility", true, "#2962FF", [{ key: "sessionBars", label: "Số nến mỗi cụm phiên", defaultValue: 10, min: 4, max: 40 }]),
  makeStudy("TSI_STRENGTH", "Trend strength index", "Trend Idx", "Chỉ số độ mạnh xu hướng (Trend Strength Index)", "Đo tương quan Pearson giữa giá và chuỗi thời gian nhằm xác nhận xu hướng bền vững.", "trend", false, "#2962FF", P_LEN(14), { fixedRange: [-1, 1], referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("TEMA", "Triple exponential moving average", "TEMA", "Trung bình động hàm mũ ba (TEMA)", "Công thức 3*EMA1 - 3*EMA2 + EMA3 giúp bám sát biến động giá tức thời.", "trend", true, "#00ACC1", P_LEN(9), { allowMultiple: true }),
  makeStudy("TRIX", "TRIX", "TRIX", "Dao động hàm mũ ba TRIX", "Tỷ lệ thay đổi phần trăm của đường trung bình hàm mũ ba giúp lọc bỏ hoàn toàn sóng nhiễu nhỏ.", "oscillator", false, "#E53935", P_LEN(18), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("TRUE_STRENGTH", "True strength index", "TSI", "Chỉ số sức mạnh thực sự (True Strength Index)", "Dao động động lượng làm mượt kép EMA(EMA(Momentum)) trên thang -100 đến +100.", "oscillator", false, "#2962FF", P_FAST_SLOW(13, 25), { secondaryColor: "#F23645", fixedRange: [-100, 100], referenceLevels: [{ value: 25, color: "#787B86", label: "+25", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -25, color: "#787B86", label: "-25", dashed: true }] }),
  makeStudy("ULCER", "Ulcer index", "Ulcer", "Chỉ số rủi ro sụt giảm Ulcer Index", "Đo độ sâu và thời gian kéo dài của các nhịp sụt giảm (Drawdown) từ đỉnh gần nhất.", "volatility", false, "#E53935", P_LEN(14)),
  makeStudy("ULTIMATE_OSC", "Ultimate oscillator", "UO", "Dao động tối thượng Larry Williams (Ultimate Oscillator)", "Kết hợp áp lực mua trên 3 khung chu kỳ (7, 14, 28) để hạn chế tín hiệu phân kỳ giả.", "oscillator", false, "#FB8C00", [
    { key: "fast", label: "Ngắn (7)", defaultValue: 7, min: 2, max: 50 },
    { key: "mid", label: "Trung (14)", defaultValue: 14, min: 4, max: 100 },
    { key: "slow", label: "Dài (28)", defaultValue: 28, min: 8, max: 200 },
  ], { fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70", dashed: true }, { value: 30, color: "#787B86", label: "30", dashed: true }] }),
  makeStudy("UP_DOWN_VOL", "Up/down volume", "U/D Vol", "Phân tách Khối lượng Tăng / Giảm (Up/Down Volume)", "Hiển thị khối lượng phiên tăng hướng lên (+) và phiên giảm hướng xuống (-).", "volume", false, "#089981", P_LEN(14), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: false }] }),
  makeStudy("VISIBLE_AVG_PRICE", "Visible average price", "VAP", "Giá trung bình vùng nhìn thấy (Visible Average Price)", "Tự động tính mức giá trung bình của toàn bộ các thanh nến đang hiển thị trên màn hình.", "trend", true, "#00ACC1", [{ key: "weighted", label: "Gia quyền KL (1=Có, 0=Không)", defaultValue: 1, min: 0, max: 1 }], { badge: "PRO" }),
  makeStudy("VOL_STOP", "Volatility stop", "VStop", "Điểm dừng biến động (Volatility Stop)", "Đường Trailing Stop thích ứng dựa trên bội số ATR khi giá đổi chiều.", "volatility", true, "#2962FF", [
    { key: "length", label: "Chu kỳ ATR", defaultValue: 20, min: 2, max: 100 },
    { key: "mult", label: "Hệ số ATR", defaultValue: 2, min: 0.5, max: 8, step: 0.5 },
  ]),
  makeStudy("VOL", "Volume", "Vol", "Khối lượng khớp lệnh & MA20 (Volume)", "Biểu đồ cột khối lượng giao dịch kèm đường trung bình khối lượng.", "volume", false, "#26A69A", [{ key: "maLength", label: "Chu kỳ MA Khối lượng", defaultValue: 20, min: 2, max: 100 }], { secondaryColor: "#F57C00" }),
  makeStudy("VOL_DELTA", "Volume delta", "Vol Delta", "Độ lệch Khối lượng chủ động (Volume Delta)", "Chênh lệch giữa thanh khoản mua đẩy giá lên và thanh khoản bán ép giá xuống mỗi nến.", "volume", false, "#089981", P_LEN(14), { badge: "PRO", referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("VWAP", "Volume-weighted average price", "VWAP", "Giá trung bình gia quyền khối lượng (VWAP)", "Mức giá trung bình định chế tính theo cả giá Typical và khối lượng khớp lệnh.", "volume", true, "#7E57C2", P_LEN(20, "Chu kỳ cuộn (Rolling Bars)", 250)),
  makeStudy("VWMA", "Volume-weighted moving average", "VWMA", "Trung bình động gia quyền khối lượng (VWMA)", "Đặt trọng số cao hơn cho các phiên có khối lượng khớp lệnh bùng nổ.", "trend", true, "#1E88E5", P_LEN(20), { allowMultiple: true }),
  makeStudy("VORTEX", "Vortex indicator", "VI", "Chỉ báo xoáy xu hướng (Vortex Indicator VI+ / VI-)", "Phát hiện điểm bắt đầu xu hướng mới khi đường VI+ và VI- giao cắt nhau.", "trend", false, "#2962FF", P_LEN(14), { secondaryColor: "#F23645", referenceLevels: [{ value: 1, color: "#787B86", label: "1.0", dashed: true }] }),
  makeStudy("WMA", "Weighted moving average", "WMA", "Đường trung bình động tỷ trọng tuyến tính (WMA)", "Trung bình động nhân trọng số tuyến tính theo thời gian.", "trend", true, "#9C27B0", P_LEN(20), { allowMultiple: true }),
  makeStudy("ALLIGATOR", "Williams alligator", "Alligator", "Cá sấu săn mồi Bill Williams (Jaw / Teeth / Lips)", "Bộ 3 đường SMMA dịch chuyển (13, 8, 5) mô phỏng trạng thái ngủ và săn mồi của xu hướng.", "trend", true, "#2962FF", [
    { key: "jaw", label: "Hàm cá sấu (Jaw)", defaultValue: 13, min: 3, max: 100 },
    { key: "teeth", label: "Răng cá sấu (Teeth)", defaultValue: 8, min: 2, max: 80 },
    { key: "lips", label: "Môi cá sấu (Lips)", defaultValue: 5, min: 2, max: 50 },
  ], { secondaryColor: "#F23645", tertiaryColor: "#089981" }),
  makeStudy("FRACTALS", "Williams fractal", "Fractals", "Điểm gãy đảo chiều Williams Fractals", "Tự động đánh dấu mũi tên tại các cụm đỉnh/đáy Fractal 5 nến của Bill Williams.", "trend", true, "#089981", [{ key: "periods", label: "Số nến mỗi bên", defaultValue: 2, min: 2, max: 10 }], { secondaryColor: "#F23645" }),
  makeStudy("WILLR", "Williams percent range", "%R", "Chỉ báo Williams %R", "Chỉ báo động lượng đo vị trí giá đóng cửa trong dải đỉnh–đáy (-20 đến -80).", "oscillator", false, "#8E24AA", P_LEN(14), { fixedRange: [-100, 0], referenceLevels: [{ value: -20, color: "#787B86", label: "-20", dashed: true }, { value: -80, color: "#787B86", label: "-80", dashed: true }] }),
  makeStudy("WOODIES_CCI", "Woodies CCI", "Woodies", "Hệ thống động lượng Woodies CCI (Turbo 6 & CCI 14)", "Kết hợp CCI(14) dạng biểu đồ xung lượng và đường Turbo CCI(6) bắt nhịp sớm.", "oscillator", false, "#00897B", P_FAST_SLOW(6, 14), { secondaryColor: "#FB8C00", referenceLevels: [{ value: 100, color: "#787B86", label: "+100", dashed: true }, { value: 0, color: "#787B86", label: "0", dashed: false }, { value: -100, color: "#787B86", label: "-100", dashed: true }] }),
  makeStudy("CHAIKIN_VOL", "Chaikin volatility", "Chaikin Vol", "Độ biến động biên độ Chaikin (Chaikin Volatility)", "Đo tốc độ giãn nở biên độ High–Low qua phần trăm thay đổi của EMA(High - Low).", "volatility", false, "#FB8C00", P_LEN(10), { referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("ELDER_IMPULSE", "Elder impulse system", "Elder Impulse", "Hệ thống Xung lượng Alexander Elder (EMA13 + MACD-H)", "Kết hợp độ dốc EMA13 và xung lượng MACD Histogram để xác định vùng Cấm Bán (Xanh) và Cấm Mua (Đỏ).", "trend", false, "#089981", P_LEN(13)),
  makeStudy("FRAMA", "Fractal adaptive moving average", "FRAMA", "Trung bình động thích ứng phân dạng (John Ehlers FRAMA)", "Sử dụng hình học Fractal để tự động giảm tốc khi giá đi ngang và bám sát khi giá bứt phá.", "trend", true, "#00ACC1", P_LEN(16), { allowMultiple: true }),
  makeStudy("GMMA", "Guppy multiple moving average", "GMMA", "Hệ thống 12 đường MA Guppy (6 Ngắn hạn + 6 Dài hạn)", "Đối chiếu hành vi Nhà giao dịch ngắn hạn (3..15) và Nhà đầu tư tổ chức (30..60) của Daryl Guppy.", "trend", true, "#2962FF", P_FAST_SLOW(8, 45), { secondaryColor: "#FF6D00", badge: "PRO" }),
  makeStudy("JMA", "Jurik moving average", "JMA", "Đường trung bình Jurik siêu mượt (JMA)", "Bộ lọc thích ứng bậc cao nổi tiếng định chế với độ trễ gần bằng 0 và không vọt lố.", "trend", true, "#00BFA5", P_LEN(14), { badge: "PREMIUM", allowMultiple: true }),
  makeStudy("LINREG_SLOPE", "Linear regression slope", "LinReg Slope", "Độ dốc Hồi quy tuyến tính (Slope)", "Đo vận tốc góc nghiêng tăng/giảm mỗi phiên của đường hồi quy tuyến tính.", "oscillator", false, "#2962FF", P_LEN(14), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeStudy("MAJORITY_RULE", "Majority rule", "Majority", "Quy tắc Đa số phiên tăng (% Majority Rule)", "Tính tỷ lệ phần trăm số phiên tăng giá chiếm ưu thế trong N phiên gần nhất.", "oscillator", false, "#7E57C2", P_LEN(14), { fixedRange: [0, 100], referenceLevels: [{ value: 70, color: "#787B86", label: "70%", dashed: true }, { value: 30, color: "#787B86", label: "30%", dashed: true }] }),
  makeStudy("PIVOT_CAMARILLA", "Pivot points Camarilla", "Camarilla", "Điểm xoay trong ngày Camarilla (R3/R4 & S3/S4)", "Hệ thống ngưỡng đảo chiều biên hẹp R3/S3 và bứt phá R4/S4 chuyên dụng cho giao dịch chủ động.", "trend", true, "#00897B", [{ key: "period", label: "Cụm phiên", defaultValue: 15, min: 5, max: 60 }], { badge: "PRO" }),
  makeStudy("PIVOT_FIBONACCI", "Pivot points Fibonacci", "Fib Pivots", "Điểm xoay tỷ lệ vàng Fibonacci (0.382 / 0.618 / 1.0)", "Kết hợp trục Pivot chuẩn với các biên hỗ trợ/kháng cự tỷ lệ Fibonacci 38.2%, 61.8%, 100%.", "trend", true, "#2962FF", [{ key: "period", label: "Cụm phiên", defaultValue: 15, min: 5, max: 60 }]),
  makeStudy("PIVOT_WOODIE", "Pivot points Woodie", "Woodie Pivots", "Điểm xoay Woodie đặt trọng số giá Đóng cửa", "Đặt trọng số gấp đôi cho giá đóng cửa để phản ánh sát tâm lý cuối phiên.", "trend", true, "#9C27B0", [{ key: "period", label: "Cụm phiên", defaultValue: 15, min: 5, max: 60 }]),
  makeStudy("STC", "Schaff trend cycle", "STC", "Chu kỳ xu hướng Schaff (STC — MACD + Double Stoch)", "Kết hợp MACD với Stochastic hai tầng giúp tín hiệu đảo chiều nhanh gấp đôi MACD truyền thống.", "oscillator", false, "#2962FF", [
    { key: "length", label: "Chu kỳ Stoch", defaultValue: 10, min: 3, max: 50 },
    { key: "fast", label: "EMA nhanh", defaultValue: 23, min: 5, max: 100 },
    { key: "slow", label: "EMA chậm", defaultValue: 50, min: 10, max: 200 },
  ], { fixedRange: [0, 100], referenceLevels: [{ value: 75, color: "#787B86", label: "75", dashed: true }, { value: 25, color: "#787B86", label: "25", dashed: true }] }),
  makeStudy("T3_MA", "Tillson T3 moving average", "T3", "Trung bình động hàm mũ bậc sáu Tillson T3", "Làm mượt 6 tầng EMA kết hợp hệ số Volume Factor (0.7) khử nhiễu tối ưu.", "trend", true, "#E91E63", P_LEN(8), { allowMultiple: true }),
  makeStudy("VIDYA", "Variable index dynamic average", "VIDYA", "Trung bình động biến thiên Tushar Chande (VIDYA)", "Điều chỉnh hệ số làm mượt tự động theo độ mạnh chỉ báo động lượng CMO.", "trend", true, "#3949AB", P_LEN(14), { allowMultiple: true }),
  makeStudy("VOL_OSC", "Volume oscillator", "Vol Osc", "Dao động Khối lượng Ngắn/Dài (% Volume Oscillator)", "Đo chênh lệch % giữa EMA khối lượng ngắn (5) và dài (10) để xác nhận dòng tiền đẩy sóng.", "volume", false, "#00897B", P_FAST_SLOW(5, 10), { referenceLevels: [{ value: 0, color: "#787B86", label: "0%", dashed: true }] }),
  makeStudy("VWAP_BANDS", "VWAP with standard deviation bands", "VWAP ±2σ", "Dải VWAP Định chế kèm Độ lệch chuẩn (±1σ / ±2σ)", "Trục VWAP trung tâm kèm hai dải biên lệch chuẩn khối lượng định chế.", "volume", true, "#7E57C2", [
    { key: "length", label: "Chu kỳ VWAP", defaultValue: 20, min: 5, max: 250 },
    { key: "mult", label: "Độ lệch chuẩn (±σ)", defaultValue: 2, min: 0.5, max: 4, step: 0.5 },
  ], { badge: "PRO" }),
  makeStudy("ZLEMA", "Zero-lag exponential moving average", "ZLEMA", "Trung bình động hàm mũ Triệt tiêu độ trễ (Zero-Lag EMA)", "Khử dữ liệu trễ (Lag = (N-1)/2) trước khi tính EMA giúp bắt nhịp đảo chiều tức thì.", "trend", true, "#00BFA5", P_LEN(20), { allowMultiple: true }),
  makeStudy("ZIGZAG", "Zigzag", "ZigZag", "Đường sóng ZigZag nối Đỉnh – Đáy", "Lọc bỏ biến động nhỏ dưới ngưỡng % lệch để nối trực quan các con sóng chính trên biểu đồ.", "trend", true, "#2962FF", [{ key: "devPct", label: "Độ lệch đảo chiều (%)", defaultValue: 4, min: 0.5, max: 25, step: 0.5 }]),
]

/* ----------------------------------------------------------------------------
 * 2. BUILT-IN BACKTESTABLE STRATEGIES (23 Official TradingView Strategies)
 * ---------------------------------------------------------------------------- */
export const BUILTIN_STRATEGIES: TvIndicatorMetadata[] = [
  makeStrategy("STRAT_BAR_UPDN", "Bar up/down strategy", "BarUpDn Strat", "Chiến lược Nến Tăng/Giảm mở rộng", "Mua khi nến đóng cửa cao hơn mở cửa và mở cửa cao hơn đóng cửa trước đó; tự động tính hiệu suất Backtest.", "STD;Bars%1Up%1Dn%1Strategy", P_LEN(1, "Xác nhận nến")),
  makeStrategy("STRAT_BB", "Bollinger Bands strategy", "BB Strat", "Chiến lược Đảo chiều dải Bollinger", "Mua khi giá cắt lên từ biên dưới Bollinger và Bán chốt lời khi chạm/cắt xuống từ biên trên.", "STD;Bollinger%1Bands%1Strategy", [
    { key: "length", label: "Chu kỳ BB", defaultValue: 20, min: 5, max: 150 },
    { key: "mult", label: "StdDev", defaultValue: 2, min: 0.5, max: 4, step: 0.2 },
  ]),
  makeStrategy("STRAT_BB_DIR", "Bollinger Bands strategy directed", "BB Directed", "Chiến lược Bollinger Thuận xu hướng", "Chỉ thực thi tín hiệu Mua Bollinger khi giá nằm trên MA200 xu hướng lớn.", "STD;Bollinger%1Band%1Directed%1Strategy", P_LEN(20)),
  makeStrategy("STRAT_CHAN_BO", "Channel breakout strategy", "Channel BO", "Chiến lược Phá vỡ Kênh giá (Turtle Breakout)", "Mua khi giá vượt đỉnh cao nhất N phiên và đóng/đảo chiều khi thủng đáy N phiên.", "STD;Channel%1BreakOut%1Strategy", P_LEN(20, "Chu kỳ kênh giá")),
  makeStrategy("STRAT_CONSEC_UD", "Consecutive up/down strategy", "Consec U/D", "Chiến lược Chuỗi nến Tăng/Giảm liên tiếp", "Vào lệnh khi xuất hiện N phiên tăng liên tiếp hoặc bắt đảo chiều sau N phiên giảm sâu.", "STD;Consecutive%1Ups%1Downs%1Strategy", [{ key: "length", label: "Số phiên liên tiếp", defaultValue: 3, min: 2, max: 10 }]),
  makeStrategy("STRAT_GREEDY", "Greedy strategy", "Greedy Strat", "Chiến lược Gap & Động lượng Greedy", "Khai thác các phiên mở cửa nhảy Gap vượt đỉnh/đáy phiên trước có tiếp diễn xung lượng.", "STD;Greedy%1Strategy", P_LEN(10, "Giới hạn giữ lệnh")),
  makeStrategy("STRAT_INSIDE_BAR", "Inside bar strategy", "InsideBar", "Chiến lược Nến nằm trong (Inside Bar Breakout)", "Phát hiện cụm nến nén Inside Bar và vào lệnh theo chiều phá vỡ biên nến mẹ.", "STD;InSide%1Bar%1Strategy", P_LEN(14, "Lọc xu hướng EMA")),
  makeStrategy("STRAT_KELTNER", "Keltner channels strategy", "KC Strat", "Chiến lược Bứt phá Kênh Keltner", "Giao dịch theo đà bứt phá biên trên và biên dưới kênh biến động Keltner ATR.", "STD;Keltner%1Channel%1Strategy", [
    { key: "length", label: "Chu kỳ EMA", defaultValue: 20, min: 5, max: 150 },
    { key: "mult", label: "Hệ số ATR", defaultValue: 2, min: 0.5, max: 5, step: 0.5 },
  ]),
  makeStrategy("STRAT_MACD", "MACD strategy", "MACD Strat", "Chiến lược Giao cắt đường MACD & Signal", "Mua khi đường MACD cắt lên Signal và Bán/Đảo chiều khi cắt xuống.", "STD;MACD%1Strategy", [
    { key: "fast", label: "EMA nhanh", defaultValue: 12, min: 2, max: 80 },
    { key: "slow", label: "EMA chậm", defaultValue: 26, min: 5, max: 160 },
    { key: "signal", label: "Signal", defaultValue: 9, min: 2, max: 50 },
  ]),
  makeStrategy("STRAT_MOMENTUM", "Momentum strategy", "Mom Strat", "Chiến lược Gia tốc Động lượng (Momentum)", "Vào lệnh khi cả Momentum và gia tốc bậc hai của Momentum đồng thuận dương/âm.", "STD;Momentum%1Strategy", P_LEN(12)),
  makeStrategy("STRAT_MA_CROSS", "Moving average crossover", "MA Cross Strat", "Chiến lược Giao cắt Trung bình động đơn", "Mua khi giá vượt lên đường SMA có xác nhận và thoát vị thế khi giá cắt xuống.", "STD;MovingAvg%1Cross%1Strategy", P_LEN(20, "Chu kỳ SMA")),
  makeStrategy("STRAT_OUTSIDE_BAR", "Outside bar strategy", "OutsideBar", "Chiến lược Nến nhấn chìm biên độ (Outside Bar)", "Bắt tín hiệu bùng nổ khi thanh nến bao trùm toàn bộ đỉnh–đáy phiên liền trước.", "STD;OutSide%1Bar%1Strategy", P_LEN(14)),
  makeStrategy("STRAT_PSAR", "Parabolic SAR strategy", "PSAR Strat", "Chiến lược Bám xu hướng Parabolic SAR", "Đảo chiều vị thế tự động mỗi khi giá chạm đường chấm Parabolic SAR.", "STD;Parabolic%1SAR%1Strategy", [
    { key: "start", label: "Start AF", defaultValue: 0.02, min: 0.005, max: 0.1, step: 0.01 },
    { key: "max", label: "Max AF", defaultValue: 0.2, min: 0.05, max: 0.5, step: 0.05 },
  ]),
  makeStrategy("STRAT_PIVOT_EXT", "Pivot extension strategy", "Pivot Ext", "Chiến lược Điểm xoay mở rộng (Pivot Extension)", "Bắt nhịp hồi quy từ đáy Swing Low và chốt tại đỉnh Swing High.", "STD;Pivot%1Extension%1Strategy", [{ key: "length", label: "Độ rộng Swing", defaultValue: 5, min: 2, max: 20 }]),
  makeStrategy("STRAT_PIVOT_REV", "Pivot reversal strategy", "Pivot Rev", "Chiến lược Phá đỉnh/đáy Pivot Reversal", "Đặt lệnh Stop vượt qua đỉnh Swing High gần nhất để ăn trọn sóng đẩy.", "STD;Pivot%1Reversal%1Strategy", [{ key: "length", label: "Độ rộng Swing", defaultValue: 4, min: 2, max: 20 }]),
  makeStrategy("STRAT_PRICE_CH", "Price channel strategy", "PriceCh Strat", "Chiến lược Kênh giá Donchian cổ điển", "Hệ thống giao dịch theo xu hướng kinh điển dựa trên kênh đỉnh/đáy N phiên.", "STD;Price%1Channel%1Strategy", P_LEN(20)),
  makeStrategy("STRAT_ROB_ADX", "Rob Booker — ADX breakout", "ADX BO", "Chiến lược Bứt phá nén ADX (Rob Booker)", "Mua/Bán khi chỉ số ADX vừa thoát khỏi vùng tích lũy thấp (< 20) kèm phá biên giá.", "STD;Rob%1Booker%1ADX%1Breakout%1Strategy", P_LEN(14), "PRO"),
  makeStrategy("STRAT_RSI", "RSI strategy", "RSI Strat", "Chiến lược Quá bán / Quá mua RSI", "Mua khi RSI cắt lên khỏi vùng quá bán (30) và Bán khi RSI cắt xuống khỏi vùng quá mua (70).", "STD;RSI%1Strategy", [
    { key: "length", label: "Chu kỳ RSI", defaultValue: 14, min: 2, max: 60 },
    { key: "oversold", label: "Ngưỡng Quá bán", defaultValue: 30, min: 10, max: 45 },
    { key: "overbought", label: "Ngưỡng Quá mua", defaultValue: 70, min: 55, max: 90 },
  ]),
  makeStrategy("STRAT_STOCH_SLOW", "Stochastic slow strategy", "Stoch Strat", "Chiến lược Giao cắt Stochastic Chậm", "Mua khi %K cắt lên %D trong vùng quá bán (< 20) và Bán khi cắt xuống trong vùng quá mua (> 80).", "STD;Stochastic%1Slow%1Strategy", P_LEN(14)),
  makeStrategy("STRAT_SUPERTREND", "Supertrend strategy", "Supertrend Strat", "Chiến lược Đảo chiều xu hướng Supertrend", "Hệ thống tự động Backtest điểm Mua/Bán đổi màu của đường Supertrend ATR.", "STD;Supertrend%Strategy", [
    { key: "atrLength", label: "Chu kỳ ATR", defaultValue: 10, min: 2, max: 60 },
    { key: "factor", label: "Hệ số Factor", defaultValue: 3, min: 0.5, max: 8, step: 0.5 },
  ], "PRO"),
  makeStrategy("STRAT_TECH_RATINGS", "Technical ratings strategy", "Ratings Strat", "Chiến lược Chấm điểm Đa chỉ báo TradingView", "Tự động mở vị thế khi điểm hợp lưu Technical Ratings đạt ngưỡng Strong Buy / Strong Sell.", "STD;Technical%1Ratings%1Strategy", P_LEN(14), "ULTIMATE"),
  makeStrategy("STRAT_MA2_CROSS", "Two moving averages crossover", "2MA Cross", "Chiến lược Giao cắt 2 đường MA (Golden / Death Cross)", "Backtest kinh điển giao cắt EMA nhanh (9) và SMA chậm (21).", "STD;MovingAvg%1Cross2%1Strategy", P_FAST_SLOW(9, 21)),
  makeStrategy("STRAT_VOL_EXP", "Volatility expansion-close strategy", "VolExpan", "Chiến lược Bùng nổ biên độ biến động ATR", "Vào lệnh khi biên độ nến tăng đột biến vượt bội số ATR trung bình.", "STD;Volty%1Expan%1Close%1Strategy", [
    { key: "length", label: "Chu kỳ ATR", defaultValue: 10, min: 2, max: 60 },
    { key: "mult", label: "Hệ số bùng nổ", defaultValue: 1.5, min: 0.5, max: 4, step: 0.25 },
  ]),
]

/* ----------------------------------------------------------------------------
 * 3. VOLUME PROFILES, TPO MARKET PROFILES & FOOTPRINT (10 Pro/Premium/Ultimate)
 * ---------------------------------------------------------------------------- */
export const PROFILE_STUDIES: TvIndicatorMetadata[] = [
  makeProfile("VP_VISIBLE", "Visible Range Volume Profile", "VRVP", "Hồ sơ khối lượng Vùng hiển thị (VRVP)", "Tự động phân bổ khối lượng Mua/Bán theo từng mức giá của toàn bộ vùng nến đang nhìn thấy kèm POC, VAH, VAL (70%).", "VolumeProfileVisible@tv-volumebyprice", "PRO", 24, 70),
  makeProfile("VP_FIXED", "Fixed Range Volume Profile", "FRVP", "Hồ sơ khối lượng Khoảng cố định (FRVP)", "Phân tích mật độ thanh khoản tích lũy, POC và Value Area trên N phiên gần nhất.", "VolumeProfileFixed@tv-volumebyprice", "PRO", 24, 70),
  makeProfile("VP_SESSION", "Session Volume Profile", "SVP", "Hồ sơ khối lượng theo Cụm phiên (SVP)", "Vẽ nhiều cụm Volume Profile độc lập cho từng chu kỳ/cụm phiên giao dịch trên biểu đồ.", "VolumeProfileSessionRough@tv-volumebyprice", "PRO", 16, 70),
  makeProfile("VP_SESSION_HD", "Session Volume Profile HD", "SVP HD", "Hồ sơ khối lượng Độ phân giải cao (SVP HD)", "Phiên bản độ nét cao chia mịn bước giá giúp lộ diện các nút thắt thanh khoản HVN / LVN.", "VolumeProfileSessionDetailed@tv-volumebyprice", "PREMIUM", 40, 70),
  makeProfile("VP_PERIODIC", "Periodic Volume Profile", "PVP", "Hồ sơ khối lượng Định kỳ (Periodic VP)", "Tự động dựng Volume Profile cho từng tháng/quý kèm đường dịch chuyển POC.", "VolumeProfilePeriodic@tv-volumebyprice", "PREMIUM", 20, 70),
  makeProfile("VP_AUTO_ANCHORED", "Auto Anchored Volume Profile", "AAVP", "Hồ sơ khối lượng Tự động neo đỉnh/đáy (AAVP)", "Tự động neo điểm bắt đầu Volume Profile tại đỉnh hoặc đáy lớn nhất gần nhất.", "AutoAnchoredVolumeProfile@tv-volumebyprice", "PREMIUM", 24, 70),
  makeProfile("TPO_PERIODIC", "Time Price Opportunity (Periodic TPO)", "TPO", "Hồ sơ thị trường Market Profile (Periodic TPO)", "Hiển thị ma trận chữ cái A–Z theo thời gian lưu trú của giá (Time Price Opportunity) kèm POC và Value Area.", "TimePriceOpportunityPeriodic@tv-volumebyprice", "ULTIMATE", 20, 70),
  makeProfile("TPO_SESSION", "Session TPO (Market Profile)", "Session TPO", "Hồ sơ thị trường TPO theo Cụm phiên", "Vẽ cấu trúc phân phối chuẩn hình chuông TPO cho từng cụm phiên độc lập.", "TimePriceOpportunitySession@tv-volumebyprice", "ULTIMATE", 16, 70),
  makeProfile("VOL_FOOTPRINT", "Volume Footprint (Cluster Delta)", "Footprint", "Biểu đồ Dấu chân Dòng tiền (Volume Footprint)", "Hiển thị trực quan cụm thanh khoản Bid x Ask và Imbalance Delta ngay trên các nến gần nhất.", "VolumeFootprint@tv-volumebyprice", "ULTIMATE", 12, 70),
  {
    id: "AUTO_ANCHORED_VWAP",
    name: "Auto Anchored VWAP",
    shortName: "AAVWAP",
    viName: "Đường VWAP Tự động neo tại Đỉnh/Đáy chính",
    description: "Tự động neo đường VWAP kèm dải lệch chuẩn ±1σ từ điểm đảo chiều lớn nhất gần nhất.",
    category: "profile",
    section: "technicals",
    subTab: "profiles",
    tvScriptId: "AutoAnchoredVWAP@tv-basicstudies",
    badge: "PRO",
    overlay: true,
    defaultColor: "#7E57C2",
    secondaryColor: "#00ACC1",
    params: [{ key: "lookback", label: "Chu kỳ tìm đỉnh/đáy neo", defaultValue: 60, min: 15, max: 220 }],
  },
]

/* ----------------------------------------------------------------------------
 * 4. CHART PATTERNS (19 Pro/Ultimate) + CANDLESTICK PATTERNS (45 Official)
 * ---------------------------------------------------------------------------- */
export const AUTO_CHART_PATTERNS: TvIndicatorMetadata[] = [
  makeChartPattern("PAT_ALL_CHART", "All Chart Patterns", "All Patterns", "Tự động nhận diện Mọi Mô hình Giá (All Chart Patterns)", "Quét đồng thời Hai Đỉnh/Đáy, Vai-Đầu-Vai, Cốc Tay Cầm, Cờ, Nêm, Tam Giác kèm mục tiêu giá.", "ULTIMATE"),
  makeChartPattern("PAT_DOUBLE_TOP", "Double Top Chart Pattern", "Double Top", "Mô hình Hai Đỉnh đảo chiều giảm (Double Top)", "Tự động nối 2 đỉnh cân xứng, đường viền cổ Neckline và phóng mục tiêu giá giảm.", "PREMIUM"),
  makeChartPattern("PAT_DOUBLE_BOTTOM", "Double Bottom Chart Pattern", "Double Bot", "Mô hình Hai Đáy đảo chiều tăng (Double Bottom)", "Tự động nhận diện cấu trúc chữ W, đường Neckline và mục tiêu bứt phá.", "PREMIUM"),
  makeChartPattern("PAT_TRIPLE_TOP", "Triple Top Chart Pattern", "Triple Top", "Mô hình Ba Đỉnh (Triple Top)", "Nhận diện vùng phân phối 3 đỉnh liên tiếp và ngưỡng hỗ trợ Neckline.", "ULTIMATE"),
  makeChartPattern("PAT_TRIPLE_BOTTOM", "Triple Bottom Chart Pattern", "Triple Bot", "Mô hình Ba Đáy tích lũy (Triple Bottom)", "Nhận diện vùng gom hàng 3 đáy vững chắc kèm mục tiêu Breakout.", "ULTIMATE"),
  makeChartPattern("PAT_HEAD_SHOULDERS", "Head and Shoulders Chart Pattern", "H&S", "Mô hình Vai – Đầu – Vai thuận (Head & Shoulders)", "Tự động vẽ Vai Trái – Đầu – Vai Phải, đường Neckline và mục tiêu đảo chiều.", "ULTIMATE"),
  makeChartPattern("PAT_INV_HEAD_SHOULDERS", "Inverse Head and Shoulders", "Inv H&S", "Mô hình Vai – Đầu – Vai ngược (Inverse H&S)", "Mô hình tạo đáy kinh điển với Vai Trái – Đầu thấp nhất – Vai Phải nâng cao.", "ULTIMATE"),
  makeChartPattern("PAT_CUP_HANDLE", "Cup and Handle Chart Pattern", "Cup&Handle", "Mô hình Cốc và Tay cầm William O'Neil (Cup & Handle)", "Phát hiện nền giá vòng cung chữ U kèm nhịp rũ tay cầm trước điểm Pivot nổ vol.", "ULTIMATE"),
  makeChartPattern("PAT_INV_CUP_HANDLE", "Inverted Cup and Handle", "Inv Cup", "Mô hình Cốc Tay cầm ngược", "Nhận diện cấu trúc phân phối vòm ngược và nhịp hồi yếu trước khi gãy nền.", "ULTIMATE"),
  makeChartPattern("PAT_BULL_FLAG", "Bullish Flag Chart Pattern", "Bull Flag", "Mô hình Cờ Tăng tiếp diễn (Bullish Flag)", "Cột cờ tăng mạnh theo sau bởi kênh rũ nhẹ song song hướng xuống.", "PRO"),
  makeChartPattern("PAT_BEAR_FLAG", "Bearish Flag Chart Pattern", "Bear Flag", "Mô hình Cờ Giảm tiếp diễn (Bearish Flag)", "Nhịp giảm gắt kèm kênh hồi kỹ thuật yếu trước khi giảm tiếp.", "PRO"),
  makeChartPattern("PAT_BULL_PENNANT", "Bullish Pennant Chart Pattern", "Bull Pennant", "Mô hình Cờ Đuôi Nheo Tăng (Bullish Pennant)", "Cột cờ tăng dốc hội tụ trong tam giác cân nhỏ chuẩn bị bứt phá tiếp.", "PRO"),
  makeChartPattern("PAT_BEAR_PENNANT", "Bearish Pennant Chart Pattern", "Bear Pennant", "Mô hình Cờ Đuôi Nheo Giảm (Bearish Pennant)", " Tam giác nén hẹp sau nhịp rơi mạnh báo hiệu tiếp diễn xu hướng giảm.", "PRO"),
  makeChartPattern("PAT_RISING_WEDGE", "Rising Wedge Chart Pattern", "Rising Wedge", "Mô hình Cái Nêm Hướng Lên (Rising Wedge)", "Hai biên hội tụ dốc lên cảnh báo suy yếu động lượng và rủi ro gãy kênh.", "PREMIUM"),
  makeChartPattern("PAT_FALLING_WEDGE", "Falling Wedge Chart Pattern", "Falling Wedge", "Mô hình Cái Nêm Hướng Xuống (Falling Wedge)", "Hai biên hội tụ dốc xuống báo hiệu cạn cung và xác suất bật tăng mạnh.", "PREMIUM"),
  makeChartPattern("PAT_TRIANGLE", "Triangle Chart Pattern", "Triangle", "Mô hình Tam Giác Hội Tụ (Ascending / Descending / Symmetrical)", "Tự động kẻ hai cạnh hội tụ của mô hình Tam giác kèm hướng bứt phá.", "PRO"),
  makeChartPattern("PAT_RECTANGLE", "Rectangle Chart Pattern", "Rectangle", "Mô hình Hộp Chữ Nhật tích lũy Darvas (Rectangle)", "Đóng khung vùng đi ngang tích lũy giữa Hỗ trợ phẳng và Kháng cự phẳng.", "PRO"),
  makeChartPattern("PAT_ELLIOTT_WAVE", "Elliott Wave Chart Pattern", "Elliott 1-5", "Tự động đếm Sóng Elliott (1–2–3–4–5 & A–B–C)", "Tự động gắn nhãn chu kỳ 5 sóng đẩy (1)-(2)-(3)-(4)-(5) và 3 sóng chỉnh (A)-(B)-(C).", "ULTIMATE"),
  makeChartPattern("PAT_HARMONIC_XABCD", "Harmonic XABCD Pattern (Gartley/Bat)", "Harmonic", "Tự động quét Mô hình Harmonic XABCD (Gartley / Bat / Butterfly)", "Tô hai cánh tam giác tỷ lệ vàng Fibonacci X–A–B–C–D trên biểu đồ.", "ULTIMATE"),
]

export const CANDLESTICK_PATTERNS: TvIndicatorMetadata[] = [
  makeCandlePattern("CDL_ALL", "All candlestick patterns", "All Candles", "Tất cả 44 Mẫu hình Nến Nhật đảo chiều & tiếp diễn", "all", "STD;Candlestick%1Pattern%1All%1Candlestick%1Patterns"),
  makeCandlePattern("CDL_ABANDONED_BABY_BEAR", "Abandoned baby (bearish)", "AbandBaby-", "Em bé bị bỏ rơi Giảm (Bearish Abandoned Baby)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Abandoned%1Baby"),
  makeCandlePattern("CDL_ABANDONED_BABY_BULL", "Abandoned baby (bullish)", "AbandBaby+", "Em bé bị bỏ rơi Tăng (Bullish Abandoned Baby)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Abandoned%1Baby"),
  makeCandlePattern("CDL_DARK_CLOUD", "Dark cloud cover (bearish)", "DarkCloud", "Mây đen bao phủ (Dark Cloud Cover)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Dark%1Cloud%1Cover"),
  makeCandlePattern("CDL_DOJI", "Doji (neutral)", "Doji", "Nến Doji lưỡng lự cung cầu", "neutral", "STD;Candlestick%1Pattern%1Doji"),
  makeCandlePattern("CDL_DOJI_STAR_BEAR", "Doji star (bearish)", "DojiStar-", "Sao Doji đảo chiều Giảm (Bearish Doji Star)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Doji%1Star"),
  makeCandlePattern("CDL_DOJI_STAR_BULL", "Doji star (bullish)", "DojiStar+", "Sao Doji đảo chiều Tăng (Bullish Doji Star)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Doji%1Star"),
  makeCandlePattern("CDL_DOWNSIDE_TASUKI", "Downside Tasuki gap (bearish)", "Tasuki-", "Khoảng trống Tasuki Giảm", "bear", "STD;Candlestick%1Pattern%1Bearish%1Downside%1Tasuki%1Gap"),
  makeCandlePattern("CDL_DRAGONFLY_DOJI", "Dragonfly Doji (bullish)", "Dragonfly", "Nến Doji Chuồn chuồn rút chân (Dragonfly Doji)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Dragonfly%1Doji"),
  makeCandlePattern("CDL_ENGULFING_BEAR", "Engulfing (bearish)", "Engulf-", "Nhấn chìm suy giảm (Bearish Engulfing)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Engulfing"),
  makeCandlePattern("CDL_ENGULFING_BULL", "Engulfing (bullish)", "Engulf+", "Nhấn chìm tăng trưởng (Bullish Engulfing)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Engulfing"),
  makeCandlePattern("CDL_EVENING_DOJI", "Evening Doji star (bearish)", "EveDoji", "Sao Hôm Doji (Evening Doji Star)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Evening%1Doji%1Star"),
  makeCandlePattern("CDL_EVENING_STAR", "Evening star (bearish)", "EveStar", "Mô hình Sao Hôm đảo chiều đỉnh (Evening Star)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Evening%1Star"),
  makeCandlePattern("CDL_FALLING_3", "Falling three methods (bearish)", "Fall3", "Ba bước giảm giá tiếp diễn (Falling Three Methods)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Falling%1Three%1Methods"),
  makeCandlePattern("CDL_FALLING_WINDOW", "Falling window (bearish)", "FallWin", "Cửa sổ rơi Gap Down (Falling Window)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Falling%1Window"),
  makeCandlePattern("CDL_GRAVESTONE_DOJI", "Gravestone Doji (bearish)", "Gravestone", "Nến Doji Bia mộ (Gravestone Doji)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Gravestone%1Doji"),
  makeCandlePattern("CDL_HAMMER", "Hammer (bullish)", "Hammer", "Nến Búa đảo chiều đáy (Bullish Hammer)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Hammer"),
  makeCandlePattern("CDL_HANGING_MAN", "Hanging man (bearish)", "HangMan", "Nến Người treo cổ vùng đỉnh (Hanging Man)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Hanging%1Man"),
  makeCandlePattern("CDL_HARAMI_BEAR", "Harami (bearish)", "Harami-", "Nến Mẹ bồng con Giảm (Bearish Harami)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Harami"),
  makeCandlePattern("CDL_HARAMI_BULL", "Harami (bullish)", "Harami+", "Nến Mẹ bồng con Tăng (Bullish Harami)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Harami"),
  makeCandlePattern("CDL_HARAMI_CROSS_BEAR", "Harami cross (bearish)", "HaramiX-", "Harami Chữ thập Giảm (Bearish Harami Cross)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Harami%1Cross"),
  makeCandlePattern("CDL_HARAMI_CROSS_BULL", "Harami cross (bullish)", "HaramiX+", "Harami Chữ thập Tăng (Bullish Harami Cross)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Harami%1Cross"),
  makeCandlePattern("CDL_INVERTED_HAMMER", "Inverted hammer (bullish)", "InvHammer", "Nến Búa ngược tạo đáy (Inverted Hammer)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Inverted%1Hammer"),
  makeCandlePattern("CDL_KICKING_BEAR", "Kicking (bearish)", "Kick-", "Cú đá đảo chiều Giảm (Bearish Kicking)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Kicking"),
  makeCandlePattern("CDL_KICKING_BULL", "Kicking (bullish)", "Kick+", "Cú đá đảo chiều Tăng (Bullish Kicking)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Kicking"),
  makeCandlePattern("CDL_LONG_LOWER_SHADOW", "Long lower shadow (bullish)", "LowShadow", "Bóng nến dưới dài rút chân (Long Lower Shadow)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Long%1Lower%1Shadow"),
  makeCandlePattern("CDL_LONG_UPPER_SHADOW", "Long upper shadow (bearish)", "UpShadow", "Bóng nến trên dài bị bán dội (Long Upper Shadow)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Long%1Upper%1Shadow"),
  makeCandlePattern("CDL_MARUBOZU_BLACK", "Marubozu black (bearish)", "Marubozu-", "Nến Cường lực Đỏ đặc (Bearish Marubozu)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Marubozu%1Black"),
  makeCandlePattern("CDL_MARUBOZU_WHITE", "Marubozu white (bullish)", "Marubozu+", "Nến Cường lực Xanh đặc (Bullish Marubozu)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Marubozu%1White"),
  makeCandlePattern("CDL_MORNING_DOJI", "Morning Doji star (bullish)", "MornDoji", "Sao Mai Doji tạo đáy (Morning Doji Star)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Morning%1Doji%1Star"),
  makeCandlePattern("CDL_MORNING_STAR", "Morning star (bullish)", "MornStar", "Mô hình Sao Mai đảo chiều tăng (Morning Star)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Morning%1Star"),
  makeCandlePattern("CDL_ON_NECK", "On-neck (bearish)", "OnNeck", "Mô hình Đường viền cổ giảm (Bearish On-Neck)", "bear", "STD;Candlestick%1Pattern%1Bearish%1On%1Neck"),
  makeCandlePattern("CDL_PIERCING", "Piercing (bullish)", "Piercing", "Nến Xuyên thấu đảo chiều tăng (Bullish Piercing)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Piercing"),
  makeCandlePattern("CDL_RISING_3", "Rising three methods (bullish)", "Rise3", "Ba bước tăng giá tiếp diễn (Rising Three Methods)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Rising%1Three%1Methods"),
  makeCandlePattern("CDL_RISING_WINDOW", "Rising window (bullish)", "RiseWin", "Cửa sổ tăng Gap Up (Rising Window)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Rising%1Window"),
  makeCandlePattern("CDL_SHOOTING_STAR", "Shooting star (bearish)", "ShootStar", "Nến Sao Băng đảo chiều đỉnh (Shooting Star)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Shooting%1Star"),
  makeCandlePattern("CDL_SPINNING_BLACK", "Spinning top black (neutral)", "Spin-", "Con xoay lưỡng lự Đỏ (Spinning Top Black)", "neutral", "STD;Candlestick%1Pattern%1Spinning%1Top%1Black"),
  makeCandlePattern("CDL_SPINNING_WHITE", "Spinning top white (neutral)", "Spin+", "Con xoay lưỡng lự Xanh (Spinning Top White)", "neutral", "STD;Candlestick%1Pattern%1Spinning%1Top%1White"),
  makeCandlePattern("CDL_3_BLACK_CROWS", "Three black crows (bearish)", "3Crows", "Ba con quạ đen (Three Black Crows)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Three%1Black%1Crows"),
  makeCandlePattern("CDL_3_WHITE_SOLDIERS", "Three white soldiers (bullish)", "3Soldiers", "Ba chàng lính ngự lâm (Three White Soldiers)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Three%1White%1Soldiers"),
  makeCandlePattern("CDL_TRISTAR_BEAR", "Tri-star (bearish)", "TriStar-", "Ba ngôi sao Doji Giảm (Bearish Tri-Star)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Tri-Star"),
  makeCandlePattern("CDL_TRISTAR_BULL", "Tri-star (bullish)", "TriStar+", "Ba ngôi sao Doji Tăng (Bullish Tri-Star)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Tri-Star"),
  makeCandlePattern("CDL_TWEEZER_BOTTOM", "Tweezer bottom (bullish)", "Tweezer+", "Đáy nhíp đôi đảo chiều tăng (Tweezer Bottom)", "bull", "STD;Candlestick%1Pattern%1Bullish%1Tweezer%1Bottom"),
  makeCandlePattern("CDL_TWEEZER_TOP", "Tweezer top (bearish)", "Tweezer-", "Đỉnh nhíp đôi đảo chiều giảm (Tweezer Top)", "bear", "STD;Candlestick%1Pattern%1Bearish%1Tweezer%1Top"),
  makeCandlePattern("CDL_UPSIDE_TASUKI", "Upside Tasuki gap (bullish)", "Tasuki+", "Khoảng trống Tasuki Tăng", "bull", "STD;Candlestick%1Pattern%1Bullish%1Upside%1Tasuki%1Gap"),
]

/* ----------------------------------------------------------------------------
 * 5. OPTIONS & VOLATILITY STUDIES (8 Ultimate Studies — 100% Unlocked)
 * ---------------------------------------------------------------------------- */
export const OPTIONS_STUDIES: TvIndicatorMetadata[] = [
  makeOptionStudy("OPT_EXP_MOVE", "Options Expected Move Cone (1σ / 2σ)", "Exp Move", "Nón biên độ kỳ vọng Quyền chọn (±1σ / ±2σ)", "Phóng chiếu hình nón xác suất biến động giá kỳ vọng 68% (±1σ) và 95% (±2σ) phía trước nến hiện tại.", true, "#2962FF", "ULTIMATE", [
    { key: "hvLen", label: "Chu kỳ Volatility", defaultValue: 20, min: 5, max: 100 },
    { key: "horizon", label: "Số phiên phóng chiếu", defaultValue: 14, min: 5, max: 40 },
  ]),
  makeOptionStudy("OPT_VOL_PROFILE", "Options Gamma & Volume Profile (GEX)", "Opt GEX", "Hồ sơ Thanh khoản & Gamma Exposure (GEX)", "Ước tính mật độ Gamma Exposure dương/âm tại các mốc giá thực hiện (Strike Levels) quanh thị giá.", true, "#8E24AA", "ULTIMATE", P_LEN(30, "Số bậc Strike")),
  makeOptionStudy("OPT_HV_IV", "Historical vs Implied Volatility", "HV vs IV", "So sánh Độ biến động Thực tế (HV) & Hàm ý (IV)", "Theo dõi chênh lệch Volatility Risk Premium giữa IV ước tính và HV lịch sử.", false, "#2962FF", "ULTIMATE", P_LEN(20)),
  makeOptionStudy("OPT_IV_RANK", "IV Rank & IV Percentile (IVR / IVP)", "IV Rank", "Thứ hạng Bách phân vị Biến động (IV Rank 0–100)", "Đánh giá biến động hiện tại đang đắt (> 80) hay rẻ (< 20) so với dải 52 tuần.", false, "#7E57C2", "PREMIUM", P_LEN(60, "Chu kỳ tham chiếu")),
  makeOptionStudy("OPT_SKEW", "Options Volatility Skew & Tail Risk", "Vol Skew", "Độ lệch đuôi phân phối Volatility Skew", "Đo độ bất đối xứng giữa biến động chiều giảm (Put Skew) và chiều tăng (Call Skew).", false, "#F4511E", "ULTIMATE", P_LEN(20)),
  makeOptionStudy("OPT_PCR", "Synthetic Put/Call Volume Ratio", "Put/Call", "Tỷ lệ áp lực Phòng vệ Put / Đầu cơ Call (PCR)", "Tỷ số áp lực phòng vệ giảm giá so với mua đầu cơ giá lên (PCR > 1.2 = Quá sợ hãi/Đáy gần).", false, "#00897B", "PRO", P_LEN(14)),
  makeOptionStudy("OPT_GARMAN_KLASS", "Garman-Klass Institutional Volatility", "GK Vol", "Độ biến động định chế Garman-Klass (OHLC)", "Công thức ước lượng biến động hiệu quả gấp 7.4 lần độ lệch chuẩn Close-to-Close truyền thống.", false, "#00ACC1", "PREMIUM", P_LEN(20)),
  makeOptionStudy("OPT_MAX_PAIN", "Max Pain & Strike Pinning Levels", "Max Pain", "Ngưỡng Giá Cân bằng Thanh khoản (Max Pain Magnet)", "Xác định mức giá trọng tâm hút thanh khoản mạnh nhất trong chu kỳ đáo hạn.", true, "#F59E0B", "ULTIMATE", P_LEN(22, "Chu kỳ đáo hạn")),
]

/* ----------------------------------------------------------------------------
 * 6. FUNDAMENTAL FINANCIAL METRICS (24 Corporate Financial Series — Cơ bản)
 * ---------------------------------------------------------------------------- */
export const FUNDAMENTAL_STUDIES: TvIndicatorMetadata[] = [
  // Valuation
  makeFundamental("FUND_PE", "Price to earnings ratio (P/E)", "P/E", "Hệ số Giá / Thu nhập (P/E Ratio)", "Định giá cổ phiếu theo bội số lợi nhuận sau thuế 4 quý gần nhất (TTM).", "valuation", "#2962FF"),
  makeFundamental("FUND_PB", "Price to book ratio (P/B)", "P/B", "Hệ số Giá / Giá trị sổ sách (P/B Ratio)", "So sánh thị giá với giá trị tài sản ròng trên mỗi cổ phần.", "valuation", "#00897B"),
  makeFundamental("FUND_PEG", "PEG ratio", "PEG", "Hệ số P/E trên Tăng trưởng EPS (PEG Ratio)", "Đánh giá định giá P/E có hợp lý so với tốc độ tăng trưởng lợi nhuận hay không (< 1 là hấp dẫn).", "valuation", "#7E57C2"),
  makeFundamental("FUND_PS", "Price to sales ratio (P/S)", "P/S", "Hệ số Vốn hóa / Doanh thu thuần (P/S)", "Đo lường mức định giá trên mỗi đồng doanh thu tạo ra.", "valuation", "#00ACC1"),
  makeFundamental("FUND_EV_EBITDA", "Enterprise value to EBITDA", "EV/EBITDA", "Hệ số Giá trị doanh nghiệp / EBITDA", "Chỉ số định giá chuẩn M&A loại bỏ khác biệt cấu trúc nợ vay và khấu hao.", "valuation", "#3949AB"),
  makeFundamental("FUND_MCAP", "Market capitalization", "Mkt Cap", "Vốn hóa thị trường (Tỷ VNĐ)", "Tổng giá trị thị trường của lượng cổ phiếu đang lưu hành theo biến động giá.", "valuation", "#1E88E5"),
  // Income Statement
  makeFundamental("FUND_EPS", "EPS diluted (TTM)", "EPS", "Thu nhập trên mỗi cổ phần (EPS TTM - VNĐ)", "Lợi nhuận ròng phân bổ trên mỗi cổ phiếu phổ thông trong 12 tháng trượt.", "income", "#089981"),
  makeFundamental("FUND_EPS_GROWTH", "EPS diluted growth YoY %", "EPS Gr %", "Tăng trưởng EPS cùng kỳ (% YoY)", "Tốc độ tăng trưởng lợi nhuận trên mỗi cổ phiếu so với cùng kỳ năm trước.", "income", "#43A047"),
  makeFundamental("FUND_REVENUE", "Total revenue", "Revenue", "Tổng Doanh thu thuần (Tỷ VNĐ)", "Quy mô doanh thu bán hàng và cung cấp dịch vụ qua các kỳ báo cáo.", "income", "#2962FF"),
  makeFundamental("FUND_REV_GROWTH", "Revenue growth YoY %", "Rev Gr %", "Tăng trưởng Doanh thu (% YoY)", "Tốc độ mở rộng doanh thu cốt lõi qua từng quý.", "income", "#00BFA5"),
  makeFundamental("FUND_GROSS_MARGIN", "Gross margin %", "Gross Mgn", "Biên lợi nhuận gộp (%)", "Phản ánh lợi thế cạnh tranh giá bán và kiểm soát giá vốn hàng bán.", "income", "#FB8C00"),
  makeFundamental("FUND_OP_MARGIN", "Operating margin %", "Op Margin", "Biên lợi nhuận hoạt động EBIT (%)", "Hiệu quả sinh lời từ hoạt động kinh doanh cốt lõi sau chi phí bán hàng & quản lý.", "income", "#F4511E"),
  makeFundamental("FUND_NET_MARGIN", "Net margin %", "Net Mgn", "Biên lợi nhuận ròng sau thuế (%)", "Tỷ lệ lợi nhuận giữ lại cuối cùng trên mỗi 100 đồng doanh thu.", "income", "#8E24AA"),
  // Profitability & Balance Sheet
  makeFundamental("FUND_ROE", "Return on equity % (ROE)", "ROE %", "Tỷ suất sinh lời trên Vốn chủ sở hữu (ROE %)", "Thước đo vàng đánh giá hiệu quả sử dụng vốn của cổ đông (> 15% là chất lượng cao).", "balance", "#089981"),
  makeFundamental("FUND_ROA", "Return on assets % (ROA)", "ROA %", "Tỷ suất sinh lời trên Tổng tài sản (ROA %)", "Hiệu suất tạo lợi nhuận trên toàn bộ quy mô tài sản doanh nghiệp.", "balance", "#00897B"),
  makeFundamental("FUND_ROIC", "Return on invested capital % (ROIC)", "ROIC %", "Tỷ suất sinh lời trên Vốn đầu tư (ROIC %)", "Đo lường hiệu quả phân bổ vốn thực tế của ban lãnh đạo.", "balance", "#2962FF"),
  makeFundamental("FUND_TOTAL_ASSETS", "Total assets", "Assets", "Tổng Tài sản (Tỷ VNĐ)", "Quy mô tổng tài sản trên bảng cân đối kế toán.", "balance", "#5C6BC0"),
  makeFundamental("FUND_TOTAL_DEBT", "Total debt", "Debt", "Tổng Nợ vay tài chính (Tỷ VNĐ)", "Tổng nợ vay ngắn hạn và dài hạn chịu lãi của doanh nghiệp.", "balance", "#E53935"),
  makeFundamental("FUND_DEBT_EQUITY", "Debt to equity ratio (D/E)", "D/E", "Tỷ lệ Nợ vay / Vốn chủ sở hữu (D/E)", "Đo đòn bẩy tài chính và mức độ an toàn bảng cân đối.", "balance", "#D81B60"),
  makeFundamental("FUND_CURRENT_RATIO", "Current ratio", "Curr Ratio", "Hệ số Thanh toán hiện hành (Current Ratio)", "Khả năng đáp ứng nghĩa vụ nợ ngắn hạn bằng tài sản lưu động.", "balance", "#00ACC1"),
  makeFundamental("FUND_BVPS", "Book value per share (BVPS)", "BVPS", "Giá trị sổ sách mỗi cổ phiếu (BVPS - VNĐ)", "Giá trị tài sản thuần trên 1 cổ phần.", "balance", "#43A047"),
  // Cash Flow & Dividends
  makeFundamental("FUND_FCF", "Free cash flow (FCF)", "FCF", "Dòng tiền tự do (Free Cash Flow - Tỷ VNĐ)", "Tiền mặt thực dương còn lại sau khi trừ chi phí đầu tư tài sản cố định CAPEX.", "cashflow", "#089981"),
  makeFundamental("FUND_OCF", "Operating cash flow (OCF)", "OCF", "Lưu chuyển tiền thuần từ HĐKD (OCF)", "Dòng tiền thực thu từ hoạt động kinh doanh chính.", "cashflow", "#2962FF"),
  makeFundamental("FUND_DIV_YIELD", "Dividend yield %", "Div Yield", "Tỷ suất Cổ tức tiền mặt (% / năm)", "Tỷ lệ cổ tức tiền mặt nhận được hàng năm so với thị giá cổ phiếu.", "cashflow", "#F59E0B"),
]

/* ----------------------------------------------------------------------------
 * 7. COMMUNITY, EDITOR'S PICKS, TOP, TRENDING, STORE & PURCHASED VIP (38 Scripts)
 * ---------------------------------------------------------------------------- */
export const COMMUNITY_AND_VIP_STUDIES: TvIndicatorMetadata[] = [
  // Biên tập viên chọn (Editors' Picks — extracted directly from REA editors-picks.json)
  makeCommunityScript("COM_REDK_VACCI", "RedK DualTF Volume-Accelerated CCI (RedK VA-CCI)", "VA-CCI", "CCI Gia tốc Khối lượng Đa khung (RedK VA-CCI)", "Kết hợp CCI gia tốc theo thanh khoản trên 2 khung thời gian giúp phát hiện dòng tiền lớn vào sóng sớm.", "editors_picks", "RedKTrader", 798, false, "#00897B", "PRO", P_LEN(20)),
  makeCommunityScript("COM_ZEIIERMAN_HTF", "Higher Timeframe Trend Frontier (Zeiierman)", "HTF Frontier", "Biên giới Xu hướng Khung lớn (Zeiierman)", "Vẽ dải biên xu hướng động của khung thời gian lớn kèm vùng đệm hỗ trợ/kháng cự định chế.", "editors_picks", "Zeiierman", 1050, true, "#089981", "PRO", P_LEN(34)),
  makeCommunityScript("COM_ROLLING_PROFILE", "Rolling Candle Profile with Buy/Sell Signals (Zeiierman)", "Rolling VP", "Hồ sơ Thanh khoản Cuộn kèm Tín hiệu Mua/Bán", "Tích hợp Volume Profile cuộn động với điểm nổ thanh khoản Breakout trên biểu đồ giá.", "editors_picks", "Zeiierman", 4450, true, "#2962FF", "ULTIMATE", P_LEN(30)),
  makeCommunityScript("COM_KALMAN_OSC", "Innovation Adaptive Kalman Filter Oscillator", "Kalman Osc", "Bộ lọc thích ứng Kalman Oscillator [jdehorty]", " Ứng dụng bộ lọc trạng thái Kalman hàng không vũ trụ để tách tín hiệu động lượng sạch không trễ.", "editors_picks", "jdehorty", 1435, false, "#7E57C2", "PRO", P_LEN(14), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeCommunityScript("COM_BUYERS_SELLERS_SR", "Buyers & Sellers Profile + Dynamic S/R (Zeiierman)", "Buy/Sell SR", "Hồ sơ Phe Mua & Phe Bán + Kháng cự/Hỗ trợ Động", "Phân tách vùng kiểm soát của phe Mua/Bán và kẻ các vùng cản thanh khoản tự động.", "editors_picks", "Zeiierman", 8518, true, "#00BFA5", "PREMIUM", P_LEN(40)),
  makeCommunityScript("COM_TREND_RIBBON_BOS", "Trend Target Ribbon [BOSWaves]", "BOS Ribbon", "Dải băng Xu hướng & Mục tiêu Chốt lời [BOSWaves]", "Dải băng đổi màu theo cấu trúc Break of Structure kèm các mức chốt lời ATR.", "editors_picks", "BOSWaves", 6771, true, "#2962FF", "PRO", P_LEN(21)),
  makeCommunityScript("COM_WYCKOFF", "Wyckoff Accumulation & Distribution [theUltimator5]", "Wyckoff", "Nhận diện Pha Tích lũy / Phân phối Wyckoff (Spring / UTAD)", "Tự động đóng khung vùng Trading Range Wyckoff và gắn nhãn điểm rũ bỏ Spring / Upthrust.", "editors_picks", "TheUltimator5", 8301, true, "#8E24AA", "ULTIMATE", P_LEN(45)),
  makeCommunityScript("COM_DGT_REVERSAL", "Dual-Phase Reversal by DGT", "DGT Rev", "Kênh Đảo chiều Kép 2 Pha [dgtrd]", "Kết hợp dải Keltner và phân kỳ động lượng để bắt điểm đảo chiều sớm.", "editors_picks", "dgtrd", 2062, true, "#FB8C00", undefined, P_LEN(20)),
  makeCommunityScript("COM_DELTA_FOOTPRINT_MAP", "Volume Delta Footprint Map (Zeiierman)", "Delta Map", "Bản đồ Nhiệt Volume Delta Footprint", "Hiển thị vùng mất cân bằng cung cầu (Orderflow Imbalance) trực tiếp quanh thân nến.", "editors_picks", "Zeiierman", 2817, true, "#00ACC1", "ULTIMATE", P_LEN(20)),
  makeCommunityScript("COM_IB_AUCTION", "Initial Balance Auction Intelligence by DGT", "IB Auction", "Vùng Cân bằng Đầu phiên (Initial Balance Auction)", "Xác định biên độ cân bằng đầu chu kỳ (IB High / IB Low) và các mức mở rộng đấu giá 1.5x, 2x.", "editors_picks", "dgtrd", 2140, true, "#3949AB", "PRO", P_LEN(15)),

  // Hàng đầu (Top All-Time TradingView Scripts)
  makeCommunityScript("COM_SQUEEZE_LAZYBEAR", "Squeeze Momentum Indicator [LazyBear]", "SqzMom", "Chỉ báo Nén Động lượng Kinh điển [LazyBear]", "Chỉ báo số #1 lịch sử TradingView: phát hiện khi Bollinger nằm trọn trong Keltner (Squeeze On) trước cú nổ lớn.", "top", "LazyBear", 104520, false, "#089981", undefined, P_LEN(20), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: false }] }),
  makeCommunityScript("COM_SMC_LUXALGO", "Smart Money Concepts (SMC) [LuxAlgo]", "SMC", "Hệ thống Dòng tiền Thông minh SMC (BOS / CHoCH / Order Blocks / FVG)", "Tự động vẽ cấu trúc BOS, CHoCH, khối lệnh định chế Order Blocks (OB) và khoảng trống thanh khoản FVG.", "top", "LuxAlgo", 89400, true, "#089981", "ULTIMATE", [{ key: "swingLen", label: "Chu kỳ Cấu trúc Swing", defaultValue: 6, min: 3, max: 25 }]),
  makeCommunityScript("COM_MACD_ULT_CM", "CM_MacD_Ult_MTF [ChrisMoody]", "CM MACD", "MACD Đa Sắc Thái & Tín hiệu Giao cắt [ChrisMoody]", "MACD 4 màu xung lượng kèm chấm tròn xanh/đỏ trực quan ngay tại điểm giao cắt đường Signal.", "top", "ChrisMoody", 74200, false, "#2962FF", undefined, P_FAST_SLOW(12, 26), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: true }] }),
  makeCommunityScript("COM_WAVETREND", "WaveTrend Oscillator [WT LazyBear]", "WaveTrend", "Dao động Sóng WaveTrend [LazyBear]", "Chỉ báo bắt đỉnh/đáy cực nhạy với vùng quá mua (+60) và quá bán (-60).", "top", "LazyBear", 63800, false, "#089981", undefined, P_FAST_SLOW(10, 21), { secondaryColor: "#F23645", referenceLevels: [{ value: 60, color: "#F23645", label: "+60", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: true }, { value: -60, color: "#089981", label: "-60", dashed: true }] }),
  makeCommunityScript("COM_UT_BOT", "UT Bot Alerts [QuantNomad]", "UT Bot", "Tín hiệu Mua/Bán tự động UT Bot Alerts", "Đường ATR Trailing nhạy bén gắn nhãn BUY / SELL rõ nét trên biểu đồ nến.", "top", "QuantNomad", 52900, true, "#089981", "PRO", [
    { key: "keyVal", label: "Độ nhạy Key Value", defaultValue: 2, min: 0.5, max: 6, step: 0.5 },
    { key: "atrPeriod", label: "Chu kỳ ATR", defaultValue: 10, min: 2, max: 50 },
  ]),
  makeCommunityScript("COM_VUMANCHU_B", "VuManChu Cipher B + Divergences", "Cipher B", "Tổ hợp Sóng Dòng tiền VuManChu Cipher B", "Kết hợp sóng động lượng WaveTrend, dòng tiền MFI Area và chấm tín hiệu đảo chiều.", "top", "vumanchu", 49100, false, "#2962FF", "ULTIMATE", P_FAST_SLOW(9, 12), { secondaryColor: "#089981", referenceLevels: [{ value: 60, color: "#F23645", label: "60", dashed: true }, { value: 0, color: "#787B8655", label: "0", dashed: false }, { value: -60, color: "#089981", label: "-60", dashed: true }] }),
  makeCommunityScript("COM_SSL_HYBRID", "SSL Hybrid [Mihkel00]", "SSL Hybrid", "Kênh xu hướng SSL Hybrid & Baseline HMA", "Kết hợp đường cơ sở HMA không trễ với kênh giao cắt High/Low SSL.", "top", "Mihkel00", 38700, true, "#00BCD4", undefined, P_LEN(60, "Baseline HMA")),
  makeCommunityScript("COM_WADDAH_ATTAR", "Waddah Attar Explosion v2 [SHK]", "WAE", "Chỉ báo Bùng nổ Xu hướng Waddah Attar Explosion", "Kết hợp Histogram MACD với ngưỡng nổ dải Bollinger giúp tránh hoàn toàn vùng Sideway.", "top", "SHK", 34600, false, "#089981", undefined, P_LEN(20), { secondaryColor: "#FB8C00" }),
  makeCommunityScript("COM_MADRID_RIBBON", "Madrid Moving Average Ribbon", "MMAR", "Dải Cầu vồng 8 đường MA Madrid Ribbon", "Hiển thị dải 8 đường MA từ chu kỳ 10 đến 80 đổi màu đồng bộ theo sức mạnh sóng.", "top", "Madrid", 31200, true, "#089981", undefined, P_LEN(10, "Bước MA cơ sở")),
  makeCommunityScript("COM_CORAL_TREND", "Coral Trend Indicator [LazyBear]", "Coral", "Đường xu hướng bậc ba Coral Trend [LazyBear]", "Bộ lọc EMA bậc 6 cực mượt đổi màu xanh/đỏ rõ ràng theo pha thị trường.", "top", "LazyBear", 29500, true, "#089981", undefined, P_LEN(21)),

  // Xu hướng (Trending Now)
  makeCommunityScript("COM_LORENTZIAN_ML", "Machine Learning: Lorentzian Classification [jdehorty]", "ML Lorentz", "Phân loại Học máy Không gian Lorentzian (ML Signals)", "Mô hình k-NN khoảng cách Lorentzian đa đặc trưng (RSI, CCI, ADX, WT) dự báo xác suất đảo chiều.", "trending", "jdehorty", 46800, true, "#00BFA5", "ULTIMATE", P_LEN(14, "Chu kỳ đặc trưng ML")),
  makeCommunityScript("COM_NADARAYA_WATSON", "Nadaraya-Watson Envelope [LuxAlgo]", "NW Env", "Dải Hồi quy Nhân Nadaraya-Watson Envelope [LuxAlgo]", "Dải biên làm mượt Kernel Gaussian kèm tín hiệu chạm biên đảo chiều cực mượt.", "trending", "LuxAlgo", 41200, true, "#089981", "PREMIUM", [
    { key: "length", label: "Cửa sổ Kernel", defaultValue: 50, min: 15, max: 150 },
    { key: "mult", label: "Hệ số biên ATR", defaultValue: 2.5, min: 1, max: 5, step: 0.25 },
  ]),
  makeCommunityScript("COM_ICT_FVG_LIQ", "Liquidity Swings & Fair Value Gaps (ICT)", "ICT FVG", "Vùng Thanh khoản & Khoảng trống Hợp lý ICT (FVG + BSL/SSL)", "Tự động tô các ô Fair Value Gap (Bullish/Bearish FVG) chưa lấp và các mốc quét thanh khoản.", "trending", "LuxAlgo", 27900, true, "#2962FF", "PRO", [{ key: "maxZones", label: "Số vùng FVG tối đa", defaultValue: 8, min: 2, max: 20 }]),
  makeCommunityScript("COM_SUPERTREND_AI", "SuperTrend AI (Clustering) [LuxAlgo]", "ST AI", "SuperTrend Trí tuệ Nhân tạo (K-Means Clustering)", "Tự động tối ưu hệ số ATR bằng phân cụm hiệu suất thực tế trên dữ liệu cổ phiếu.", "trending", "LuxAlgo", 35400, true, "#089981", "ULTIMATE", P_LEN(10, "Chu kỳ ATR AI")),
  makeCommunityScript("COM_ORDER_BLOCKS", "Order Blocks & Breaker Blocks [LuxAlgo]", "OB/BB", "Khối lệnh Định chế Order Blocks & Breaker Blocks", "Xác định các vùng cầu/cung định chế nơi dòng tiền lớn đặt lệnh trước nhịp bứt phá.", "trending", "LuxAlgo", 33100, true, "#089981", "PREMIUM", P_LEN(7, "Độ sâu Swing OB")),
  makeCommunityScript("COM_MSB_OB", "Market Structure Break & Order Block (MSB-OB)", "MSB-OB", "Phá vỡ Cấu trúc Thị trường & Khối lệnh (MSB-OB)", "Đánh dấu trực quan điểm gãy cấu trúc MSB và vùng Retest tối ưu.", "trending", "EmreKb", 22400, true, "#7E57C2", "PRO", P_LEN(8)),
  makeCommunityScript("COM_VCP_MINERVINI", "Volatility Contraction Pattern (VCP Mark Minervini)", "VCP", "Mô hình Thu hẹp Biến động VCP (Mark Minervini SEPA)", "Kiểm tra điều kiện Trend Template MA50>MA150>MA200 và các nhịp thu hẹp biên độ VCP.", "trending", "PatternAlpha", 18900, true, "#2962FF", "PRO", P_LEN(20)),
  makeCommunityScript("COM_TURTLE_CHANNELS", "Turtle Trade Channels & ATR Stops", "Turtle", "Hệ thống Kênh Giao dịch Rùa (Turtle Channels 20/10)", "Kênh vào lệnh 20 phiên kết hợp đường thoát lệnh nhanh 10 phiên của huyền thoại Richard Dennis.", "trending", "QuantNomad", 16200, true, "#00897B", undefined, P_FAST_SLOW(10, 20)),

  // Chợ (Store — Extracted from REA script-store.json, 100% Unlocked!)
  makeCommunityScript("STORE_SUPPLY_DEMAND", "Supply & Demand Price Action Toolkit", "S&D Pro", "Bộ công cụ Vùng Cung – Cầu Hành động giá Chuyên sâu", "Tự động vẽ vùng Supply (Bán định chế) & Demand (Mua định chế) kèm độ mạnh vùng cản.", "store", "AfnanTAjuddin", 9800, true, "#089981", "ULTIMATE", P_LEN(10)),
  makeCommunityScript("STORE_TRENDPRO", "TrendPro Suite — Find Your Edge", "TrendPro", "Tổ hợp Bắt sóng Định chế TrendPro Suite", "Kết hợp dải xu hướng thích ứng, lọc nhiễu ADX và điểm mua sớm Pullback.", "store", "LevelUpTools", 11200, true, "#2962FF", "ULTIMATE", P_LEN(18)),
  makeCommunityScript("STORE_BIGBELUGA", "BigBeluga Smart Money Premium", "BigBeluga", "Hệ thống Thanh khoản Cá Voi BigBeluga Premium", "Theo dõi dấu chân cá mập qua cụm thanh khoản tích lũy và kênh xu hướng động.", "store", "BigBeluga", 14500, true, "#00ACC1", "ULTIMATE", P_LEN(24)),
  makeCommunityScript("STORE_AI_EDGE", "AI Edge Institutional Composite", "AI Edge", "Chỉ số Hợp lưu AI Edge Định chế [jdehorty]", "Tổng hợp động lượng chuẩn hóa đa tầng của dòng tiền tổ chức trên thang -100 đến +100.", "store", "jdehorty", 12900, false, "#7E57C2", "ULTIMATE", P_LEN(14), { fixedRange: [-100, 100], referenceLevels: [{ value: 50, color: "#089981", label: "+50", dashed: true }, { value: 0, color: "#787B86", label: "0", dashed: true }, { value: -50, color: "#F23645", label: "-50", dashed: true }] }),
  makeCommunityScript("STORE_DEMARK_9_13", "DeMARK 9-13 TD Sequential Ultimate", "TD 9-13", "Đếm nhịp Cạn kiệt Xu hướng DeMARK TD Sequential 9–13", "Tự động đếm nhịp Setup 1..9 và Countdown 1..13 phát hiện chính xác điểm đảo chiều kiệt sức.", "store", "DeMARK", 19400, true, "#F59E0B", "ULTIMATE", [{ key: "lookback", label: "Độ lệch so sánh nến (TD=4)", defaultValue: 4, min: 2, max: 10 }]),
  makeCommunityScript("STORE_PATTERNS_PRO", "Chart Patterns Pro (incl. Scanner)", "Patterns Pro", "Máy quét Đa Mô hình Giá & Mục tiêu Chốt lời Tự động", "Bản mở rộng chuyên nghiệp tự động quét mô hình giá, kẻ vùng Entry, Stop Loss và Target.", "store", "TRN-Trading", 8700, true, "#2962FF", "ULTIMATE", [{ key: "pivotLen", label: "Độ nhạy Scanner", defaultValue: 5, min: 2, max: 15 }]),

  // Đã mua (Purchased VIP Vault — Pre-unlocked for AIInvest User!)
  makeCommunityScript(
    "VIP_HUD_DE_TE",
    "HuD DE | TE [Hud_Hud]",
    "[HuD] DE | TE",
    "Hệ thống Chuyên gia Phân kỳ & Xu hướng Homily (HuD DE | TE V2)",
    "Bản Invite-Only Homily (JOaQvb2o) tích hợp 2 chế độ: DE (Deviation Expert — bắt phân kỳ chênh lệch giá & săn siêu cổ phiếu vượt 0) và TE (Trend Expert — nến xu hướng kèm 4 đường EMA hỗ trợ/kháng cự động).",
    "purchased",
    "Hud_Hud",
    892,
    false,
    "#00E676",
    "ULTIMATE",
    [
      {
        key: "mode",
        label: "Expert Option",
        defaultValue: 0,
        min: 0,
        max: 1,
        options: [
          { value: 0, label: "DE — Deviation Expert" },
          { value: 1, label: "TE — Trend Expert" },
        ],
      },
      { key: "fastLen", label: "Chu kỳ cơ sở nhanh (Fast EMA)", defaultValue: 5, min: 2, max: 100 },
      { key: "slowLen", label: "Chu kỳ cơ sở chậm DE (Slow EMA)", defaultValue: 20, min: 5, max: 200 },
      { key: "ema1", label: "Đường hỗ trợ EMA 1", defaultValue: 5, min: 2, max: 100 },
      { key: "ema2", label: "Đường hỗ trợ EMA 2", defaultValue: 10, min: 3, max: 150 },
      { key: "ema3", label: "Đường hỗ trợ EMA 3", defaultValue: 20, min: 5, max: 200 },
      { key: "ema4", label: "Đường Swing EMA 4 (Tím)", defaultValue: 50, min: 10, max: 300 },
    ],
    {
      tvScriptId: "PUB;JOaQvb2o",
      allowMultiple: true,
      referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: false }],
    }
  ),
  makeCommunityScript(
    "VIP_HUD_DE",
    "HuD DE — Deviation Expert [Hud_Hud]",
    "[HuD] DE",
    "Chuyên gia Độ lệch & Săn Cổ phiếu Mạnh Homily (HuD DE V2)",
    "Chế độ DE độc lập của HuD DE | TE (JOaQvb2o): Nến độ lệch EMA(5)–EMA(20), trục cân bằng 0 (◆ Kim cương vượt 0), 3 đường EMA 5/10/20 chấm tròn đổi màu & EMA 50 tím.",
    "purchased",
    "Hud_Hud",
    892,
    false,
    "#00E676",
    "ULTIMATE",
    [
      { key: "fastLen", label: "Chu kỳ nhanh (Fast EMA)", defaultValue: 5, min: 2, max: 100 },
      { key: "slowLen", label: "Chu kỳ chậm (Slow EMA)", defaultValue: 20, min: 5, max: 200 },
      { key: "ema1", label: "Đường hỗ trợ EMA 1", defaultValue: 5, min: 2, max: 100 },
      { key: "ema2", label: "Đường hỗ trợ EMA 2", defaultValue: 10, min: 3, max: 150 },
      { key: "ema3", label: "Đường hỗ trợ EMA 3", defaultValue: 20, min: 5, max: 200 },
      { key: "ema4", label: "Đường Swing EMA 4 (Tím)", defaultValue: 50, min: 10, max: 300 },
    ],
    {
      tvScriptId: "PUB;JOaQvb2o#DE",
      referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: false }],
    }
  ),
  makeCommunityScript(
    "VIP_HUD_TE",
    "HuD TE — Trend Expert [Hud_Hud]",
    "[HuD] TE",
    "Chuyên gia Bám sóng Xu hướng Homily (HuD TE V2)",
    "Chế độ TE độc lập của HuD DE | TE (JOaQvb2o): Nến xu hướng EMA(5) kết hợp dải 3 đường kháng cự/hỗ trợ ngắn hạn EMA 5/10/20 chấm tròn đổi màu và đường Swing EMA 50 tím.",
    "purchased",
    "Hud_Hud",
    892,
    false,
    "#00E676",
    "ULTIMATE",
    [
      { key: "fastLen", label: "Chu kỳ nến xu hướng (TE EMA)", defaultValue: 5, min: 2, max: 100 },
      { key: "ema1", label: "Đường hỗ trợ EMA 1", defaultValue: 5, min: 2, max: 100 },
      { key: "ema2", label: "Đường hỗ trợ EMA 2", defaultValue: 10, min: 3, max: 150 },
      { key: "ema3", label: "Đường hỗ trợ EMA 3", defaultValue: 20, min: 5, max: 200 },
      { key: "ema4", label: "Đường Swing EMA 4 (Tím)", defaultValue: 50, min: 10, max: 300 },
    ],
    {
      tvScriptId: "PUB;JOaQvb2o#TE",
    }
  ),
  makeCommunityScript("VIP_ALGOALPHA_FLOW", "AlgoAlpha Institutional Orderflow Suite", "Orderflow VIP", "Tổ hợp Dòng lệnh Định chế AlgoAlpha VIP", "Phân tích áp lực dòng lệnh tổ chức kết hợp dải băng động lượng đa chu kỳ (Đã mở khóa VIP).", "purchased", "AlgoAlpha", 15600, true, "#00BFA5", "ULTIMATE", P_LEN(21)),
  makeCommunityScript("VIP_QUANTVUE_MATRIX", "QuantVue Pro Momentum & Liquidity Matrix", "QuantVue", "Ma trận Động lượng & Thanh khoản QuantVue Pro", "Dao động đa tầng kết hợp dòng tiền MFI, RSI và xung lượng WaveTrend chuẩn Quỹ.", "purchased", "QuantVue", 11800, false, "#2962FF", "ULTIMATE", P_LEN(14), { referenceLevels: [{ value: 60, color: "#F23645", label: "60", dashed: true }, { value: -60, color: "#089981", label: "-60", dashed: true }] }),
  makeCommunityScript("VIP_MARKET_CIPHER_AB", "Market Cipher A + B Institutional Full Suite", "Cipher A", "Hệ thống Tín hiệu Market Cipher A trên Biểu đồ Giá", "Gắn kim cương máu (Blood Diamond), chấm xanh đảo chiều và dải EMA Ribbon trực tiếp lên nến.", "purchased", "MarketCipher", 24300, true, "#089981", "ULTIMATE", P_LEN(14)),
  makeCommunityScript("VIP_HOSE_SMART_FLOW", "HOSE Smart Foreign & Proprietary Flow Tracker", "HOSE Flow", "Dòng tiền Khối ngoại & Tự doanh Ước tính (HOSE VIP)", "Đo xung lượng gom ròng/xả ròng của dòng tiền lớn trên cổ phiếu Việt Nam qua đột biến giá–khối lượng.", "purchased", "AIInvest Research", 9900, false, "#089981", "ULTIMATE", P_LEN(15), { referenceLevels: [{ value: 0, color: "#787B86", label: "0", dashed: false }] }),
]

export const ALL_CATALOG_INDICATORS: TvIndicatorMetadata[] = [
  ...BUILTIN_TECHNICAL_STUDIES,
  ...BUILTIN_STRATEGIES,
  ...PROFILE_STUDIES,
  ...AUTO_CHART_PATTERNS,
  ...CANDLESTICK_PATTERNS,
  ...OPTIONS_STUDIES,
  ...FUNDAMENTAL_STUDIES,
  ...COMMUNITY_AND_VIP_STUDIES,
]
