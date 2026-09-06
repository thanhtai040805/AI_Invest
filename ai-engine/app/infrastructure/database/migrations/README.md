# Database Schema & Migrations — Single Source of Truth

> **QUY TẮC BẮT BUỘC**:
> Tất cả các bảng, cột, khóa chính, khóa ngoại và chỉ mục (index) của toàn bộ hệ sinh thái AIInvest được quản lý **DUY NHẤT** tại:
> **`back-end/prisma/schema.prisma`**

### Tại sao không dùng migration SQL tại ai-engine?
Trước đây, hệ thống có hiện tượng xung đột 3 nơi (Prisma, `pg_pool.py migrate()`, và các file `migrations/*.sql` tại đây), dẫn tới lệch schema runtime và nguy cơ lỗi khi triển khai Production.

Từ phiên bản hiện tại:
1. Mọi thay đổi về cấu trúc bảng PHẢI được cập nhật vào `back-end/prisma/schema.prisma`.
2. Áp dụng schema qua Prisma (`npx prisma migrate dev` hoặc `npx prisma db push`).
3. `ai-engine` chỉ kết nối vào cơ sở dữ liệu để thực hiện đọc/ghi dữ liệu nghiệp vụ, KHÔNG tự ý thực hiện DDL (`CREATE TABLE`, `ALTER TABLE`) khi runtime.
4. Các file `.sql` trong thư mục này chỉ lưu trữ với mục đích tham khảo lịch sử (historical reference).
