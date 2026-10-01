# Đối soát Portfolio và Agent — 30/09/2026

Phạm vi: các lỗi trong hai ảnh Portfolio/Agent, từ giao diện, API và luồng khớp
lệnh đến dữ liệu Core PostgreSQL trên VPS. Kiểm tra production chỉ đọc; branch
`fix/bugs-2026-09-30` chưa merge, deploy hoặc sửa dữ liệu production.

## Kết quả đối soát

Quỹ Multi-Agent có account ID `940b0c70-2010-42f3-b947-797e6419b794`.
DB có 46 BUY, 8 SELL và 54 chứng từ `order_executions` tương ứng, đủ trường
gross value, phí, thuế và cash delta. Tiền mặt: **862.353.889 đ**; 11 vị thế.

| Khoản mục | Giá trị | Cơ sở |
|---|---:|---|
| Vốn ban đầu suy ra | 1.000.000.000 đ | Tiền mặt hiện tại trừ tổng cash delta |
| Giá vốn vị thế, chưa phí | 136.620.000 đ | `positions` đối soát chứng từ khớp lệnh |
| Giá vốn vị thế, gồm phí mua | 136.891.600 đ | Giá vốn bình quân, phân bổ phí khi bán một phần |
| Lãi/lỗ đã chốt, chưa phí và thuế | -280.500 đ | Chứng từ mua/bán |
| Lãi/lỗ đã chốt, sau phí và thuế | **-754.511 đ** | Tiền bán ròng trừ giá vốn bán gồm phí mua |
| Tổng phí và thuế đã phát sinh | 745.611 đ | Phí 623.666 đ + thuế 121.945 đ |

Với **bộ giá trong ảnh Portfolio**, giá trị vị thế là 141.800.000 đ:

- NAV = 862.353.889 + 141.800.000 = **1.004.153.889 đ**.
- Chưa chốt sau phí mua = 141.800.000 - 136.891.600 = **4.908.400 đ**.
- Tổng lãi/lỗ = 4.908.400 - 754.511 = **4.153.889 đ**, tức **0,415389%** vốn.
- Con số **3,79%** cũ là 5.180.000 / 136.620.000: lợi suất vị thế chưa phí,
  không phải lợi nhuận toàn tài khoản. Những số trên là đối soát ảnh, không
  phải khẳng định giá thị trường hiện tại vẫn bằng giá trong ảnh.

Công thức vốn suy ra và lợi suất đơn giả định tài khoản paper không nạp/rút
thêm hoặc phát sinh dòng tiền ngoài khớp lệnh. Khi hỗ trợ các dòng tiền đó,
phải có sổ dòng tiền và lợi suất điều chỉnh như TWR; không tiếp tục dùng giả định này.

## Nguyên nhân và bản sửa

1. **Không vẽ NAV dù DB có lịch sử.** Quỹ chính có 30 snapshot NAV từ 12/08
   đến 25/09; ML có 32 đến 29/09. API cũ chỉ trả một điểm. API mới đọc
   `portfolio_nav_history` theo account. Sharpe cần ít nhất 20 lợi suất ngày;
   Alpha/Beta thêm điều kiện có VNINDEX cùng ngày. Khoảng trống nhiều phiên
   không được xem như một lợi suất ngày. Drawdown tính từ snapshot đã có.

2. **NAV Agent và Portfolio khác thời điểm.** Agent dùng `portfolio_account`
   cập nhật 28/09 lúc 13:05 nhưng giá trị vị thế khớp giá đóng cửa 25/09:
   140.450.000 đ, NAV 1.002.803.889 đ. Portfolio có bộ giá khác. Hai trang
   nay dùng chung dịch vụ snapshot và công thức; account được ghi rõ vì
   Portfolio của người dùng và quỹ Multi-Agent có thể là hai tài khoản khác nhau.

3. **Đã bán nhưng không hiện lãi/lỗ đã chốt.** Giao diện/API cũ thiếu tổng
   realized P&L. Bản sửa tính từ chứng từ khớp lệnh, đối soát số lượng và giá
   vốn với `positions`; nếu thiếu hoặc lệch chứng từ thì để chưa xác định.
   Lệnh thủ công nay ghi chứng từ trong cùng transaction với tiền/vị thế/lệnh.

