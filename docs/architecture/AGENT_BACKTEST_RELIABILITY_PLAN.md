# Kế hoạch làm cho Agent backtest đáng tin cậy

## 1. Mục tiêu và ranh giới

Chạy pipeline quyết định Agent theo từng thời điểm lịch sử trên một **database PostgreSQL replay riêng** có cùng schema với vận hành. Tiền, vị thế, lệnh và các bản ghi Agent được đọc/ghi qua repository thật; giải thích được mọi điểm dừng từ Universe đến Risk. Kết quả phải tái lập được với cùng dữ liệu và phản hồi LLM. Chỉ đánh giá hiệu suất sau khi kiểm tra tính đúng của dữ liệu và quyết định.

Không gọi Agent thực thi hoặc broker; lệnh được duyệt chỉ được khớp giả lập ở bar 09:45 khả dụng của phiên sau, rồi cập nhật tiền/vị thế/lệnh trong PostgreSQL replay để phiên tiếp theo đọc qua repository. Không kết nối SAG hoặc đọc đầu ra SAG trong thời gian hold. Không phát sự kiện ra bên ngoài, không cập nhật database vận hành. Không điều chỉnh ngưỡng để ép phát sinh giao dịch.

Kết quả hiện tại là baseline cần giữ để đối chiếu: 30 phiên 2026-08-10–2026-09-23, chỉ FPT/SSI/MBB; 73 lượt Research, 13 Thesis, 3 Counter Thesis `PROCEED`, 3 Allocation, 2 Risk `PASS`, 2 mua, 0 bán; NAV 999.815.005 VND trên 1 tỷ ban đầu. File `ai-engine/.data/agent_replay_30d_verified.sqlite` là hiện vật baseline, không ghi đè.

## 2. Lỗi đã xác nhận và tiêu chí sửa

| Mức | Vấn đề đã xác nhận | Điều kiện đạt |
|---|---|---|
| P0 | RL đọc `result.data` trong khi Change Gate trả trực tiếp `result`; 27 veto thành `APPROVED`, trace luôn ghi `100%_COMMITTED_POSTGRESQL`. | Veto thật làm `approved=false`, không persist/publish/apply trọng số mới; trace ghi trạng thái thực; lỗi/missing response đóng cổng. |
| P0 | RL truy vấn 25 VN-Index mới nhất không chặn `date`; OOS Sharpe 2,99 lặp lại. | Mọi truy vấn replay có cutoff trước thời điểm quyết định; không đủ mẫu thì trạng thái `INSUFFICIENT_DATA`, không gọi prior/synthetic là OOS đo được. |
| P0 | 13/13 Thesis có target cơ sở ~1,165 × giá do `valuation_inputs` rỗng và công thức dự phòng 1,15/1,18. | Thiếu định giá thì không sinh target định lượng hoặc không cho BUY theo chính sách hiện có; target có nguồn, ngày và phép tính có thể kiểm tra. |
| P0 | F4 là điểm factor 10–95 nhưng Thesis/prompt gọi `SUE %`; LLM tạo catalyst cụ thể không có bằng chứng. | Trường và prompt gọi đúng F4 score; dữ kiện/catalyst không có nguồn được gắn `UNVERIFIED` và không được dùng làm xác nhận độc lập hay lệnh BUY. |
| P0 | `adtv20` từ Universe là VND, Allocation/Liquidity/Hard Law dùng như số cổ phiếu; Risk dùng mặc định 2 triệu cổ phiếu. | Truyền `adtv20_shares` và `adtv20_vnd` riêng, cùng cửa sổ và cutoff; thiếu shares thì fail closed, không thay bằng số mặc định. |
| P0 | Pipeline đọc `distribution_days`/`breadth_ma20_pct` không có trong Surveillance, làm Risk nhận 0/60 mặc định. | Ánh xạ đúng dữ liệu có thật hoặc đánh dấu thiếu; Risk không báo `HEALTHY` từ hằng số. |
| P1 | Replay chỉ quét ba mã được chọn trước; `Universe Discovery` không nhận `is_replay`; metadata/campaign có thể lấy trạng thái hiện tại. | Universe lịch sử có quy tắc chọn theo ngày, số mã quét/loại hiển thị; replay không vào nhánh realtime; trạng thái không có snapshot as-of được cô lập hoặc đánh dấu không kiểm chứng. |
| P1 | LLM không cố định; lần chạy lại cho lệnh khác. | Lưu/replay phản hồi LLM theo khóa input, phiên bản prompt/model; cùng snapshot cho cùng phễu, lệnh và NAV; thiếu phản hồi phải fail closed hoặc ghi rõ chế độ live LLM. |
| P1 | Runner chỉ lưu response, không lưu input/provenance/phễu; pipeline vẫn báo `REPLAY_INCOMPLETE_NO_HISTORICAL_ORDERBOOK`. | PostgreSQL replay lưu run manifest, input hash/source time, verdict và lý do loại theo mã/ngày, lệnh chờ/khớp, phí, NAV; trạng thái cuối phân biệt quyết định Agent với giả định fill. |
| P1 | 09:45 là một bar/mã/ngày; chưa có order book, intraday stop, bán sau T+2 hay thoát vị thế quan sát được. | Báo cáo nêu độ phủ và cờ giả định; test bán/T+2/không có bar và vị thế cuối kỳ; không tuyên bố tỷ lệ khớp thực tế. |

