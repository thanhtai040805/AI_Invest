# SAG Desktop

SAG Desktop dùng Electron chạy workspace Next.js hiện có và quản lý trên máy hai dịch vụ đi kèm trong gói:

- Runtime Web cục bộ Next.js standalone;
- Sidecar FastAPI/Python dạng PyInstaller `onedir`.

Bản desktop mặc định mở toàn bộ panel chính, giao diện sản phẩm và route vẫn lấy từ `apps/web`. Phiên bản đầu không tách cửa sổ tiện ích con, cũng không duy trì bộ frontend thứ hai.

## Phát triển cục bộ

Yêu cầu:

- Node.js 20+;
- Python 3.11;
- Đã cài phụ thuộc của `apps/web`, `apps/api` và `apps/desktop`.

Chuẩn bị lần đầu:

```bash
cd apps/web
npm install

cd ../api
uv sync --extra dev --extra desktop

cd ../desktop
npm install
```

Khởi động Web, API và Electron:

```bash
cd apps/desktop
npm run dev
```

Nếu cổng 3000 hoặc 8000 đã có dịch vụ tương ứng chạy, script dev sẽ tái sử dụng chúng. Khi thoát Electron, các tiến trình con do script tạo ra cũng thoát cùng.

## Tải và cập nhật cho người dùng