4. **`paper_trades` không phải sổ kế toán đáng tin cậy.** Có sáu FPT CLOSED
   trong quỹ chính nhưng không có lệnh FPT tương ứng; có giá mua 160.000,
   giá bán 131.000 nhưng P&L lưu +0,77% thay vì -18,125%. Stop-loss monitoring
   cũ đóng toàn bộ lot ngay khi cảnh báo và chép P&L vị thế vào từng lot.
   Bản sửa bỏ thao tác đó: chỉ khớp bán mới đóng đúng lượng. Replay không có
   pending order cũng ghi lot; bán một phần giữ ngày mua gốc. Agent học tính
   lại tỷ lệ từ giá và yêu cầu chứng từ mua/bán phù hợp. Các dòng cũ vẫn được
   giữ nguyên để điều tra, không dùng làm nguồn tổng lãi/lỗ tài khoản.

5. **Dữ liệu giám sát có mã đã bán.** `position_health_ticks` không có khóa
   account. API giới hạn theo các mã quỹ đang giữ; P&L hiện tại lấy từ snapshot.
   Trạng thái sức khỏe luận điểm vẫn là dữ liệu giám sát, có thời điểm riêng.
   Đây chưa phải chứng nhận sức khỏe luận điểm có phân tách account đầy đủ.

6. **Giá cũ bị gọi là live và tham chiếu SSI bất nhất.** SSI có trade 20.150
   nhưng metadata ref 36.050, sàn 33.600, trần 38.500, sinh -44,1% giả.
   Bản sửa bỏ tham chiếu/ngưỡng bất nhất, giữ trade và đánh dấu dữ liệu thiếu.
   Quote Redis/in-memory đều kiểm tra tuổi dữ liệu. Snapshot chọn giá có
   thời điểm mới hơn giữa last trade hợp lệ và đóng cửa; quote quá 30 giây
   hiện trạng thái cũ. Không thay giá thiếu bằng giá vốn hay đoán lại đơn vị.

7. **Thiếu snapshot EOD quỹ chính.** Worker nay xử lý độc lập cả quỹ chính
   và ML sau mốc ETL 17:30; cần đóng cửa đúng ngày của mọi vị thế. Thiếu
   giá/lỗi sẽ retry; lỗi một quỹ không ngăn ghi snapshot quỹ còn lại. Không
   ghi snapshot hôm nay bằng giá hôm trước.

## Realtime và hiệu năng

Không cần mọi dữ liệu realtime 100%. Giá dùng cho định giá và thông tin
khớp lệnh cần cập nhật sớm; lãi/lỗ đã chốt thay đổi khi khớp lệnh, không đổi
theo tick. Lịch sử NAV, Sharpe/Alpha/Beta là chuỗi cuối ngày.

- Trước sửa: mỗi lần mở Portfolio gọi nhiều endpoint, tính vị thế lặp ba lần
  thành 33 lượt lấy quote cho 11 mã. Sau sửa: một snapshot, mỗi mã lấy một lần.
- DB đọc tài khoản trong transaction Repeatable Read; gọi giá bên ngoài
  sau transaction. Tick không ghi PostgreSQL và không tính lại toàn sổ giao dịch.
- Giao diện dùng subscription socket hiện có, gom tick một lần mỗi giây.
  Tải lại sổ tài khoản mỗi 60 giây khi trang đang hiển thị hoặc khi reconnect;
  có nút làm mới. Đây là độ trễ trạng thái tài khoản có chủ đích, chưa phải
  stream thông báo khớp lệnh tức thời trên mọi thiết bị.
- Kiểm tra query trên dữ liệu thực: cách cũ quét khoảng 30.900 dòng mất
  khoảng 22 ms; LATERAL lấy đóng cửa gần nhất theo index mất khoảng 0,405 ms.
  Đây là một phép EXPLAIN, chưa phải benchmark tải đồng thời hoặc chứng nhận SLA.