## 3. Thứ tự thực hiện và điều kiện dừng

### Giai đoạn A — Khóa chứng cứ và bảo vệ vận hành

1. Giữ nguyên SQLite baseline để so sánh. Tạo database PostgreSQL replay với tên riêng, snapshot schema/dữ liệu lịch sử cần dùng từ nguồn chỉ đọc và tài khoản replay riêng. Ghi manifest cho mỗi run: nguồn snapshot, khoảng ngày, universe selection, vốn, mã nguồn/config/prompt/model, chính sách fill, phí và dữ liệu đầu vào. Không đưa secret vào manifest.
2. Định nghĩa `as_of` cho mỗi phiên: tín hiệu EOD dùng dữ liệu đã công bố trước 09:45; giá 09:45 chỉ dùng ở thời điểm 09:45; khớp lệnh được duyệt sau quyết định ở phiên tiếp theo. Tạo một kiểm tra chung xác nhận `source_timestamp <= decision_timestamp` cho dữ liệu có timestamp.
3. Dùng PostgreSQL replay làm nơi chứa trạng thái replay (tiền, vị thế, lệnh, campaign, quyết định, governance, phản hồi LLM). Bỏ cách runner truyền NAV/vị thế giả qua `current_nav`/`replay_portfolio` ở vòng kiểm thử hợp đồng: pipeline phải tự gọi `PortfolioRepository` với tài khoản replay và đối soát giá mark as-of. Bật đường ghi Agent vào database replay; đường ghi ra database vận hành hoặc event/broker ngoài phải bị chặn trước khi gọi. Không tái sử dụng campaign/tài khoản live.
4. Kiểm tra thất bại khi DB thiếu bảng/bar/nguồn giá, và xác minh không có event/broker call. Nếu không giữ được ranh giới này, dừng trước khi chạy rộng.

### Giai đoạn B — Sửa hợp đồng quyết định

