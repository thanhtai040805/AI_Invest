# Nguyên tắc lựa chọn cổ phiếu, cắt lỗ và quản trị danh mục Agents

Tài liệu này mô tả các quy tắc đang có trong mã nguồn, đối chiếu ngày 02/10/2026. Đây là hướng dẫn đọc kết quả của hệ thống cho đội nghiên cứu tài chính và thiết kế UI. Các con số bên dưới là ngưỡng mặc định trong code; không xác nhận cấu hình hoặc danh mục đang chạy trên production. NAV được hiểu là tổng giá trị tài khoản, gồm tiền mặt và cổ phiếu.

## 1. Cách đọc quyết định cuối cùng

Trang Agents ưu tiên quyết định bằng lời và số cổ phiếu được phép giao dịch, đi cùng giá tham chiếu, mục tiêu, thời gian đã phân tích và lý do đầu tư được thẩm định. Tỷ lệ phân bổ trong log rủi ro là giá trị của **lệnh này** trên tổng tài sản; nguồn hiện chưa cung cấp tỷ trọng nắm giữ cuối cùng có thể xác nhận cho cùng lượt. Số cổ phiếu đang nắm giữ được đọc riêng từ snapshot tài khoản hiện tại. Luận điểm và phán quyết phải ghép đúng thesis_id; lượng duyệt phải ghép đúng mã quyết định phân bổ. Không dùng cùng mã cổ phiếu/ngày để tự khẳng định toàn bộ chuỗi đã khớp với nhau.

Kết quả phân bổ, kiểm soát rủi ro và thực thi lệnh phải được đọc cùng nhau. Số lượng do khâu phân bổ đề xuất có thể bị khâu rủi ro giảm hoặc chặn. Số lượng đã được duyệt vẫn cần thanh khoản để khớp. **Đề xuất mua/bán, duyệt mua/bán và đã khớp là các trạng thái khác nhau.** Tỷ trọng mục tiêu cũng có thể cần nhiều phiên mới đạt được.

## 2. Nguyên tắc lựa chọn cổ phiếu của Multi-Agent

Hệ thống đi qua các điều kiện sau trước khi xem xét cấp vốn:

1. **Được phép giao dịch và có hồ sơ tài chính phù hợp.** Cổ phiếu không bị tạm ngừng hoặc đình chỉ; ý kiến kiểm toán phải là chấp nhận toàn phần. Bộ lọc dữ liệu kế toán loại mã thiếu dữ liệu cần thiết hoặc có chỉ dấu cảnh báo vượt ngưỡng. Nhóm tài chính có cơ chế miễn trừ theo bộ lọc riêng; việc vượt qua một bộ lọc không chứng minh doanh nghiệp hoàn toàn không có rủi ro kế toán.
2. **Có thanh khoản để mua và thoát vị thế.** Giá trị giao dịch trung bình 20 phiên tối thiểu 15 tỷ đồng. Code đang miễn ngưỡng này cho VN30. Chiến lược Quant còn yêu cầu lịch sử niêm yết khoảng 12 tháng, cũng có ngoại lệ VN30.
3. **Có nghiên cứu đủ cơ sở.** Điểm tổng hợp nghiên cứu tối thiểu 60/100 và mức đánh giá thuộc A+, A hoặc B. Phân tích xét định giá, chất lượng tài chính, tăng trưởng lợi nhuận, động lượng giá, dòng tiền và kỹ thuật. Các trọng số có thể đổi theo trạng thái thị trường và chính sách đã lưu.
4. **Có điều kiện hỗ trợ luận điểm.** Code kiểm tra ba nhóm: cơ bản/định giá/lợi nhuận; dòng tiền hoặc động lượng; bối cảnh thị trường. Trong bối cảnh giảm mạnh hoặc khủng hoảng, luận điểm thường không được thông qua; một ngoại lệ trong engine luận điểm dành cho chất lượng doanh nghiệp và động lượng đều đạt 90/100 trở lên. Pipeline hằng ngày có thể chặn mua sớm hơn ngoại lệ này.
5. **Giá mục tiêu có đủ cơ sở và khoảng tăng kỳ vọng.** Khâu luận điểm yêu cầu giá mục tiêu cơ sở cao hơn giá hiện tại ít nhất 15%. Đây là điều kiện xét luận điểm dựa trên định giá của hệ thống, không bảo đảm giá sẽ tăng 15%. Thiếu dữ liệu định giá phù hợp thì chờ hoặc bỏ qua giao dịch.
6. **Đã qua phản biện và còn đủ điều kiện cấp vốn.** Phán quyết chặn hoặc điểm phản biện từ 70 trở lên sẽ không được khâu eligibility thông qua. Phản biện có điều kiện còn có thể dẫn tới hạ quy mô hoặc áp điều kiện ở CIO.
7. **Danh mục và thị trường cho phép giải ngân.** Mã đạt nghiên cứu vẫn có thể chưa được mua nếu thiếu tiền mặt, vượt giới hạn một mã/ngành, thanh khoản không đủ, chưa có dữ liệu rủi ro thị trường hoặc đang ở trạng thái phòng thủ.

