# 📊 AIInvest FE - Architecture Gap Analysis & BE Requirements

Tài liệu này phân tích khoảng cách giữa giao diện hiện tại (v1.0-UI) và hệ thống thực tế (Production-Ready), đồng thời liệt kê chi tiết các phần đang dùng dữ liệu ảo và các chức năng chưa hoàn thiện để làm căn cứ phát triển Backend.

---

## 1. Audit Dữ liệu ảo (Mock Data Areas)
Các phần dưới đây hiện đang hiển thị số liệu "fake" từ Store hoặc trực tiếp trong Code, cần thay thế bằng API thực tế:

### A. Dashboard & Market Data
- **Chỉ số Index (Header):** VN-Index, HNX-Index... đang dùng mảng static trong `useMarketStore`.
- **Bảng điện (MarketTable):** Chỉ có 4-5 mã mẫu (VNM, FPT, VIC, SSI). Cần API trả về toàn bộ mã theo sàn (HOSE, HNX, UPCOM).
- **Thống kê tổng (Table Footer):** Khối lượng (Vol) và Giá trị giao dịch (Value) của VN-Index đang ghi cứng (21,450.2B).
- **Độ rộng thị trường (Sidebar):** Số mã Tăng/Giảm/Tham chiếu đang dùng số ảo (245 Tăng, 92 Giảm).
- **So sánh thanh khoản:** Biểu đồ so sánh hôm nay/hôm qua đang dùng dữ liệu sinh ngẫu nhiên.
- **Bản đồ nhiệt (Heatmap):** Các ô màu sắc đang được gán cứng, không phản ánh đúng tỷ trọng vốn hóa thực.

### B. Market Screener
- **Chỉ số tài chính:** Các cột P/E, P/B, ROE, D/E, RSI trong bảng lọc đang dùng số cứng (ví dụ: P/E luôn là 14.5, ROE luôn là 22.4%).
- **Thanh trượt (Sliders):** Giá trị hiển thị trên thanh trượt (P/E: 15, RSI: 30-70) chỉ là Text, không khớp với dữ liệu trong bảng.
- **Kết quả tìm kiếm:** Hiện tại trang này luôn hiển thị 100% số mã có trong store, không thực hiện lọc thực tế.

### C. Chi tiết cổ phiếu (Stock Detail)
- **Sổ lệnh (Order Book/DOM):** 20 mức giá mua/bán đang dùng hàm `Math.random()` để tự sinh số liệu mỗi lần load.
- **Khớp lệnh (Tape):** Danh sách các lệnh khớp gần đây được sinh tự động qua `setInterval` sau mỗi 800ms.
- **Tin tức & AI Insight:** Nội dung "Aura Consensus" và danh sách tin tức liên quan là các đoạn Text tĩnh.
- **Thông số cơ bản:** EPS, P/E, P/B, ROE, Dividend... truyền vào component dưới dạng Props cứng.

### D. Quản lý danh mục (Portfolio)
- **Tổng tài sản (NAV):** Các con số 1.2 tỷ, Sức mua 450 triệu... được ghi cứng trong `usePortfolioStore`.
- **Hiệu suất (Performance Chart):** Biểu đồ đường Equity Curve đang dùng mảng dữ liệu mẫu.

---