1. RL: đọc đúng phản hồi Change Gate, lấy trọng số hiện hành theo as-of thay vì `current_state={}`, không chuyển trọng số bị veto xuống Research; bỏ trace commit cố định. OOS chỉ dùng dữ liệu trước ngày replay và chỉ dán nhãn thống kê đo được khi đủ mẫu. Kelly prior vẫn có thể tồn tại nhưng phải ghi `PRIOR`, `sample_size=0`; Allocation không được gọi đó là realized calibration.
2. Thesis: sửa nhãn F4 ở rule và prompt; bỏ target giá 1,165× khi thiếu đầu vào; chặn catalyst/dữ kiện không dẫn nguồn khỏi quyết định BUY. Đồng bộ quy tắc “ba tín hiệu độc lập” giữa Thesis và Counter Thesis, ưu tiên nguồn định lượng độc lập, tránh đếm CSS cùng factor thành hai bằng chứng.
3. Universe/Research: bảo đảm `is_replay` và cutoff được truyền toàn chuỗi; kiểm tra độ phủ ngày công bố của dữ liệu tài chính, audit opinion, trading status. Trường không có lịch sử phải mang cờ thiếu provenance, không được tự chuyển thành `VERIFIED`.
4. Allocation/Risk: chuẩn hóa đơn vị ADTV; truyền `adtv20_shares` cùng order, không dùng fallback 2 triệu; ánh xạ breadth đúng; giữ trạng thái `ESTIMATE` của tail risk và không coi estimate là bằng chứng đã thử stress thật. Dùng campaign cục bộ và vị thế ảo ở mọi lần quyết định.

Sau mỗi mục, chạy test mục tiêu; không chuyển sang mục sau khi test mới đỏ. Thay đổi hợp đồng nội bộ phải kiểm tra mọi caller và đường live trước khi sửa.

### Giai đoạn C — Replay có thể giải thích và tái lập

1. Chạy một tập nhỏ cố định trước: cùng 3 mã, cùng 30 ngày. Lưu từng lý do bị loại ở Universe, Research, Thesis, Counter, CIO, Allocation, Risk; lưu rõ lệnh chờ và fill giả định. Đối soát `NAV = cash + market value`, phí, lô 100, T+2, không bán khống, không dùng giá của chính phiên ra quyết định để khớp.
2. Thêm chế độ ghi và phát lại phản hồi LLM. Test chạy lại cùng snapshot phải cho cùng quyết định và NAV; khi prompt/model/input đổi thì cache không được dùng sai.
3. Chạy kiểm tra độ phủ universe theo ngày và lấy tập HOSE đủ dữ liệu có quy tắc chọn trước khi biết lợi nhuận kỳ test. Không ép quét toàn bộ nếu dữ liệu/cost LLM không đáp ứng; ghi chính xác số mã đủ điều kiện và mẫu đã quét. Thử 5 phiên trước, rồi 30 phiên; 60 phiên chỉ khi dữ liệu và kết quả 30 phiên đạt gate.
4. Tạo báo cáo gồm: số lượt vào/ra mỗi cổng, reasons theo mã/ngày, vốn sử dụng, số lệnh mua/bán và giao dịch đóng, NAV, phí, mức giảm NAV, kết quả theo mã, dữ liệu ước lượng/thiếu, các cảnh báo look-ahead và số quyết định không tái lập. Không công bố Sharpe/OOS hoặc hiệu suất chiến lược khi không đủ giao dịch và thời gian nắm giữ.

### Giai đoạn D — Kiểm định và bàn giao

1. Test bắt buộc: Governance veto/missing/error; as-of truy vấn và tương lai bị từ chối; F4 không biến thành phần trăm SUE; định giá thiếu nguồn không sinh BUY; ADTV đơn vị và trần lệnh; breadth không nhận 0/60 mặc định; campaign replay không đọc live; LLM playback; fill/T+2/không có bar; audit/NAV đối soát.
2. Chạy test mục tiêu, lint/compile cho file sửa, `git diff --check`, và một replay nhỏ. So sánh phễu mới với baseline để phân biệt thay đổi do lỗi đã sửa với thay đổi do universe. Ghi test cũ đỏ sẵn từ trước riêng, không nhận là regression mới.
3. Cập nhật `PROJECT_MEMORY.md` và sơ đồ luồng nếu thay đổi kiến trúc/data flow. Không sửa migration hoặc SAG. Bàn giao chính xác file, lệnh chạy, kết quả, giới hạn còn lại, và quyết định có đủ điều kiện thử shadow hay chưa.