Trên UI, thay nhãn “3 signals” bằng câu có nội dung cụ thể, chẳng hạn: “Định giá và lợi nhuận đáp ứng điều kiện nghiên cứu; dòng tiền hoặc động lượng hỗ trợ; bối cảnh thị trường chưa ở trạng thái giảm mạnh.” Câu này chỉ được viết khi từng điều kiện tương ứng có bằng chứng trong dữ liệu.

**Điểm cần giữ chính xác:** nhóm xác nhận thứ hai hiện kiểm tra dòng tiền hoặc động lượng, không có một điều kiện riêng bắt buộc “đạt đúng điểm mua kỹ thuật”. Điểm kỹ thuật có tham gia nghiên cứu nhưng vượt qua ba nhóm xác nhận chưa đủ để kết luận giá đang ở điểm mua. Nội dung “doanh nghiệp tốt”, “vĩ mô hỗ trợ”, “đã đạt điểm mua” phải được giải thích riêng dựa trên kết quả thật, không tự suy ra từ nhãn PASS.

## 3. Nguyên tắc phân bổ và quản trị rủi ro Multi-Agent

| Nội dung | Mặc định trong code | Cách diễn giải cho người đọc |
|---|---|---|
| Tập trung một cổ phiếu | Tối đa 15% NAV | Một mã không được chiếm quá lớn trong tài khoản; CIO có thể áp trần thấp hơn, chẳng hạn 8%. |
| Tập trung một ngành | Tối đa 35% NAV | Tránh nhiều mã cùng phụ thuộc một câu chuyện ngành. |
| Rủi ro trước khi mua | Tối đa 2% NAV cho lệnh mua được xét | Tính cả tình huống giá giảm mạnh trong thời gian cổ phiếu chưa về; không chỉ dựa vào giá cắt lỗ dự kiến. |
| Cổ phiếu chưa về | Tối đa 35% NAV | Giữ khả năng xoay xở vì lượng này chưa thể bán ngay. |
| Quy mô giao dịch trong phiên | Tối đa 15% khối lượng giao dịch trung bình 20 phiên | Lệnh lớn cần chia phiên để hạn chế ảnh hưởng giá. |
| Quy mô vị thế tích lũy | Tối đa 25% khối lượng giao dịch trung bình 20 phiên | Khống chế lượng nắm giữ để còn khả năng thoát. |
| Số lượng giao dịch | Làm tròn theo lô 100 cổ phiếu ở khâu phân bổ | Tiền và tỷ trọng cuối cùng phụ thuộc giá, số lượng khả dụng và lô giao dịch. |

Ngưỡng 2% NAV trước khi mua dùng tình huống hai phiên giảm sàn liên tiếp của HOSE, tương đương giảm 13,51%, hoặc mức giảm đến giá cắt lỗ nếu lớn hơn. Đây là cách hệ thống giảm quy mô trước khi mua; không phải cam kết tổng thiệt hại thực tế luôn nằm trong 2%. Riêng kiểm tra này xét giá trị lệnh mua đang đề xuất; không cộng toàn bộ rủi ro của các lần mua trước vào cùng một mã.