Gói cài chính thức được phát hành thống nhất tại [`Zleap-AI/SAG` Releases](https://github.com/Zleap-AI/SAG/releases/latest):

- macOS Apple Silicon: DMG dùng để cài, ZIP với `latest-mac.yml` dùng cho tự động cập nhật;
- Windows x64: NSIS EXE chưa ký để cài, `latest.yml` với blockmap dùng cho tự động cập nhật; Windows có thể hiển thị cảnh báo "nhà phát hành không xác định";
- `SHA256SUMS.txt` dùng để kiểm tra tính toàn vẹn của bản tải.

Client đã cài mặc định theo kênh ổn định `latest` của GitHub Releases. Release phải là bản chính thức không phải bản nháp; bản nháp và pipeline thất bại sẽ không được client phát hiện.

## Phát hành ra public bằng một lệnh

Phát hành chính thức chỉ thực hiện từ thư mục root của public clone độc lập `Zleap-AI/SAG`, trên nhánh `main` sạch và đã merge xong. Clone này không được thêm remote repo nội bộ, cũng không được chứa lịch sử Git nội bộ:

```bash
make release-dry-run VERSION=1.4.0
make release VERSION=1.4.0
```

`scripts/release-public.mjs` sẽ:

1. Kiểm tra nhánh hiện tại, workspace sạch, SemVer ổn định tăng chặt, và các remote fetch/push đều trỏ tới `Zleap-AI/SAG`;
2. Pull và xác nhận local `main` chứa `origin/main`, đồng thời root commit của hai bên giống hệt nhau, chặn lịch sử nội bộ hoặc lịch sử không liên quan vào public repo;
3. Đồng bộ version runtime Desktop/Web/API và lockfile, cập nhật badge README, lưu `Unreleased` thành phiên bản này;
4. Tạo commit `release: vX.Y.Z` và tag chú thích bất biến;
5. Push nguyên tử `main + vX.Y.Z` tới `origin` của public repo. Nếu bất kỳ reference nào push thất bại, cả hai đều không có hiệu lực trên remote.

Tag sau đó kích hoạt `.github/workflows/desktop-release.yml`. Pipeline tái sử dụng toàn bộ cổng CI, build song song trên runner gốc `macos-15` ARM64 và `windows-2025` x64; macOS phải ký và notarize thành công, Windows phải tạo rõ gói cài không ký, và cả hai nền tảng đủ metadata cập nhật cùng file kiểm tra thì mới tạo GitHub Release public.

Script phát hành không build hoặc upload binary cục bộ. Khi push thất bại, commit và tag phát hành cục bộ vẫn giữ lại, sau khi xử lý có thể thử lại; không di chuyển hoặc tái sử dụng tag đã public.

## Môi trường phát hành GitHub

Mở [`Settings → Environments`](https://github.com/Zleap-AI/SAG/settings/environments) của public repo, tạo Environment `desktop-release` trùng tên chính xác. Nếu bật giới hạn Deployment branches and tags, cần đồng thời cho phép `main` (duyệt thủ công) và `v*.*.*` (tag phát hành chính thức); có thể tùy chọn thêm Required reviewers làm cổng phát hành thủ công.

Cấu hình trong **Environment secrets** của Environment đó:

| Secret | Công dụng |
| --- | --- |
| `APPLE_CERTIFICATE_BASE64` | Chứng chỉ Developer ID Application `.p12` chứa private key dạng Base64 một dòng; pipeline ánh xạ thành `CSC_LINK` |
| `APPLE_CERTIFICATE_PASSWORD` | Mật khẩu export `.p12`; pipeline ánh xạ thành `CSC_KEY_PASSWORD` |
| `APPLE_ID` | Email tài khoản Apple Developer, dùng cho notarization |
| `APPLE_APP_SPECIFIC_PASSWORD` | Mật khẩu dành riêng cho App của Apple ID, dùng cho notarization; không phải mật khẩu thường của tài khoản |
| `APPLE_TEAM_ID` | Apple Developer Team ID |

Không cần cấu hình Environment variables thường, cũng không cần tự tạo GitHub PAT; job phát hành dùng `GITHUB_TOKEN` do GitHub tự cung cấp, và chỉ xin `contents: write` ở job phát hành cuối cùng.

- Trong Certificates, Identifiers & Profiles của Apple Developer tạo chứng chỉ **Developer ID Application**, export cùng private key từ keychain máy thành `.p12` có mật khẩu; đây là nguồn của `APPLE_CERTIFICATE_BASE64` và `APPLE_CERTIFICATE_PASSWORD`.
- Trên trang tài khoản Apple ID tạo mật khẩu dành riêng cho App, lưu thành `APPLE_APP_SPECIFIC_PASSWORD`; không đưa mật khẩu thường của Apple ID vào GitHub.
- `APPLE_TEAM_ID` xem trong Apple Developer Membership details.

Trên máy macOS chuyển chứng chỉ thành Base64 một dòng có thể dán vào GitHub Secret:

```bash
openssl base64 -A -in DeveloperIDApplication.p12 | pbcopy
```

Kết quả lưu thành `APPLE_CERTIFICATE_BASE64`; lệnh chỉ ghi vào clipboard, không dán kết quả vào terminal, Issue, PR hoặc log.

`APPLE_SIGNING_IDENTITY` hiện có của bạn chưa cần đưa vào: electron-builder sau khi import `.p12` sẽ tự tìm chứng chỉ Developer ID Application. Identity đầy đủ thường có tiền tố `Developer ID Application:`, ánh xạ trực tiếp thành `CSC_NAME` lại bị builder hiện tại từ chối; chỉ khi `.p12` chứa nhiều chứng chỉ cùng loại mới cần xác nhận qualifier không có tiền tố rồi cấu hình tường minh. `APPLE_PASSWORD` cũng không được pipeline tham chiếu, khuyến nghị xóa khỏi GitHub Secrets; notarization chỉ dùng `APPLE_APP_SPECIFIC_PASSWORD`.

Nếu các Secret này đã cấu hình ở cấp repo **Settings → Secrets and variables → Actions**, tham chiếu vẫn có hiệu lực, không cần tạo lặp. Khi cần cách ly chặt hơn, copy 5 mục trên vào `desktop-release` Environment secrets; Environment này chỉ mở cho macOS release job.

Windows hiện không cấu hình Secret chứng chỉ, pipeline sẽ tắt tự động phát hiện chứng chỉ và kiểm tra installer giữ nguyên không ký. Environment có thể cấu hình required reviewer làm cổng phát hành thủ công. Workflow chỉ xin `contents: write` để tạo Release, thông tin đăng nhập ký macOS không truyền cho CI thường, PR, fork hoặc build task Windows.

Sau khi cấu hình Secrets xong, có thể chọn `main` trong **Actions → Desktop Release → Run workflow** của public repo để duyệt thủ công một lần. Lần đó chạy toàn bộ cổng chất lượng, ký và notarize macOS, build Windows không ký, giữ Artifacts tạm 7 ngày; chạy thủ công không tạo GitHub Release. Chỉ push tag chú thích `vX.Y.Z` mới vào bước phát hành public.

## Build cục bộ và xử lý sự cố

Build phát hành phải thực hiện trên đúng hệ điều hành đích. Sidecar PyInstaller chứa thư viện gốc liên quan đến hệ điều hành và kiến trúc CPU, không thể tạo sidecar Windows phát hành được trên macOS.

macOS Apple Silicon:

```bash
cd apps/desktop
npm run dist:mac
```

Windows x64:

```powershell
cd apps/desktop
npm run dist:win
```

Thứ tự build cố định:

1. Biên dịch Electron main/preload;
2. Build lại Next.js standalone với địa chỉ API desktop;
3. Đóng băng sidecar Python;
4. Ghép Web, API và manifest chạy;
5. Sinh sản phẩm cài đặt; macOS thêm bước ký và notarize, Windows hiện giữ không ký.

Sản phẩm nằm tại `apps/desktop/release/`:

- macOS: DMG dùng để cài, ZIP dùng cho tự động cập nhật;
- Windows: installer NSIS và metadata cập nhật của nó.

Chỉ kiểm tra thư mục ứng dụng mà không sinh installer, chạy:

```bash
npm run package:dir
```

## Cấu hình build và phát hành

| Biến | Giá trị mặc định | Công dụng |
| --- | --- | --- |
| `SAG_DESKTOP_APP_ID` | `ai.zleap.sag` | Định danh duy nhất ứng dụng; sau khi phát hành public lần đầu không tùy tiện sửa đổi |
| `SAG_DESKTOP_API_PORT` | `8000` | Cổng API cục bộ; đồng thời ghi vào build Web và runtime desktop |
| `SAG_DESKTOP_WEB_PORT` | `32100` | Cổng Web cục bộ ưu tiên; khi bị chiếm sẽ tìm cổng khả dụng phía sau |
| `SAG_UPDATE_GITHUB_REPOSITORY` | chưa đặt | Nguồn cập nhật GitHub, dạng `owner/repository`; pipeline chính thức truyền `Zleap-AI/SAG` |
| `SAG_UPDATE_BASE_URL` | chưa đặt | Địa chỉ gốc nguồn cập nhật chung dự phòng; không thể đặt đồng thời với nguồn cập nhật GitHub |
| `SAG_NOTARIZE` | `false` | Đặt `true` khi thực hiện notarization macOS |
| `SAG_DESKTOP_PYTHON` | Python trong `apps/api/.venv` | Trình thông dịch dùng khi build sidecar |
| `SAG_PYTHON_DIST_DIR` | thư mục đóng băng mặc định của API | Tái sử dụng sidecar đã build trong CI |

Thông tin đăng nhập ký macOS chỉ tiêm vào bước ký và notarize cuối của electron-builder, không truyền cho Next.js, PyInstaller hoặc phụ thuộc build của chúng, cũng không ghi vào repo. Windows hiện không tiêm thông tin đăng nhập ký. File gốc icon ứng dụng và sản phẩm từng nền tảng nằm tại `apps/desktop/assets/icon-master.png`, `icon.icns` và `icon.ico`.

`SAG_DESKTOP_API_PORT` thuộc tham số build phát hành, không khuyến nghị để người dùng cuối sửa vì API Base trong Next.js là giá trị thời điểm build. Nếu thật sự sửa đổi, giai đoạn build và chạy phải nhất quán.

## Chạy và thư mục dữ liệu

Client chính thức chỉ lắng nghe loopback:

- Web: cổng động bắt đầu từ `localhost:32100`;
- API/MCP: `127.0.0.1:8000`.

Cơ sở dữ liệu, file upload, dữ liệu engine tri thức và khóa runtime desktop không ghi vào thư mục cài đặt mà ghi vào thư mục `userData` chuẩn của Electron:

- macOS: `~/Library/Application Support/SAG/`
- Windows: `%APPDATA%\SAG\`

Cập nhật ứng dụng không ghi đè thư mục này; trình gỡ cài Windows cũng cấu hình mặc định giữ lại dữ liệu người dùng.

## Ràng buộc cập nhật

Bản desktop dùng version toàn bộ và cập nhật toàn bộ: Electron, Next.js, Python API và phụ thuộc gốc của chúng phát hành với cùng một version trong `apps/desktop/package.json`. Không cập nhật riêng Web hoặc sidecar Python, nếu không không đảm bảo tương thích giao diện và migration dữ liệu.

Public build chính thức dùng GitHub provider, phát hành gói cài, payload cập nhật ZIP/EXE, blockmap, `latest-mac.yml` và `latest.yml` trong cùng một Release không phải bản nháp. electron-builder sinh `app-update.yml` bên trong gói cài, client dựa vào đó phát hiện bản ổn định tiếp theo; không cố định địa chỉ cập nhật vào thư mục tải của một tag nào.

Kịch bản tự host dự phòng có thể đặt `SAG_UPDATE_BASE_URL` dùng generic provider, nhưng phải tự đảm bảo cùng một URL ổn định luôn cung cấp metadata mới nhất và payload tương ứng. Sản phẩm dev/cục bộ không cấu hình provider sẽ không sinh cấu hình cập nhật, cũng không kiểm tra cập nhật.

## Kiểm tra trước phát hành

Tối thiểu hoàn thành:

```bash
npm run typecheck
npm run prepare:release
```

Và trên máy đích sạch xác nhận:

- Lần cài đầu tiên và lần khởi động nguội đầu tiên;
- Trang đăng nhập Web và `/api/v1/system/ready`;
- Import tài liệu, tìm kiếm, hội thoại, chế độ khám phá và MCP;
- Sau khi thoát ứng dụng cả hai dịch vụ cục bộ đều kết thúc;
- Nâng cấp ghi đè giữ lại dữ liệu người dùng;
- Ký và notarization macOS, quy trình cài đặt "nhà phát hành không xác định" Windows, cùng tự động cập nhật của cả hai nền tảng.