## 4. Quy tắc quyết định và rollback

- Ưu tiên sửa tính đúng hơn tăng turnover. Không hạ ngưỡng Research/Counter/Risk chỉ để tạo thêm lệnh.
- Không ghi đè hiện vật baseline; mỗi replay dùng PostgreSQL database hoặc snapshot riêng. Không `DELETE`/`TRUNCATE` các bảng trong database vận hành để dọn replay: nhiều kết nối tự commit, bảng có khóa liên kết, event và cache không thể hoàn nguyên bằng cách xóa vài bảng. Sau đối soát có thể hủy **đúng database replay** đã tạo, với tên/đích đã xác minh. Nếu một thay đổi làm test hoặc invariant đỏ, sửa hoặc hoàn tác riêng thay đổi đó.
- Dữ liệu không có as-of hoặc catalyst không có chứng cứ phải được báo `UNVERIFIED`/`INSUFFICIENT_DATA`; không biến nó thành số đo thật hoặc tín hiệu tốt/xấu.
- Đợt 30/60 phiên chỉ được gọi là kiểm định hiệu suất khi có tập mã được chọn trước, nguồn dữ liệu lịch sử nhất quán, phản hồi LLM tái lập, đủ giao dịch đóng, và không có truy vấn nhìn trước. Nếu chưa đạt, báo cáo là kiểm định tích hợp.

## 5. Backlog triển khai có thể giao việc

Các mục dưới đây là thứ tự phụ thuộc, không phải danh sách tính năng để triển khai đồng thời. Mỗi mục chỉ hoàn thành khi có kiểm tra chạy được và bằng chứng trong PostgreSQL replay hoặc log. Không sửa chiến lược hay nới ngưỡng để làm tăng số lệnh.