Quy mô cấp vốn còn phụ thuộc độ tin cậy của nghiên cứu, dữ liệu hiệu quả chiến lược, bối cảnh thị trường và danh mục hiện hữu. Khi cổ phiếu có xu hướng biến động cùng nhiều mã đã giữ, khâu xây dựng danh mục có thể giảm 25% tỷ trọng đề xuất nếu được cung cấp dữ liệu tương quan. Khi thiếu dữ liệu hiệu chuẩn hiệu quả chiến lược, hệ thống dùng giả định mặc định và gắn cảnh báo; không nên hiển thị các giả định đó như xác suất thắng đã được kiểm chứng.

Mức tiền mặt cơ sở ở engine phân bổ là **10% khi thị trường thuận lợi, 30% khi giằng co, 60% khi giảm**. Engine này trả về khoảng số mã mục tiêu lần lượt **12–18, 8–12 và 4–6 mã**; khoảng này là thông tin định hướng, chưa phải một giới hạn số mã được kiểm tra đầy đủ trong chính hàm phân bổ. Khâu rủi ro hoặc CIO có thể yêu cầu giữ tiền mặt cao hơn.

Danh mục giảm từ đỉnh sẽ chuyển sang các mức phòng thủ:

| Tài khoản giảm từ đỉnh | Hành vi mặc định của engine rủi ro |
|---|---|
| Từ 3% | Cảnh giác, tiền mặt tối thiểu 15%, quy mô lệnh được xét còn 90%. |
| Từ 5% | Tiền mặt tối thiểu 25%, quy mô lệnh được xét còn 75%. |
| Từ 10% | Tiền mặt tối thiểu 50%, quy mô lệnh được xét còn 50%. |
| Từ 15% | Tiền mặt tối thiểu 75%, chặn lệnh ở cổng rủi ro; engine phân bổ đóng băng mở vị thế mới. |

Các tỷ lệ này là mức đề xuất và giới hạn ở những khâu tương ứng, không xác nhận tài khoản đã bán đủ để đạt tỷ lệ tiền mặt. Khi phục hồi xuống dưới mức cảnh giác, engine yêu cầu ít nhất hai phiên quan sát, rủi ro thị trường an toàn và độ rộng thị trường khỏe trước khi trả lại đầy đủ hạn mức. Trong Agent Risk, số phiên quan sát mặc định đang là 2 nếu caller không truyền; vì vậy không được diễn giải mặc định đó như bằng chứng đã quan sát đủ hai phiên thật.

Độ rộng và áp lực phân phối cũng có thể siết quy mô. Ba phiên phân phối trong 20 phiên hoặc độ rộng dưới 40% dẫn tới đề xuất tối thiểu 25% tiền mặt. Bốn phiên phân phối, hoặc phân kỳ mạnh giữa chỉ số và phần lớn cổ phiếu, dẫn tới tối thiểu 40% tiền mặt và giảm nửa quy mô lệnh mới. Từ năm phiên phân phối, khâu rủi ro chặn mua và đề xuất tối thiểu 60% tiền mặt. Suy giảm chất lượng mô hình hoặc trượt giá bất thường có một cơ chế giảm quy mô/dừng mua riêng. Khi nhiều yêu cầu cùng xuất hiện, khâu rủi ro chọn mức tiền mặt cao nhất và mức giảm quy mô nghiêm ngặt nhất.

## 4. Nguyên tắc cắt lỗ và bảo vệ lợi nhuận Multi-Agent

Engine giám sát kiểm tra khả năng bán trước, sau đó ưu tiên các điều kiện dưới đây theo thứ tự. Việc kích hoạt điều kiện sinh đề xuất thoát hoặc giảm vị thế; số thực sự khớp còn phụ thuộc cổng kiểm soát, cổ phiếu khả dụng và bên mua trên thị trường.