Thiết kế này tương ứng việc tách market data stream và account/order updates
trong [Alpaca](https://docs.alpaca.markets/us/v1.4.2/docs/websocket-streaming).
Các trường tiền mặt, giá trị thị trường, realized và unrealized cũng được tách
trong [IBKR Portfolio Ledger](https://www.interactivebrokers.com/docs/web-api/v1/endpoints/portfolio/portfolio-ledger).
Phí thuộc giá vốn/tiền bán theo
[IBKR Statements](https://www.interactivebrokers.com/en/general/education/pdfnotes/PDF-TWS_Statements.php/1000).
Đây là tham chiếu cách tổ chức dữ liệu, không phải tuyên bố mọi broker dùng
cùng giá đánh dấu, phương pháp lot hoặc chu kỳ refresh.

## Xác minh và dữ liệu còn cần xử lý

- Backend build và 12 kiểm thử kế toán/snapshot/khớp lệnh: pass.
- Frontend production build và ESLint các file sửa: không lỗi, một warning
  `cdc` không dùng đã tồn tại.
- 24 kiểm thử Python critical: pass, bỏ hai Beneish cần seeded DB; ba
  DeprecationWarning từ joblib/NumPy. Ruff chưa có trong Python local;
  workflow CI có cài và chạy kiểm tra Ruff.
- Playwright local với fixture và socket thật: hai trang cùng NAV
  10.779.400 đ, đã chốt 84.400 đ, chưa chốt 695.000 đ, tổng 779.400 đ,
  lợi suất 7,79%; Portfolio có SVG đường NAV. Tick tăng giá làm NAV/P&L đổi
  trong khi số request snapshot vẫn 10 trước/sau, tiền mặt/realized giữ nguyên.
  Đây là kiểm tra local, chưa kiểm tra bản deploy production.

Production vẫn có khoảng trống lịch sử NAV và SSI daily chưa có đóng cửa
28–29/09 ở thời điểm kiểm tra. Cần khôi phục từ nguồn giá chưa điều chỉnh đã
xác minh và đối soát lịch sử giao dịch từng ngày. Không dùng vị thế hiện tại
để tự tạo lại NAV quá khứ. Việc sửa dữ liệu production và deploy chưa được thực hiện.

Sau phản hồi về chi phí kiểm thử, CI và lệnh `npm test` đã bỏ wildcard.
Backend chỉ chạy danh sách critical tường minh: hai kiểm tra khớp lệnh/quỹ ML
có trước và unit test hàm thuần kế toán mới. Kiểm tra snapshot bằng mock được
phân loại service, chỉ chạy khi điều tra Portfolio; không thêm vào gate deploy.
Xem `back-end/tests/README.md` để biết phạm vi và giới hạn của từng nhóm.

## Bổ sung: UX ML và phiên 28/09

- Trang cũ mặc định kỳ tùy chọn chỉ gồm hôm nay và thu gọn lịch sử NAV, nên
  số liệu cũ đã có vẫn bị che. Bản sửa mặc định toàn bộ lịch sử và dự báo mới
  nhất; kỳ tùy chọn khởi tạo từ mốc NAV đang có. Lịch sử NAV, biểu đồ và bảng
  phiên luôn hiển thị; bảng đối soát tách riêng và chia 20 dòng/trang.
- ML có ba dự báo ngày 28/09, dữ liệu nền 25/09, tạo lúc 11:29 ngày 28;
  bốn khớp SHADOW thực sự ngày 28. DB không có NAV 28, dù FPT/SHB/TCB có
  giá đóng cửa đúng ngày. Dự báo không tự tạo snapshot NAV. Worker EOD cũ
  chỉ xử lý ngày đang chạy, không bù ngày bỏ lỡ. Không có log ngày 28 để
  xác định nguyên nhân vận hành: yêu cầu đọc log bị duyệt tự động từ chối
  bởi lỗi quota của bước duyệt, không phải vì thao tác bị đánh giá là nguy hiểm.
  Kiểm tra hạn mức tài khoản sau đó vẫn cho phép dùng bình thường; nguyên nhân
  lỗi quota của bộ duyệt chưa được xác định.
- Tại thời điểm kiểm tra, dữ liệu đóng cửa mới đến 29/09. Dự báo 28 cần
  ba phiên sau ngày nền 25: 28, 29, 30; vì chưa đủ dữ liệu giá nên cả ba chưa
  đối soát. Đây khác với việc mất snapshot NAV. UI nay ghi rõ các ngày chưa
  đối soát và ngày dữ liệu nền; không tự coi là đạt/không đạt.
- API cũ gọi chênh lệch NAV 29 so với 25 (-1.229.893 đ) là lãi/lỗ phiên 29.
  API mới lấy ngày giao dịch có giá đóng cửa để xác định phiên trước: thiếu
  NAV 28 thì để lãi/lỗ phiên 29 chưa xác định. Lũy kế toàn kỳ vẫn có thể tính
  từ hai mốc NAV đầu/cuối đã biết. Biểu đồ không nối liền qua ngày thiếu.
- Phục dựng book bằng các khớp trước/sau đúng thời điểm: 28/09 có FPT
  3.000 × 63.700 = 191.100.000; SHB 16.800 × 11.500 = 193.200.000;
  TCB 5.800 × 32.850 = 190.530.000. Tổng vị thế 574.830.000, tiền mặt
  403.231.008, **NAV 978.061.008 đ**. Nếu snapshot đó được khôi phục đúng,
  lãi/lỗ phiên 28 là +2.560.107 đ và phiên 29 là -3.790.000 đ.
- Ở lần kiểm tra ban đầu, chưa sửa dữ liệu lịch sử production. Sau đó chủ
  hệ thống yêu cầu bổ sung trực tiếp NAV 28 vào DB, không chờ xác nhận nguyên
  nhân vận hành và không đưa script ghi dữ liệu production vào nhánh này.
- Xác minh local: TypeScript và ESLint file ML; backend build và chỉ test
  hợp đồng ML liên quan (không chạy toàn bộ test). Preview browser dùng
  fixture NAV từ ví dụ đã đối soát và số liệu accuracy giả lập để kiểm tra
  UX; đã xác nhận mặc định có chỉ số/NAV, lịch sử hiện sẵn, phân trang,
  kỳ tùy chọn, cảnh báo khoảng ngày và tách bộ lọc ngày dự báo khỏi kỳ.

### Chốt công thức cho màn ML

- Lãi/lỗ đã chốt lọc theo ngày SELL thực sự khớp; giá vốn bình quân di động
  dùng toàn bộ lịch sử, gồm phí mua phân bổ và tiền bán sau phí/thuế. Có bảng
  chứng từ từng lượng bán, kể cả bán một phần. Thiếu chứng từ hoặc lệch vị thế
  thì trả null, không dùng giá nền dự báo hay giá đóng cửa để giả lập lãi đã chốt.
- NAV chốt mới nhất lấy trực tiếp snapshot, không phụ thuộc kỳ hoặc ngày dự
  báo chọn. Vị thế/tiền mặt hiện tại có NAV ước tính riêng bằng các giá đóng
  cửa có ngày; đây không phải snapshot NAV của phiên hiện tại.
- NAV kỳ dùng đúng phiên trước `from` và phiên cuối có dữ liệu thị trường
  trong kỳ; thiếu snapshot một trong hai mốc thì chưa tính. Riêng 28–29 bao
  gồm cả hai phiên nên mốc 25 hợp lệ cho tổng -1.229.893 đ; nếu chỉ chọn 29,
  thiếu mốc 28 thì biến động NAV kỳ là null. Không gọi số hai phiên là lãi/lỗ
  của riêng phiên 29.
- Các kết quả ba phiên của dự báo được ghi rõ giá nền/ngày feature và đổi
  nhãn thành biến động giá ML. Code vẫn lấy ba bản ghi giá theo từng mã sau
  ngày feature; chưa chứng nhận đó là ba phiên sàn đầy đủ khi dữ liệu mã bị
  khuyết. Chính sách bán hiện có là điều kiện bảo vệ giá và time stop 5 ngày
  lịch; không âm thầm đổi chiến lược thành bán T+3 trong bản sửa giao diện.
- Chỉ hai kiểm tra liên quan được chạy: hợp đồng endpoint ML (mốc đầu/cuối
  bị thiếu, NAV cố định, giá vốn mua trước kỳ) và unit kế toán cho bán từng
  phần/lọc ngày bán. Browser local dùng sale/accuracy giả lập để xác minh UI;
  không coi số lãi đã chốt trong fixture là kết quả production.

### Bổ sung trực tiếp NAV ngày 28 theo yêu cầu chủ hệ thống

- Ngày 30/09/2026, đã thêm đúng một dòng vào `portfolio_nav_history` trên
  PROD: tài khoản `standalone-pure-ml-fund-account`, ngày `2026-09-28`, NAV
  `978061008.00`, tiền mặt `403231008.00`. Transaction đã COMMIT và kết nối
  mới đọc lại xác nhận bản ghi tồn tại; không thay đổi tiền mặt/vị thế hiện tại.
- Trước ghi, phục dựng lại cả ba ngày 25/28/29 từ toàn bộ chứng từ khớp và
  đóng cửa chưa điều chỉnh đúng ngày; 31 chứng từ hợp lệ, không thiếu giá hay
  vị thế âm; hai ngày liền kề khớp NAV/tiền mặt đã lưu chính xác đến đồng.
  Transaction có guard nguồn, mốc vốn ban đầu và snapshot xung đột.
- Đã sao lưu bảng NAV bằng pg_dump custom và kiểm tra pg_restore --list:
  `/opt/aiinvest-backups/ml-nav-before-20260928-20260930T093747Z.dump`.
  SHA-256: `864ba6969ec8e8a605fef43113b8c97fe36644a95fef4910a639445ac342e729`.
- Sau bổ sung: biến động NAV phiên 28 là `+2560107.00`, phiên 29 là
  `-3790000.00`. Không tạo migration/script recovery trong repository,
  không commit/push/deploy code. Chưa kiểm tra giao diện PROD sau làm mới;
  nguyên nhân tác vụ EOD bỏ lỡ ngày 28 vẫn chưa được xác nhận.