| Mã | Phụ thuộc | Tệp/chỗ sửa chính | Việc cần làm | Kiểm tra chấp nhận |
|---|---|---|---|---|
| A1 | Không | Runner, cấu hình kết nối PostgreSQL, repository | Tạo database replay tách biệt và tài khoản riêng; xác minh `current_database()` ở mọi pool/adapter trước khi ghi. Chụp schema và dữ liệu nguồn đủ dùng; lưu manifest. Chặn broker, queue, event ngoài và mọi kết nối tới DB vận hành. | Kết nối sai tên DB dừng trước run; thiếu bảng/nguồn dừng rõ; database vận hành chỉ đọc và baseline cũ không đổi. |
| A2 | A1 | `daily_pipeline_orchestrator.py`, `universe_discovery.py`, repository dữ liệu | Truyền `is_replay`, thời điểm quyết định và cutoff qua mọi pha. Truy vấn EOD chỉ lấy dữ liệu đã có trước quyết định; dữ liệu ngày hiện tại chỉ dùng khi timestamp phù hợp. | Một bản ghi tương lai cố ý đưa vào không thay đổi kết quả ngày quá khứ; dữ liệu thiếu dừng với mã lý do rõ. |
| B1 | A2 | `reinforcement_learning.py`, Governance Change Gate | Chuẩn hóa kết quả Governance, veto fail closed; lấy trạng thái policy as-of; không ghi/apply policy mới khi veto. Bỏ dữ liệu OOS tương lai và nhãn OOS cho prior/synthetic. | Test approve/veto/missing/error; trace persistence khớp DB; OOS thiếu mẫu là `INSUFFICIENT_DATA`. |
| B2 | A2 | `thesis_engine.py`, `investment_thesis.py`, `thesis_synthesizer.py`, `counter_thesis.py` | Đổi F4 thành factor score; bỏ target theo bội số giá hiện tại; buộc định giá và catalyst có nguồn, ngày công bố, nội dung đối chiếu; cùng quy tắc bằng chứng độc lập ở Thesis và Counter. | Thiếu đầu vào không sinh target/BUY; catalyst LLM không có nguồn không được coi là tín hiệu; test trường hợp có dữ liệu thực. |
| B3 | A2 | `universe_discovery.py`, `portfolio_allocation.py`, `portfolio_risk.py`, Surveillance | Tách ADTV VND/cổ phiếu; dùng đúng số cổ phiếu cho participation/hard law; tính/đưa breadth MA20 và phiên phân phối theo cutoff; không dùng số mặc định để cho PASS. | Mất ADTV/breadth thì mã lý do cụ thể và không PASS; giá trị VND không thể bị diễn giải thành cổ phiếu. |
| C1 | B1–B3 | Runner, `PortfolioRepository`, các bảng lệnh/vị thế/account trong PostgreSQL replay | Ghi input hash, timestamp nguồn, kết quả từng Agent, lý do loại, pending/fill/fee/NAV. Giả lập fill bằng bar 09:45 rồi cập nhật cash/positions/orders qua contract thật; phiên sau pipeline tự đọc NAV/vị thế bằng repository. Duy trì T+2 và campaign replay. | `get_account_state()`/`get_open_positions()` trả đúng trạng thái ngày trước; đối soát NAV từng ngày; không bán vượt vị thế khả dụng; không fill khi thiếu bar; quyết định D chỉ fill từ D+1. |
| C2 | C1 | Điểm gọi LLM trong Thesis/Counter/CIO và runner | Ghi/phát lại phản hồi theo hash input cùng phiên bản prompt/model. Bỏ phản hồi không khớp khóa; ghi số lần gọi thực và chi phí nếu đo được. | Hai lần chạy cùng snapshot cho cùng verdict, order và NAV; đổi prompt/model không dùng nhầm phản hồi cũ. |
| C3 | C2 | Runner, SQL chọn universe, báo cáo | Chọn tập HOSE theo quy tắc as-of định trước; thử 5 phiên, sau đó 30; chỉ mở 60 khi đủ dữ liệu và các gate sạch. Báo phễu theo Agent/mã/ngày, lệnh đóng, phí, exposure và độ phủ. | Mỗi ngày ghi số mã đủ dữ liệu, được quét, bị loại; tổng phễu khớp agent events và ledger. |
| D1 | C3 | `tests/`, `PROJECT_MEMORY.md`, sơ đồ kiến trúc | Chạy test đường đúng/sai, kiểm tra diff, lưu câu lệnh và kết quả tái lập; cập nhật tài liệu đúng luồng thực tế. | Không còn kết quả OOS/Sharpe hoặc kết luận lợi nhuận không đủ căn cứ; có lệnh chạy và giới hạn rõ. |

### Hợp đồng dữ liệu tối thiểu cho một quyết định replay

- `decision_at`: timestamp có múi giờ; mỗi nguồn có `source_at` hoặc ngày công bố. Nguồn không có thời điểm công bố được đánh dấu `UNVERIFIED`; không suy diễn từ ngày tải về.
- `universe`: quy tắc chọn đã cố định trước run, danh sách mã và lý do loại theo ngày. Không dùng thành viên chỉ số hoặc trạng thái giao dịch hiện tại để viết lại lịch sử.
- `valuation_inputs`: phương pháp, giá trị, đơn vị, kỳ tài chính, nguồn và `source_at`. Không có các trường bắt buộc thì Thesis không tạo target định lượng.
- `catalyst_evidence`: nguồn, thời điểm công bố, đoạn chứng cứ và mã chứng cứ. Văn bản LLM chỉ là diễn giải, không tự thành chứng cứ.
- `adtv20_vnd` và `adtv20_shares`: cùng cửa sổ 20 phiên và cutoff. Trường nhận vào của Liquidity/Hard Law phải là `adtv20_shares`.
- `governance`: proposal, current state as-of, verdict và lý do; replay lưu local. Trace không được báo `COMMITTED` nếu không có write thành công.
- `order`: ticker, side, số lượng lô 100, ngày quyết định, ngày chờ khớp, giá/cách fill, phí, lý do từ chối, vị thế khả dụng T+2.