| Điều kiện | Hành động được engine đề xuất |
|---|---|
| Lỗ của một vị thế làm mất từ 2% NAV | Bán toàn bộ lượng có thể bán. Đây là 2% giá trị tài khoản, không phải cổ phiếu giảm 2% từ giá mua. |
| Vị thế từng lãi ít nhất 10%, sau đó mất ít nhất 35% phần lợi nhuận cao nhất | Bán toàn bộ lượng khả dụng để giữ thành quả. Ví dụ từng lãi 20%, sau đó chỉ còn lãi 13% thì đã chạm điều kiện này. |
| Giá hiện tại xuống dưới đáy hỗ trợ được truyền vào | Đề xuất bán toàn bộ lượng khả dụng vì cấu trúc giá đã yếu. Code dùng giá hiện tại trong lần kiểm tra, chưa có điều kiện bắt buộc đợi đóng cửa. |
| Nến có râu trên dài hơn nửa biên độ và khối lượng cao hơn 1,5 lần trung bình 20 phiên | Đề xuất giảm khoảng một nửa lượng khả dụng để phòng dấu hiệu phân phối. |
| Giữ quá nửa thời gian kỳ vọng mà hiệu suất từ âm 3% đến dưới dương 2% | Đề xuất giảm khoảng một nửa vị thế vì vốn chưa tạo hiệu quả. Khi không được truyền thời gian kỳ vọng, engine dùng 90 ngày. |

**Giá cắt lỗ trong luận điểm và điều kiện bán đang có một khoảng cách triển khai.** Khâu luận điểm mặc định ghi giá cắt lỗ thấp hơn giá tham chiếu 7%; khâu rủi ro dùng giá này hoặc mức mặc định tương tự để xét mua. Engine giám sát hiện không đọc trực tiếp trường giá cắt lỗ đó. Nếu tự nạp vị thế từ cơ sở dữ liệu mà thiếu đáy kỹ thuật, Agent Monitoring lại dùng một mốc thay thế thấp hơn giá vốn 5%. Do đó UI phải dùng đúng giá có nguồn và ghi rõ điều kiện; không thể khẳng định mọi vị thế đều tự bán chính xác ở mức âm 7%.

Điều kiện theo thời gian có mức khẩn cấp MEDIUM. Cơ chế tự dispatch trong Agent Monitoring chỉ đẩy những lệnh EMERGENCY hoặc HIGH. Vì vậy time stop hiện có thể xuất hiện dưới dạng đề xuất mà chưa tự chuyển sang thực thi.

Giám sát luận điểm chỉ phản ứng khi được caller truyền một sự kiện luận điểm bị phá vỡ. Trước 14:00, agent chuyển cảnh báo cần xử lý danh mục. Từ 14:00, nếu chưa có phản hồi/phê duyệt xử lý, agent đề xuất bán lượng khả dụng. Chưa có bằng chứng từ luồng này rằng hệ thống tự đọc đủ mọi sự kiện doanh nghiệp, vĩ mô và kỹ thuật để phát hiện sự kiện; tên “tự vô hiệu hóa” dễ tạo kỳ vọng quá mức.

## 5. Theo dõi danh mục hiện có và rủi ro cần quan sát

Giữ vị thế khi luận điểm còn cơ sở, danh mục vẫn phù hợp và chưa có điều kiện thoát. Mua thêm khi mục tiêu được nâng, còn tiền để duy trì đệm an toàn, còn sức chứa ngành/mã và khối lượng giao dịch cho phép. Giảm hoặc bán khi mục tiêu hạ hoặc điều kiện phòng vệ phát sinh. Khâu tái cân bằng mặc định bỏ qua chênh lệch tỷ trọng dưới **2 điểm phần trăm NAV** để hạn chế giao dịch vụn và phí; mua mới và bán hết là hai ngoại lệ. Đây là chênh lệch tỷ trọng tài khoản, không phải giá cổ phiếu biến động 2%.