| Vị trí | Element | Trạng thái hiện tại |
| :--- | :--- | :--- |
| **Header Chung** | Thanh tìm kiếm (Global Search) | Chỉ lưu giá trị nhập, chưa có logic gợi ý mã (AutoComplete) hay chuyển trang. |
| **Sidebar** | Nút Settings & Logout | Đang trỏ về `#`, chưa có trang cấu hình tài khoản hay logic đăng xuất. |
| **Dashboard** | Nút "PRESET: ALPHA_BULL" | Placeholder, chưa có menu thả xuống để chọn các bộ lọc mẫu khác (ví dụ: Dividend, Value). |
| **Dashboard** | Nút "Custom Scan" | Nút bấm không kích hoạt hành động hay mở Modal. |
| **Dashboard** | Bộ lọc sàn "HSX", "HNX", "UPC" | Có hiệu ứng hover nhưng không thực hiện lọc dữ liệu trong bảng điện. |
| **Dashboard** | Header bảng (Ticker, Price...) | Các icon sắp xếp và lọc trên tiêu đề cột chưa có code xử lý logic sorting/filtering. |
| **Dashboard** | Biểu đồ Market Pulse/Heatmap | Dữ liệu tĩnh hoàn toàn, chưa có tính năng click-to-detail (bấm vào ô mã để xem chi tiết). |
| **Screener** | Sidebar "Quick Filters" | Bấm đổi màu nút (active state) nhưng bảng kết quả bên phải không thay đổi. |
| **Screener** | Sliders (P/E, RSI, ROE...) | Chỉ là UI hiển thị, không thể kéo trượt và không gửi tham số lọc về Store. |
| **Screener** | Thống kê "Matches: 24 / 1,420" | Con số 24 được ghi cứng, không phản ánh kết quả lọc thực tế. |
| **Screener** | Chế độ View (Grid/List) | Nút chuyển đổi giao diện chưa thay đổi layout của bảng. |
| **Screener** | Nút "Export CSV" | Chưa có logic kết xuất dữ liệu sang định dạng Excel/CSV. |
| **Stock Detail** | Nút Timeframe (1m, 5m, 1H...) | Đổi trạng thái nút nhưng không kích hoạt lệnh load lại data mới cho biểu đồ. |
| **Stock Detail** | Công cụ biểu đồ (Vẽ, Chỉ báo) | **Đã hoàn thành**: Tích hợp KLineCharts với bộ công cụ vẽ (Trendline, Fibonacci, Rectangle...) và chỉ báo (MA, Volume). |
| **Stock Detail** | Tính năng Zoom/Pan biểu đồ | **Đã hoàn thành**: Hỗ trợ Zoom/Pan cực mượt trên nền tảng Canvas. |
| **Stock Detail** | Bảng đặt lệnh (Buy/Sell) | Các nút BUY/SELL chưa kết nối với hệ thống khớp lệnh (Trading Engine). |
| **Stock Detail** | Ô nhập Price/Quantity | Chưa có logic validation nghiệp vụ (ví dụ: bước giá, số dư tối thiểu, giờ giao dịch). |
| **Stock Detail** | Nút "Max" (Sức mua tối đa) | Chưa tính toán được khối lượng tối đa dựa trên Equity, Sức mua và Tỷ lệ Margin. |
| **Stock Detail** | Sổ lệnh (Order Book) | Tương tác "Click-to-Fill" (bấm vào mức giá để tự điền vào khung đặt lệnh) chưa được triển khai. |
| **Stock Detail** | Nút "Xem toàn bộ lịch sử" | Mục tin tức và AI Insight chưa có trang xem chi tiết/lịch sử đầy đủ. |
| **Community** | Tương tác bài viết (Share, Comment) | Các nút Share và Comment chưa mở ra khung nhập liệu hay hộp thoại chia sẻ. |
| **Community** | Menu bài viết (dấu 3 chấm) | Nút mở menu tùy chọn bài viết (Xóa, Báo cáo, Lưu) chưa hoạt động. |
| **Community** | Nút "Theo dõi" (Follow) | Chỉ có hiệu ứng hover, chưa lưu trạng thái quan hệ người dùng vào database. |
| **Community** | Click vào Avatar/Tên Chuyên gia | Chưa dẫn đến trang Profile cá nhân của chuyên gia để xem lịch sử danh mục của họ. |
| **Portfolio** | Các Tab Phân tích (Performance, Allocation) | Chuyển tab chỉ đổi UI, dữ liệu biểu đồ và thống kê bên dưới vẫn là các mảng mẫu. |
| **Portfolio** | Risk Metrics (Sharpe, Alpha, Beta) | Các chỉ số quản trị rủi ro chuyên nghiệp hiện đang được ghi cứng (hardcoded), chưa có logic tính toán. |
| **Portfolio** | Nút Nạp/Rút/Tái cơ cấu | Placeholder cho các luồng xử lý giao dịch tiền mặt và quản lý tài khoản ngân hàng. |
| **Portfolio** | Nút "Manage Alerts" | Không dẫn đến trang quản lý cảnh báo danh mục. |
| **Auto-Pilot** | Nút "VIEW ALL ACTIVITY" | Không dẫn đến trang lịch sử log chi tiết của Robot. |
| **Auto-Pilot** | Click vào dòng Log | Các dòng log có `cursor-pointer` nhưng bấm vào không điều hướng người dùng. |
| **Auto-Pilot** | Click vào Strategy Card | Chưa có trang xem chi tiết cấu hình chiến thuật (Parameters/Rules). |
| **AI Assistant** | Nút Attach & Mic | Placeholder cho tính năng phân tích file (PDF báo cáo tài chính) và ra lệnh bằng giọng nói. |
| **Alerts** | Nút "Tạo cảnh báo mới" | Chưa có giao diện Modal/Form để thiết lập điều kiện (Ví dụ: Cảnh báo khi giá > X). |
| **Advanced Chart** | Lưu trữ hình vẽ kỹ thuật | **Chỉ UI**: Hình vẽ (Trendline, Fibonacci...) chỉ tồn tại trong phiên làm việc, chưa lưu vào Database/Local Storage. |
| **Advanced Chart** | Nút "Add Indicator" (f+) | **Chỉ UI**: Hiện tại chỉ hiển thị icon, chưa có Modal chọn danh mục chỉ báo kỹ thuật (RSI, MACD...). |
| **Advanced Chart** | Nút "Settings" (Bánh răng) | **Chỉ UI**: Chưa mở ra bảng cấu hình giao diện biểu đồ (Màu nến, lưới, scale...). |
| **Advanced Chart** | Chụp ảnh biểu đồ (Camera) | **Chỉ UI**: Nút có hiệu ứng bấm nhưng chưa thực hiện lưu ảnh (Screenshot) về máy. |
| **Price Chart** | Nguồn dữ liệu (Data Source) | **Mock Data**: Biểu đồ vẫn đang dùng hàm `Array.from` để tạo nến giả lập, chưa kết nối API/WebSocket của sàn. |
| **Price Chart** | Đa biểu đồ (Layout 2x2, 1x2) | **Chỉ UI**: Các nút chuyển đổi bố cục biểu đồ (Grid) chưa thay đổi số lượng instance của PriceChart. |
| **Global** | Nút "Quick Buy/Sell" trên Chart | **Chỉ UI**: Các nút giao dịch nhanh trên biểu đồ chưa được kết nối với Order Entry. |


