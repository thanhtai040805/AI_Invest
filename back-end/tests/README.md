# Phạm vi kiểm tra backend

CI đã build backend trước khi chạy `npm run test:critical`. Lệnh này liệt kê
file cụ thể, không tự phát hiện hoặc chạy toàn bộ test trong repository:

- `shadowFill.test.cjs`: không khớp giả khi thiếu depth hoặc dữ liệu cũ.
- `mlFund.test.cjs`: hợp đồng API quỹ ML, tài khoản và ngày hiệu lực.
- `unit/portfolioAccounting.test.cjs`: công thức kế toán, phí, đối soát NAV,
  giá có thời điểm và lợi suất lịch sử. Các hàm thuần; không gọi DB/network.

Hai file đầu thuộc danh sách critical có trước bản sửa ngày 30/09. Thêm file
mới vào thư mục test không tự đưa nó vào CI. Không lấy coverage hoặc số test
làm mục tiêu; chỉ mở rộng danh sách critical khi có rủi ro cụ thể cần chặn.

`service/portfolioSnapshot.test.cjs` kiểm tra dịch vụ bằng DB/API giả lập,
không phải unit test hàm thuần và không chứng minh transaction trên DB thật.
Nó không chặn CI/deploy. Khi điều tra Portfolio, build một lần rồi chạy
`npm run test:portfolio` để kiểm tra riêng kế toán và dịch vụ snapshot.

Các test cũ ngoài danh sách critical không được tự chạy. Việc rà soát hoặc
xóa chúng cần đối chiếu hợp đồng hiện tại, không mặc định giữ hay chạy chỉ vì
file đã tồn tại. Các lệnh này dùng `dist`; cần build lại sau khi sửa TypeScript.