Khi cần bán nhưng còn hàng chưa về, khâu tái cân bằng chỉ cho phép bán lượng khả dụng. Nếu dưới 100 cổ phiếu khả dụng, nó trả trạng thái chờ thanh toán. Khi thanh khoản chưa đủ để đạt mục tiêu trong một phiên, có cơ chế lưu chiến dịch tích lũy hoặc giảm vị thế để xử lý tiếp. Quyết định “Nắm giữ” vì chờ cổ phiếu về cần kèm lý do, tránh bị hiểu là luận điểm vẫn tích cực.

“Rủi ro cần quan sát” nên được viết thành các sự kiện có thể kiểm tra:

| Nhóm theo dõi | Nội dung cần giải thích cho người đọc | Khi dữ liệu xác nhận bất lợi |
|---|---|---|
| Câu chuyện doanh nghiệp | Kết quả kinh doanh so với giả định; biên lợi nhuận; chất lượng báo cáo; tiến độ dự án hoặc yếu tố tăng trưởng đã nêu trong luận điểm. Mẫu mặc định hiện đề cập biên lợi nhuận giảm hơn 2 điểm phần trăm, chỉ dấu kế toán cảnh báo và catalyst chậm 1–2 quý. | Cập nhật lại luận điểm và mục tiêu; có thể chuyển thành sự kiện yêu cầu đánh giá/thoát. Không coi một mẫu văn bản là sự kiện đã xảy ra. |
| Vĩ mô và thị trường | Thanh khoản thị trường, lãi suất/tỷ giá liên quan đến doanh nghiệp, nhu cầu ngành, độ rộng và các phiên phân phối. | Khâu rủi ro có thể tăng tiền mặt, giảm quy mô hoặc chặn mua khi nhận được dữ liệu tương ứng. |
| Phân tích kỹ thuật | Giá so với hỗ trợ; tín hiệu phân phối; đỉnh lợi nhuận và phần lợi nhuận đã mất. Mẫu luận điểm còn có điều kiện mất đường giá trung bình 50 phiên kèm khối lượng hơn hai lần bình quân 20 phiên. | Engine giám sát có thể sinh đề xuất bán/giảm khi được cấp đúng giá, nến, hỗ trợ và đỉnh giá. Điều kiện trong văn bản không đồng nghĩa đã có detector tự động cho điều kiện đó. |

Phần này cần nêu **quan sát điều gì, mức nào/sự kiện nào đáng chú ý, và quyết định sẽ được xem lại ra sao**. Kết quả phản biện có thể được ẩn khỏi giao diện chính, nhưng các giới hạn và điều kiện quan sát còn ảnh hưởng đến quyết định cuối cùng vẫn phải được phản ánh.

Hệ thống hiện dùng thời gian luận điểm bằng số tháng, có các cách định giá cho khoảng đến ba tháng và trên ba tháng. Chưa có ba bộ chính sách riêng được triển khai đầy đủ cho “lướt sóng”, “trung hạn” và “dài hạn”. Có thể tổ chức báo cáo theo thời gian được trả về, nhưng không nên tự tạo mục tiêu hoặc quy tắc cắt lỗ mới chỉ vì đổi nhãn kỳ hạn.

## 6. Kênh Standalone ML có bộ quy tắc riêng

Standalone ML xếp hạng ứng viên bằng mô hình và tạo lệnh cho tài khoản riêng. Nó không đi qua toàn bộ chuỗi nghiên cứu, luận điểm, phản biện, phân bổ và risk gate của Multi-Agent. Hệ thống lưu các dự báo và chỉ xem xét số ứng viên hàng đầu theo giới hạn do caller truyền; dữ liệu đặc trưng phải có trước phiên quyết định. Mã đã giữ hoặc đã có lệnh chờ sẽ không được mua lặp lại trong chu kỳ này.