---

## 3. Yêu cầu Backend (API Requirements)
Dựa trên các lỗ hổng trên, Backend cần cung cấp các nhóm API sau:

1.  **Market Data API:**
    *   `GET /market/indices`: Lấy thông tin các chỉ số VN-Index, HNX-Index, UPCOM-Index.
    *   `GET /market/breadth`: Lấy số lượng mã tăng/giảm/tham chiếu toàn thị trường.
    *   `GET /market/liquidity`: Lấy dữ liệu thanh khoản theo thời gian thực (so sánh với phiên trước).
    *   `GET /market/snapshot`: Lấy danh sách toàn bộ mã cổ phiếu kèm giá khớp lệnh, +/-.

2.  **Stock Detail API:**
    *   `GET /stock/{symbol}/profile`: Thông tin doanh nghiệp, thông số tài chính cơ bản.
    *   `GET /stock/{symbol}/ohlcv`: Dữ liệu nến cho biểu đồ (hỗ trợ nhiều timeframe).
    *   `GET /stock/{symbol}/orderbook`: 10-20 mức giá mua/bán (Real-time).
    *   `GET /stock/{symbol}/trades`: Lịch sử khớp lệnh trong phiên.

3.  **Screener API:**
    *   `POST /market/screener`: Nhận các điều kiện lọc (P/E, ROE, RSI...) và trả về danh sách mã thỏa mãn.

4.  **Portfolio API:**
    *   `GET /portfolio/summary`: Tổng tài sản, sức mua, lãi lỗ tạm tính.
    *   `GET /portfolio/assets`: Danh sách các mã đang nắm giữ kèm giá vốn.

5.  **AI Service:**
    *   `POST /ai/chat`: Gửi câu hỏi và nhận câu trả lời từ LLM (tích hợp dữ liệu thị trường).
    *   `GET /ai/consensus/{symbol}`: Lấy nhận định tổng hợp từ AI cho một mã cụ thể.

---

## 4. Kế hoạch loại bỏ Mock Data
1.  **Giai đoạn 1:** Thay thế `useStockStore` tĩnh bằng **React Query (TanStack Query)** để gọi API thật.
2.  **Giai đoạn 2:** Kết nối **WebSocket** cho các thành phần cần tốc độ cao (OrderBook, Price, Tape).
3.  **Giai đoạn 3:** Chuyển đổi các logic "sinh số ngẫu nhiên" sang logic "tính toán dựa trên dữ liệu thật" từ Backend.