### PostgreSQL: cách cô lập và kiểm thử NAV/vị thế thật

1. **Nguồn:** mở kết nối chỉ đọc tới database vận hành để kiểm tra bảng, schema, số phiên, độ phủ nguồn và ngày công bố. Chụp schema và **chỉ các bảng dữ liệu không thuộc SAG trong allowlist đã kiểm tra**, bằng snapshot nhất quán; không cập nhật nguồn. Không dump/copy toàn bộ database vì hold SAG đang cấm đọc đầu ra SAG. Database hiện khoảng 4,5 GB, nên preflight kiểm tra dung lượng và thời gian copy của tập bảng đã chọn. Tránh đưa bản sao vào Git hoặc thư mục production.
2. **Đích:** tạo database có tên replay riêng, quyền/DSN riêng, cùng schema và dữ liệu tham chiếu không thuộc SAG cần cho 30/60 ngày. Process runner phải nạp DSN đích trước khi import module mở pool; mỗi pool/adapter xác minh `current_database()` thuộc tên replay. Nếu DSN trỏ sang database nguồn thì thoát trước khi khởi tạo Agent. Không dùng `search_path` trên schema chung làm ranh giới duy nhất. Các nhánh phụ thuộc SAG phải báo `SAG_CLOSED`/deferred, không tự suy ra tín hiệu tốt hoặc xấu.
3. **Tài khoản:** seed một user/account replay với vốn ban đầu, cash, peak NAV và không có vị thế. Giả lập fill cập nhật `users.cash_balance`, `positions`, `orders`/execution theo hợp đồng hiện có. Mỗi ngày, **không truyền sẵn NAV/vị thế**: gọi `get_account_state()` và `get_open_positions()` từ cùng DSN, so với ledger/fill và dừng nếu sai.
4. **Giá as-of:** `PortfolioRepository` hiện lấy `market_data_daily ORDER BY date DESC` không cutoff, và T+2 mặc định dùng đồng hồ hiện tại. Thêm `as_of` vào đường đọc replay và truy vấn giá `date <= market_data_date`; nếu không có giá mark hợp lệ thì dừng, không dùng giá mới nhất hay giá mua thay thế. T+2 dùng thời điểm replay. Test có giá tương lai cao bất thường và vị thế vừa mua để chắc NAV/số cổ phiếu khả dụng không đổi sai.
5. **Ghi/ngoại vi:** trong đích replay, Agent được ghi vào các bảng thật để phát hiện lỗi contract. Bất kỳ RabbitMQ, broker, Redis chia sẻ, API ngoài hay thao tác trên database nguồn phải bị tắt/chuyển sang sandbox trước khi chạy. Dùng `is_replay` cho thời gian và hành vi thị trường; dùng cờ đích lưu trữ tách biệt để tránh chặn chính các phép ghi PostgreSQL cần kiểm thử.
6. **Dọn:** lưu manifest và báo cáo ra hiện vật riêng trước khi dọn. Đối chiếu fingerprint/count của các bảng nguồn trước/sau. Chỉ `DROP DATABASE` đúng đích replay sau khi xác minh tên tuyệt đối và không còn kết nối, hoặc giữ bản sao để điều tra. Không `DELETE`/`TRUNCATE` những bảng Agent đã chạm trong nguồn; một số đường ghi tự commit và event không hoàn nguyên bằng xóa dòng.

Việc dùng một transaction bao ngoài runner không đủ: `get_conn()` tạo nhiều kết nối và tự `commit()` sau từng thao tác. Clone PostgreSQL giữ đúng loại DB, schema, ràng buộc và câu SQL của pipeline, đồng thời cho phép kiểm thử lỗi lấy NAV/vị thế mà user nêu.