| Nội dung | Hành vi của Standalone ML đang vận hành trong service |
|---|---|
| Quy mô nhóm cao nhất | 12% NAV cho ứng viên đạt ngưỡng xếp hạng A+. |
| Quy mô nhóm tiếp theo | 5% NAV cho ứng viên đạt ngưỡng xếp hạng A. |
| Nhóm dưới các ngưỡng trên | Dùng cấu hình tỷ trọng riêng; mặc định constructor là 20% NAV. Ngưỡng lọc có thể được caller hoặc biến môi trường áp thêm. |
| Bảo vệ hòa vốn | Khi từng lãi từ 2,5%, nếu giảm về lãi 0,2% hoặc thấp hơn thì xếp lệnh bán toàn bộ. |
| Cắt lỗ | Khi giảm từ giá vốn 3,5% thì xếp lệnh bán toàn bộ. |
| Chốt lời | Khi lãi từ 6% thì xếp lệnh bán toàn bộ. |
| Thời gian nắm giữ | Từ 5 ngày theo lịch thì xếp lệnh bán toàn bộ; phép đếm này không phải 5 phiên giao dịch. |
| Điều kiện trước khi bán | Phải có ít nhất 100 cổ phiếu, đã hết khóa thanh toán và có dữ liệu sổ lệnh mới, đủ điều kiện thực thi. |

Monitor ML áp các điều kiện bán trên cho tất cả tier trong service; nó chưa phân tách A+ để giữ vị thế chạy dài và A để chốt ở 6%. File `dual_tier_sniper_engine.py` có mô tả chiến lược khác: A+ giữ theo xu hướng, A cắt ở 3% và chốt 6%, lọc thanh khoản 10 tỷ đồng và đổi nhóm theo xu hướng chỉ số. Các lệnh gọi engine này xuất hiện trong scripts/experiments; service Standalone đang import nhưng chưa gọi trực tiếp bộ lọc đó. Không dùng mô tả thí nghiệm làm luật đã được áp dụng mặc định cho tài khoản ML.

Standalone không kiểm tra đầy đủ các trần 15% một mã, 35% một ngành và đệm tiền mặt như Multi-Agent trong hàm xếp lệnh này. Số lượng mua bị giới hạn bởi tiền mặt còn lại, đã trừ phần dự phòng cho lệnh chờ và phí. Các mức 12%, 5% và tỷ trọng cấu hình là mục tiêu cho lệnh đang xét, không phải bằng chứng đã qua risk gate Multi-Agent.

## 7. Giới hạn thực thi cần phản ánh đúng trên UI

