# AIInvest Documentation &amp; Architecture Portal
> Trung tâm tài liệu kỹ thuật, kiến trúc hệ thống công nghệ thông tin và thư viện sơ đồ đồ họa của dự án AIInvest.

---

## 1. Cấu Trúc Tài Liệu (Documentation Hierarchy)

```
docs/
├── README.md                                # Master Portal điều hướng toàn bộ docs & diagrams
├── architecture/                            # Chuyên mục tài liệu kiến trúc hệ thống CNTT
│   ├── IT_SYSTEM_ARCHITECTURE.md            # Đặc tả chi tiết kiến trúc CNTT 5 tầng & mô hình bảo mật
│   ├── ALGORITHMS_AND_FINANCIAL_MODELS.md   # Đặc tả toán học, thuật toán & mô hình định lượng (Beneish, Kelly, HOSE T+2.5)
│   └── PROJECT_MEMORY.md                    # Living Project Memory & Quy tắc cốt lõi của Monorepo
│
└── diagrams/                                # Thư mục chuyên biệt chứa sơ đồ trực quan (HTML/SVG)
    ├── README.md                            # Hướng dẫn tra cứu & tiêu chuẩn kỹ thuật sơ đồ
    └── paper-grade-algorithmic-data-flow.html # Sơ đồ luồng biến đổi dữ liệu & thuật toán chuẩn khoa học (Interactive Step Motion & Inspector Drawer)
```

---

## 2. Liên Kết Nhanh (Quick Links)

### Tài Liệu Kiến Trúc & Thuật Toán Hệ Thống (Architecture & Algorithms Docs)
- [IT_SYSTEM_ARCHITECTURE.md](file:///d:/AIInvest/docs/architecture/IT_SYSTEM_ARCHITECTURE.md): Phân tích chi tiết 5 tầng công nghệ (Presentation, API Gateway, Quant Org Core, SAG Forensics, Persistence), phân tách vùng mạng và quy trình xử lý BCTC.
- [ALGORITHMS_AND_FINANCIAL_MODELS.md](file:///d:/AIInvest/docs/architecture/ALGORITHMS_AND_FINANCIAL_MODELS.md): Đặc tả chi tiết toán học: Beneish M-Score (M8/M5), Sloan Accruals (Le & Tran 2022), Rủi ro thanh toán sàn HOSE T+2.5 (13.51%), Quy mô vị thế Half/Quarter-Kelly theo Market Regime, Hard Laws Điều 1, 2, 4, và Chuỗi băm bất biến CIO.
- [PROJECT_MEMORY.md](file:///d:/AIInvest/docs/architecture/PROJECT_MEMORY.md): Bộ nhớ kiến trúc hệ thống, danh mục trách nhiệm từng thư mục và các quy chuẩn chống suy đoán.

### Thư Viện Sơ Đồ Trực Quan (Interactive Diagrams Hub)
Được thiết kế theo tiêu chuẩn editorial của `/diagram-design` và meta-skill `scientific-diagram-prompt-crafter`:
- [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html): Luồng biến đổi thuật toán & dữ liệu chuẩn NeurIPS/SIGMOD (Bố cục 2 pha Offline/Online, băm dòng `quote_hash` SHA-256, chiếu siêu đồ thị $M:N$, không gian vector 1024 chiều, bộ lọc ngưỡng sàn động, dồn ứ hàng đợi Queue Accumulator, điều khiển Step Motion và bảng khảo sát chi tiết Inspector Drawer).
- [docs/diagrams/README.md](file:///d:/AIInvest/docs/diagrams/README.md): Hướng dẫn chi tiết mở, tương tác và kiểm thử sơ đồ.
