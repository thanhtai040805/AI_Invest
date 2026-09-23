FROM nginx:1.27-alpine

# Cài đặt curl để kiểm tra healthcheck
RUN apk add --no-cache curl

# Thay thế file cấu hình mặc định bằng cấu hình AIInvest
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -fsS http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