- **Chỉ ghi đã khớp khi có xác nhận thực thi.** Trade Execution đang dùng khớp mô phỏng từ sổ lệnh thật. Sổ lệnh phải nằm trong phiên khớp liên tục, cập nhật trong 10 giây và có đủ lượng ở mức giá cho phép. Thiếu bên mua, giá chưa tới hoặc dữ liệu cũ thì chưa khớp; lệnh bán khẩn cấp cũng không được tạo giá khớp giả. Cơ chế Shadow hiện yêu cầu đủ khối lượng lệnh, không mô phỏng khớp một phần từ độ sâu thiếu.
- **Chưa có đường đặt lệnh broker thật trong Standalone ML.** Chọn LIVE sẽ bị service từ chối. Từ “thực thi” trên báo cáo hiện cần đi kèm trạng thái mô phỏng/Shadow khi đó là nguồn thực tế.
- **Chờ thanh toán không thể bị bỏ qua để cắt lỗ.** Repository AI khóa T và T+1, mở khóa từ 11:30 ngày T+2 theo bộ đếm ngày làm việc. Hàm này bỏ cuối tuần nhưng chưa loại ngày nghỉ lễ; không nên trình bày như một bộ lịch thanh toán hoàn chỉnh. Giao dịch còn cần phiên thị trường phù hợp sau khi mở khóa.
- **Kho dữ liệu chưa quản lý từng lô mua riêng trong vị thế.** Khi mua thêm, repository AI cập nhật thời điểm mở của cả vị thế; lượng cũ có thể bị xem là khóa cùng lượng mới. Đây là giới hạn của cách lưu vị thế hiện tại, ảnh hưởng số khả dụng báo cáo.
- **“100% tiền mặt” trong trạng thái phòng thủ là mục tiêu/chặn mua.** Pipeline hằng ngày dừng mua sớm khi thị trường giảm hoặc khủng hoảng và quét giám sát vị thế; nó không tự bán mọi vị thế chỉ để đạt 100% tiền mặt. UI không được coi trạng thái này là tiền mặt thực tế đã đạt 100%.
- **Luồng hằng ngày chưa truyền đầy đủ danh mục cho mọi khâu.** Ngoài replay, pipeline truyền tiền mặt bằng NAV và danh mục rỗng vào khâu phân bổ/rủi ro, rồi đặt chiều thực thi là BUY. Các engine độc lập có khả năng tái cân bằng vị thế hiện hữu, nhưng không thể khẳng định pipeline này đã áp đầy đủ việc quản trị danh mục hiện có hoặc tự bán theo mọi đề xuất phân bổ.
- **Lệnh thủ công ở back-end dùng một luồng khác.** Nó kiểm tra sổ lệnh, tiền và tổng số cổ phiếu, ghi giao dịch Shadow; chưa gọi các risk gate Multi-Agent hoặc kiểm tra khóa T+2 trong hàm `placeOrder`. Vì vậy không tuyên bố mọi đường đặt lệnh trong ứng dụng đều cùng tuân thủ tất cả quy tắc trên.
- **Dữ liệu SAG đang có quy tắc đóng luồng.** Kết quả SAG chưa được dùng làm bằng chứng production trong quy trình thông thường; trạng thái đóng không được diễn giải thành doanh nghiệp kém hoặc không đạt điều kiện tài chính.

Các tài liệu và chuỗi lý do trong code có vài con số cũ: 20% thanh khoản một phiên so với mức tính toán hiện là 15%; kịch bản 19,6% so với mức hai phiên sàn 13,51% trong engine; điểm luận điểm 65 so với điều kiện thực tế 60. Tài liệu này ưu tiên điều kiện được thực thi. Trần 15%/35%/2% cũng cần đọc theo nơi áp dụng: Agent Risk có thể nạp `risk_limits` từ cơ sở dữ liệu, còn Compliance và StopLossEngine vẫn dùng một số ngưỡng mặc định trong code. Việc đổi một cấu hình không bảo đảm tất cả khâu đã đổi đồng bộ.

## 8. Phụ lục nguồn trong repository

Các mốc dưới đây là vị trí đối chiếu mã nguồn; số dòng có thể dịch chuyển khi file được cập nhật. Đường dẫn tính từ gốc repository.