Tham chiếu vận hành: [PostgreSQL 18: SQL Dump](https://www.postgresql.org/docs/18/backup-dump.html) mô tả snapshot nhất quán của `pg_dump`; [PostgreSQL 18: CREATE DATABASE](https://www.postgresql.org/docs/18/sql-createdatabase.html) lưu ý copy trực tiếp bằng `TEMPLATE` không dùng khi database nguồn còn kết nối khác. Vì vậy bước A1 dùng dump/restore có allowlist thay cho `CREATE DATABASE ... TEMPLATE aiinvest`.

### Cổng dừng trước khi công bố kết quả giao dịch

Hiện chưa tìm thấy đường cấp `valuation_inputs` và `catalyst_evidence` lịch sử vào pipeline. Đây là điều kiện dữ liệu phải giải quyết bằng nguồn có thời điểm công bố kiểm chứng được; không tạo giá mục tiêu hay catalyst thay thế. Nếu chưa có, vẫn chạy smoke test 5 phiên để kiểm tra an toàn và phễu, nhưng kết luận là **backtest chưa đủ điều kiện đo hiệu suất**. 30/60 phiên và shadow trading chỉ được đề xuất sau khi A1–C3 đạt, lệnh mua/bán có thể kiểm toán và số giao dịch đóng đủ để việc đo hiệu suất có ý nghĩa.

### Bảng nghiệm thu theo Agent

Pipeline hiện đặt tên 12 Agent, trong đó `trade_execution` là Agent-08. Vì yêu cầu bỏ Agent thực thi, replay phải ghi kết quả của **11 Agent còn lại**; phần giả lập fill thuộc runner, không được trình bày là Agent thứ 12. Không chế tạo heartbeat broker để Governance báo tuân thủ trong replay.

| Agent | Bằng chứng tối thiểu trong log/PostgreSQL replay |
|---|---|
| Market Surveillance (01) | regime, breadth MA20, ngày/nguồn giá và cờ thiếu dữ liệu |
| Universe Discovery (02) | số mã quét, đủ điều kiện, từng lý do loại và cutoff |
| Equity Research (03) | điểm, grade, dữ liệu tài chính as-of và lý do dừng |
| Investment Thesis (04) | F4 score đúng đơn vị, valuation/catalyst có nguồn hoặc trạng thái thiếu |
| Counter Thesis (05) | verdict, từng bằng chứng độc lập và nguồn phản biện |
| Portfolio Allocation (06) | cash/vị thế/campaign replay, ADTV shares, số lượng và lý do từ chối |
| Portfolio Risk (07) | hard laws, breadth, tail-risk status, ADTV shares và verdict |
| Position Monitoring (09) | vị thế đầu ngày, T+2, stop/exit, lệnh SELL chờ |
| Reinforcement Learning (10) | prior và cỡ mẫu, OOS status, Change Gate thật, policy có được áp dụng hay không |
| System Governance (11) | audit chain, sai phạm, trạng thái kiểm toán replay; không giả báo broker connected |
| Strategy CIO (12) | kết luận và lý do trước/sau phản biện, input tham chiếu |

Đối soát mỗi ngày: số lần gọi từng Agent trong `agent_events` bằng tổng event tương ứng trong phễu; `fills` chỉ xuất phát từ lệnh được duyệt hoặc exit hợp lệ; `days.nav = days.cash + Σ(quantity × giá mark)`; `audit_events` xác minh hash chain. Báo riêng `SUCCESS` kỹ thuật của Agent và quyết định đầu tư `PASS/BLOCK/DEFERRED`, vì hai khái niệm này đã bị trộn trong baseline.

Để kiểm tra đủ 11 Agent khi dữ liệu thật chưa qua Thesis, dùng **một kịch bản tích hợp có fixture công khai**: đầu vào định giá/catalyst có nguồn và timestamp trong fixture, Governance veto và approve ở hai ca, vị thế đủ T+2 để thử SELL. Báo cáo fixture chỉ chứng minh luồng code và phép đối soát, không cộng vào hiệu suất backtest lịch sử. Nhánh dữ liệu thật phải giữ `INSUFFICIENT_DATA` tại Thesis đến khi có nguồn lịch sử phù hợp.