| Nội dung | Nguồn và dòng bắt đầu |
|---|---|
| Trạng thái giao dịch, kiểm toán, thanh khoản 15 tỷ, ngoại lệ VN30, lịch sử niêm yết | `ai-engine/app/domain/agents/universe_discovery.py:259`, `:276`, `:292`, `:310` |
| Dữ liệu kế toán thiếu/cảnh báo, phân nhóm và phòng thủ ngoài Group A | `ai-engine/app/domain/agents/universe_discovery.py:334`, `:364`, `:380`, `:398` |
| Ngưỡng nghiên cứu thực tế | `ai-engine/app/domain/agents/equity_research.py:248` |
| Điều kiện xác nhận và ngoại lệ doanh nghiệp | `ai-engine/app/domain/rules/thesis_engine.py:185`, `:197`, `:212` |
| Luận điểm, định giá tối thiểu 15%, giá cắt lỗ và điều kiện quan sát | `ai-engine/app/domain/rules/thesis_engine.py:169`, `:257`, `:325` |
| Phản biện trong khâu eligibility | `ai-engine/app/domain/rules/portfolio/eligibility_engine.py:49`, `:84` |
| Trần rủi ro, hai phiên sàn, thanh khoản, mã và ngành | `ai-engine/app/domain/rules/hard_laws.py:47`, `:52`, `:71`, `:95` |
| Khóa hàng tối đa 35% và sức chứa theo rủi ro | `ai-engine/app/domain/rules/risk/t25_exposure_manager.py:30`, `:72`, `:89` |
| Trần CIO, ngành, giảm tỷ trọng do tương quan | `ai-engine/app/domain/rules/portfolio/construction_engine.py:63`, `:69`, `:95` |
| Giả định hiệu quả chiến lược khi chưa hiệu chuẩn | `ai-engine/app/domain/rules/portfolio/probability_engine.py:32`, `:89` |
| Quy mô thay đổi theo thị trường | `ai-engine/app/domain/rules/portfolio/kelly_engine.py:67` |
| Tiền mặt, khoảng số mã, trần giải ngân và đóng băng mở mới | `ai-engine/app/domain/rules/portfolio/dynamic_allocation_engine.py:55`, `:69`, `:103`, `:122` |
| Thanh khoản, chia phiên, lô 100 và chuỗi lý do 20% cũ | `ai-engine/app/domain/rules/portfolio/liquidity_engine.py:36`, `:62`, `:79` |
| Tái cân bằng, chờ thanh toán và chiến dịch nhiều phiên | `ai-engine/app/domain/rules/portfolio/rebalancing_engine.py:71`, `:114`, `:152` |
| Các mức giảm từ đỉnh và điều kiện hồi phục | `ai-engine/app/domain/rules/risk/drawdown_recovery_protocol.py:64`, `:118` |
| Phòng thủ theo độ rộng/phân phối và chất lượng mô hình | `ai-engine/app/domain/rules/risk/breadth_risk_engine.py:83`, `ai-engine/app/domain/rules/risk/cdc_controller.py:59` |
| Hạn mức động, mặc định số phiên quan sát, quyết định chặn/giảm và tiền mặt | `ai-engine/app/domain/agents/portfolio_risk.py:79`, `:254`, `:449`, `:467`, `:498` |
| Thứ tự bảo vệ, khóa hàng, trailing, kỹ thuật và time stop | `ai-engine/app/domain/rules/stop_loss.py:105`, `:124`, `:146`, `:167`, `:191`, `:212`, `:234` |
| Mốc hỗ trợ thay thế 5%, sự kiện luận điểm và điều kiện tự dispatch | `ai-engine/app/domain/agents/position_monitoring.py:152`, `:330`, `:411` |
| Số lượng cần kiểm tra ở cổng cuối và cho phép bán phòng vệ qua failsafe | `ai-engine/app/domain/rules/governance/compliance_engine.py:143`, `:155`, `:179`; `ai-engine/app/domain/agents/system_governance.py:190` |
| Khớp mô phỏng và lệnh chưa khớp | `ai-engine/app/domain/rules/execution/shadow_fill.py:9`; `ai-engine/app/domain/agents/trade_execution.py:395` |
| Khóa T+2, cập nhật thời điểm cả vị thế khi mua thêm | `ai-engine/app/domain/repositories/portfolio_repository.py:33`, `:752` |
| Luồng thủ công back-end | `back-end/src/services/portfolio.service.ts:119`, `:156`, `:171`, `:179` |
| Pipeline phòng thủ, portfolio rỗng ngoài replay, BUY và các nhãn 7%/15% | `ai-engine/app/domain/pipeline/daily_pipeline_orchestrator.py:233`, `:512`, `:548`, `:580`, `:608` |
| ML từ chối LIVE, dữ liệu trước ngày quyết định, xếp hạng, tỷ trọng và hàng chờ | `ai-engine/app/domain/services/ml/standalone_ml_channel.py:281`, `:340`, `:395`, `:398`, `:412` |
| Bốn điều kiện bán đang dùng ở monitor ML | `ai-engine/app/domain/services/ml/standalone_ml_channel.py:492`, `:523` |
| Chiến lược Dual Tier riêng | `ai-engine/app/domain/services/ml/dual_tier_sniper_engine.py:48`, `:75`, `:112` |
| Quy tắc SAG đang đóng | `.agents/rules/06-sag-analysis-hold.md:1` |
